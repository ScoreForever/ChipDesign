"""INT4 quantization-aware training for the programmable NPU.

A network is a list of layer specs. The same spec drives three forms:
  * float training with BatchNorm,
  * QAT with BatchNorm folded and every tensor fake-quantized exactly where
    the hardware quantizes (INT4 weights per output channel, INT4 activations
    per tensor, INT32 bias, window sums for AvgPool, integer-weighted Add),
  * export to an npu_program model (JSON-compatible dict).

Spec entries:
  conv   out, k=[kh,kw], s=[sh,sw], p=[t,l,b,r], dw=False, relu=True, pool=None|'max2'|'max1x2'
  pool   kind='max'|'avg', k, s            (standalone; avg is followed by a Requant)
  gap                                      (global average pool + Requant)
  fc     out, relu=False                   (final fc has no BN and yields INT32 logits)
  save   name                              (remember the current tensor)
  add    name, relu=True                   (residual: current + saved)
"""
from __future__ import annotations
import math
import torch
import torch.nn as nn
import torch.nn.functional as F


# Largest code after ReLU: 15 uses the unsigned u4 tensors of ABI 4 (default);
# 7 keeps post-ReLU tensors in signed INT4 (the ABI 3 behaviour, for comparison).
RELU_MAX = 15


def grad_scale(x, scale):
    return (x - x * scale).detach() + x * scale


def round_ste(x):
    return (x.round() - x).detach() + x


class ActQuant(nn.Module):
    """LSQ per-tensor INT4 activation quantizer with a fixed clamp range."""

    def __init__(self, lo=-8, hi=7):
        super().__init__()
        self.lo, self.hi = lo, hi
        self.scale = nn.Parameter(torch.tensor(1.0))
        self.register_buffer('initialized', torch.tensor(False))

    def forward(self, x):
        if not self.initialized:
            with torch.no_grad():
                # MSE-optimal clipping over a grid of candidate ranges.
                v = x.detach().flatten()[:400000].float()
                top = v.abs().max().clamp(min=1e-6)
                best, best_err = None, None
                for frac in torch.linspace(0.05, 1.0, 96):
                    s = top * frac / self.hi
                    err = ((torch.clamp(torch.round(v / s), self.lo, self.hi) * s - v) ** 2).mean()
                    if best_err is None or err < best_err:
                        best, best_err = s, err
                self.scale.copy_(best)
                self.initialized.fill_(True)
        g = 1.0 / math.sqrt(x.numel() / x.shape[0] * self.hi)
        s = grad_scale(self.scale.abs() + 1e-8, g)
        return torch.clamp(round_ste(x / s), self.lo, self.hi) * s


def weight_quant(w, scale):
    """Per-output-channel INT4 [-8,7] fake quantization (LSQ scale)."""
    g = 1.0 / math.sqrt(w.numel() / scale.numel() * 7)
    s = grad_scale(scale.abs() + 1e-8, g).view(-1, *([1] * (w.dim() - 1)))
    return torch.clamp(round_ste(w / s), -8, 7) * s


def int_scale(weight):
    """Initial per-channel scale: map the largest magnitude near 7."""
    return weight.detach().abs().flatten(1).amax(1).clamp(min=1e-6) / 7


class Layer(nn.Module):
    def __init__(self, spec, cin):
        super().__init__()
        self.spec = dict(spec)
        t = spec['type']
        self.cin = cin
        if t == 'conv':
            kh, kw = spec.get('k', [3, 3])
            self.dw = spec.get('dw', False)
            cout = cin if self.dw else spec['out']
            self.conv = nn.Conv2d(cin, cout, (kh, kw), tuple(spec.get('s', [1, 1])), 0,
                                  groups=cin if self.dw else 1, bias=True)
            self.bn = nn.BatchNorm2d(cout)
            self.cout = cout
        elif t == 'fc':
            self.fc = nn.Linear(cin, spec['out'])
            self.last = spec.get('last', False)
            self.bn = None if self.last else nn.BatchNorm1d(spec['out'])
            self.cout = spec['out']
        else:
            self.cout = cin
        # Output activation quantizer (QAT). ReLU outputs use [0,7].
        relu = spec.get('relu', t == 'conv' or t == 'add')
        # Averages of post-ReLU tensors are non-negative: quantize them like ReLU outputs.
        if t == 'gap' or (t == 'pool' and spec.get('kind') == 'avg'):
            relu = spec.get('relu', True)
        if t == 'fc' and spec.get('last', False):
            self.aq = None
        elif t in ('conv', 'fc', 'gap', 'add') or (t == 'pool' and spec.get('kind') == 'avg'):
            self.aq = ActQuant(0 if relu else -8, RELU_MAX if relu else 7)
        else:
            self.aq = None
        self.relu = relu
        self.wscale = None

    def fold(self):
        """Fold BatchNorm into weights/bias and initialize the weight scales."""
        t = self.spec['type']
        if t in ('conv', 'fc') and self.bn is not None:
            mod = self.conv if t == 'conv' else self.fc
            bn = self.bn
            std = torch.sqrt(bn.running_var + bn.eps)
            gain = bn.weight / std
            with torch.no_grad():
                mod.weight.mul_(gain.view(-1, *([1] * (mod.weight.dim() - 1))))
                mod.bias.copy_((mod.bias - bn.running_mean) * gain + bn.bias)
            self.bn = None
        if t in ('conv', 'fc'):
            mod = self.conv if t == 'conv' else self.fc
            scale = int_scale(mod.weight)
            # The final FC emits INT32 logits compared by argmax, so all its
            # rows must share one scale; elsewhere Requant absorbs per-row scales.
            if t == 'fc' and self.last:
                scale = scale.max().view(1)
            self.wscale = nn.Parameter(scale)

    def forward(self, x, qat, saved):
        t = self.spec['type']
        if t == 'conv':
            p = self.spec.get('p', [0, 0, 0, 0])
            x = F.pad(x, (p[1], p[3], p[0], p[2]))
            w = weight_quant(self.conv.weight, self.wscale) if qat else self.conv.weight
            x = F.conv2d(x, w, self.conv.bias, self.conv.stride, 0, 1, self.conv.groups)
            if self.bn is not None:
                x = self.bn(x)
            if self.relu:
                x = F.relu(x)
            pool = self.spec.get('pool')
            if pool == 'max2':
                x = F.max_pool2d(x, 2, 2)
            elif pool == 'max1x2':
                x = F.max_pool2d(x, (1, 2), (1, 2))
        elif t == 'pool':
            k, s = tuple(self.spec['k']), tuple(self.spec.get('s', self.spec['k']))
            x = F.max_pool2d(x, k, s) if self.spec['kind'] == 'max' else F.avg_pool2d(x, k, s)
        elif t == 'gap':
            x = x.mean((2, 3), keepdim=True)
        elif t == 'fc':
            x = x.flatten(1)
            w = weight_quant(self.fc.weight, self.wscale) if qat else self.fc.weight
            x = F.linear(x, w, self.fc.bias)
            if self.bn is not None:
                x = self.bn(x)
            if self.relu:
                x = F.relu(x)
        elif t == 'save':
            saved[self.spec['name']] = x
            return x
        elif t == 'add':
            x = x + saved[self.spec['name']]
            if self.relu:
                x = F.relu(x)
        if qat and self.aq is not None:
            x = self.aq(x)
        return x


class Net(nn.Module):
    def __init__(self, spec, input_shape):
        """input_shape: (H, W, C)."""
        super().__init__()
        self.input_shape = tuple(input_shape)
        self.inq = ActQuant(-8, 7)
        layers, c = [], input_shape[2]
        for s in spec:
            layer = Layer(s, c)
            layers.append(layer)
            c = layer.cout
        self.layers = nn.ModuleList(layers)
        self.qat = False

    def forward(self, x):
        """x: float [N, C, H, W] (already normalized)."""
        if self.qat:
            x = self.inq(x)
        saved = {}
        for layer in self.layers:
            x = layer(x, self.qat, saved)
        return x

    def to_qat(self):
        for layer in self.layers:
            layer.fold()
        self.qat = True
        return self

    def weight_count(self):
        n = 0
        for layer in self.layers:
            for mod in (getattr(layer, 'conv', None), getattr(layer, 'fc', None)):
                if mod is not None:
                    n += mod.weight.numel()
        return n


def q15(scale):
    """Real multiplier -> (Q15 multiplier, shift) as npu_program.requant expects."""
    if scale <= 0:
        raise ValueError('requant scale must be positive')
    fraction, exponent = math.frexp(scale)
    q = round(fraction * 2 ** 15)
    if q == 2 ** 15:
        q //= 2
        exponent += 1
    if not -31 <= exponent <= 31:
        raise ValueError(f'scale {scale} outside the Q15 shift range')
    return q, exponent


def export(net, name='model', metadata=None):
    """Emit an npu_program model. Returns (model dict, input scale)."""
    assert net.qat
    h, w, c = net.input_shape
    s_in = float(net.inq.scale.detach().abs())
    nodes, cur, cur_scale = [], 'input', s_in
    saved = {}
    count = 0

    def fresh(tag):
        nonlocal count
        count += 1
        return f'{tag}{count}'

    def requant_node(src, real_scales, out_scale, relu, channels):
        mults, shifts = zip(*(q15(r / out_scale) for r in real_scales))
        if len(mults) == 1:
            mults, shifts = mults * channels, shifts * channels
        q = fresh('q')
        if relu and RELU_MAX == 15:
            # u4 output: the clamp at zero is the ReLU.
            nodes.append(dict(op='Requant', input=src, output=q, dtype='u4', clamp=[0, 15],
                              multiplier=list(mults), shift=list(shifts)))
            return q
        nodes.append(dict(op='Requant', input=src, output=q, multiplier=list(mults), shift=list(shifts), clamp=[-8, 7]))
        if relu:
            r = fresh('relu')
            nodes.append(dict(op='ReLU', input=q, output=r))
            return r
        return q

    for layer in net.layers:
        spec = layer.spec
        t = spec['type']
        if t == 'conv':
            conv = layer.conv
            ws = layer.wscale.detach().abs()
            wq = torch.clamp(torch.round(conv.weight.detach() / ws.view(-1, 1, 1, 1)), -8, 7).long()
            bias = torch.round(conv.bias.detach() / (cur_scale * ws)).long()
            kh, kw = conv.kernel_size
            if layer.dw:
                weights = wq.reshape(layer.cout, kh * kw).tolist()
            else:
                weights = wq.permute(0, 2, 3, 1).reshape(layer.cout, -1).tolist()
            out = fresh('conv')
            node = dict(op='Conv', input=cur, output=out, channels=layer.cout, kernel=[kh, kw],
                        stride=list(conv.stride), pads=list(spec.get('p', [0, 0, 0, 0])),
                        weights=weights, bias=bias.tolist())
            if layer.dw:
                node['groups'] = layer.cout
            nodes.append(node)
            out_scale = float(layer.aq.scale.detach().abs())
            cur = requant_node(out, [cur_scale * float(s) for s in ws], out_scale, layer.relu, layer.cout)
            pool = spec.get('pool')
            if pool:
                p = fresh('pool')
                k = [2, 2] if pool == 'max2' else [1, 2]
                nodes.append(dict(op='MaxPool', input=cur, output=p, kernel=k, stride=k))
                cur = p
            cur_scale = out_scale
        elif t == 'pool':
            p = fresh('pool')
            k = list(spec['k'])
            s = list(spec.get('s', spec['k']))
            if spec['kind'] == 'max':
                nodes.append(dict(op='MaxPool', input=cur, output=p, kernel=k, stride=s))
                cur = p
            else:
                nodes.append(dict(op='AvgPool', input=cur, output=p, kernel=k, stride=s))
                out_scale = float(layer.aq.scale.detach().abs())
                cur = requant_node(p, [cur_scale / (k[0] * k[1])], out_scale, layer.relu, layer.cin)
                cur_scale = out_scale
        elif t == 'gap':
            shape = layer.spatial
            p = fresh('gap')
            nodes.append(dict(op='AvgPool', input=cur, output=p, kernel=list(shape), stride=list(shape)))
            out_scale = float(layer.aq.scale.detach().abs())
            cur = requant_node(p, [cur_scale / (shape[0] * shape[1])], out_scale, layer.relu, layer.cin)
            cur_scale = out_scale
        elif t == 'fc':
            fc = layer.fc
            ws = layer.wscale.detach().abs()
            wq = torch.clamp(torch.round(fc.weight.detach() / ws.view(-1, 1)), -8, 7).long()
            bias = torch.round(fc.bias.detach() / (cur_scale * ws)).long()
            out = fresh('fc')
            # Flattened NHWC input: torch flattens NCHW, so reorder the weights.
            hh, ww, cc = layer.flat_shape
            wq = wq.reshape(-1, cc, hh, ww).permute(0, 2, 3, 1).reshape(wq.shape[0], -1)
            nodes.append(dict(op='FC', input=cur, output=out, channels=layer.cout,
                              weights=wq.tolist(), bias=bias.tolist()))
            if layer.last:
                cur = out
            else:
                out_scale = float(layer.aq.scale.detach().abs())
                cur = requant_node(out, [cur_scale * float(s) for s in ws], out_scale, layer.relu, layer.cout)
                cur_scale = out_scale
        elif t == 'save':
            saved[spec['name']] = (cur, cur_scale)
        elif t == 'add':
            other, other_scale = saved[spec['name']]
            base = max(cur_scale, other_scale) / 127
            wa, wb = round(cur_scale / base), round(other_scale / base)
            a = fresh('add')
            nodes.append(dict(op='Add', input=cur, input2=other, output=a, weights=[wa, wb]))
            out_scale = float(layer.aq.scale.detach().abs())
            cur = requant_node(a, [base], out_scale, layer.relu, layer.cout)
            cur_scale = out_scale
        layer.export_tensor = (cur, cur_scale)
    model = dict(input=dict(name='input', shape=[h, w, c], dtype='i4'), nodes=nodes,
                 metadata=dict(name=name, input_scale=s_in, **(metadata or {})))
    return model


def annotate_shapes(net):
    """Record spatial sizes needed by export (GAP window, FC flatten order)."""
    h, w, c = net.input_shape
    x = torch.zeros(1, c, h, w, device=next(net.parameters()).device)
    saved = {}
    with torch.no_grad():
        was = net.qat
        net.qat = False
        net.eval()
        for layer in net.layers:
            if layer.spec['type'] == 'gap':
                layer.spatial = (x.shape[2], x.shape[3])
            if layer.spec['type'] == 'fc':
                layer.flat_shape = (x.shape[2], x.shape[3], x.shape[1]) if x.dim() == 4 else (1, 1, x.shape[1])
            x = layer(x, False, saved)
        net.qat = was
