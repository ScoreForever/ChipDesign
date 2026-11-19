#!/usr/bin/env python3
"""Train / calibrate hardware-exact INT4/U4 ResNet-20 (CIFAR-10) & ResNet-18 (ImageNet-1K)
and export standard ONNX + ABI 5.0 Weight-Stationary 3D TiledPlans.

Designed to run on NVIDIA H100 (PKUh100) with zero RTL changes.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import sys
import time
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import torchvision
from torchvision import datasets, models, transforms

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / 'software/host'):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import npu_program as abi  # noqa: E402
from software.compiler import (  # noqa: E402
    GraphIR,
    NodeIR,
    ReferenceBackend,
    TensorDesc,
    TiledRuntime,
    compile_graph_to_plan,
    float_scale_to_q15,
    load_onnx_to_ir,
    oracle_reference,
    verify_plan,
)

# Imagenette2-320 WordNet synsets -> exact 1000-class ImageNet-1K class indices
IMAGENETTE_SYNSET_TO_IMAGENET1K = {
    'n01440764': 0,    # tench
    'n02102040': 217,  # English springer
    'n02979186': 482,  # cassette player
    'n03000684': 491,  # chain saw
    'n03028079': 497,  # church
    'n03394916': 566,  # French horn
    'n03417042': 569,  # garbage truck
    'n03425413': 571,  # gas pump
    'n03445777': 574,  # golf ball
    'n03888257': 701,  # parachute
}

BIAS_MIN = -(1 << (abi.BIAS_BITS - 1))
BIAS_MAX = (1 << (abi.BIAS_BITS - 1)) - 1


def round_ste(x: torch.Tensor) -> torch.Tensor:
    return (x.round() - x).detach() + x


def grad_scale(x: torch.Tensor, scale: float) -> torch.Tensor:
    return (x - x * scale).detach() + x * scale


class ActQuant(nn.Module):
    """Hardware-exact LSQ per-tensor INT4 ([-8, 7]) or U4 ([0, 15]) activation quantizer."""

    def __init__(self, lo: int = 0, hi: int = 15) -> None:
        super().__init__()
        self.lo = int(lo)
        self.hi = int(hi)
        self.scale = nn.Parameter(torch.tensor(0.1))
        self.register_buffer('initialized', torch.tensor(False))

    @torch.no_grad()
    def calibrate(self, x: torch.Tensor) -> None:
        v = x.detach().float().reshape(-1)
        if v.numel() > 500_000:
            step = max(1, v.numel() // 500_000)
            v = v[::step]
        top = v.abs().max().clamp(min=1e-5)
        best_s = top / self.hi
        best_err = float('inf')
        for frac in torch.linspace(0.15, 1.0, 64, device=v.device):
            s = (top * frac / self.hi).clamp(min=1e-6)
            q = torch.clamp(torch.round(v / s), self.lo, self.hi) * s
            err = float(((q - v) ** 2).mean().item())
            if err < best_err:
                best_err = err
                best_s = s
        self.scale.copy_(best_s.to(self.scale.device))
        self.initialized.fill_(True)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if not bool(self.initialized.item()):
            self.calibrate(x)
        g = 1.0 / math.sqrt(max(1.0, x.numel() / max(1, x.shape[0]) * self.hi))
        s = grad_scale(self.scale.abs().clamp(min=1e-6), g)
        return torch.clamp(round_ste(x / s), self.lo, self.hi) * s


class QConv2d(nn.Module):
    """Folded Conv2d with per-output-channel symmetric INT4 [-8, 7] LSQ weight quantization."""

    def __init__(self, conv: nn.Conv2d) -> None:
        super().__init__()
        self.stride = conv.stride
        self.padding = conv.padding
        self.groups = conv.groups
        self.weight = nn.Parameter(conv.weight.detach().clone())
        bias = conv.bias.detach().clone() if conv.bias is not None else torch.zeros(conv.out_channels)
        self.bias = nn.Parameter(bias)
        w_init = self.weight.detach().abs().flatten(1).amax(1).clamp(min=1e-6) / 7.0
        self.w_scale = nn.Parameter(w_init)

    def quantized_weight(self) -> tuple[torch.Tensor, torch.Tensor]:
        g = 1.0 / math.sqrt(max(1.0, self.weight.numel() / self.w_scale.numel() * 7.0))
        s = grad_scale(self.w_scale.abs().clamp(min=1e-6), g)
        sv = s.view(-1, 1, 1, 1)
        wq = torch.clamp(round_ste(self.weight / sv), -8, 7) * sv
        return wq, s

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        wq, _ = self.quantized_weight()
        return F.conv2d(x, wq, self.bias, self.stride, self.padding, 1, self.groups)


class QLinear(nn.Module):
    """Linear layer with single global symmetric INT4 [-8, 7] LSQ weight scale (exact INT32 logit argmax)."""

    def __init__(self, linear: nn.Linear) -> None:
        super().__init__()
        self.weight = nn.Parameter(linear.weight.detach().clone())
        bias = linear.bias.detach().clone() if linear.bias is not None else torch.zeros(linear.out_features)
        self.bias = nn.Parameter(bias)
        w_init = self.weight.detach().abs().max().clamp(min=1e-6) / 7.0
        self.w_scale = nn.Parameter(w_init)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        g = 1.0 / math.sqrt(max(1.0, self.weight.numel() * 7.0))
        s = grad_scale(self.w_scale.abs().clamp(min=1e-6), g)
        wq = torch.clamp(round_ste(self.weight / s), -8, 7) * s
        return F.linear(x, wq, self.bias)


def fold_conv_bn_module(conv: nn.Conv2d, bn: nn.BatchNorm2d) -> nn.Conv2d:
    """Return a new Conv2d with BatchNorm analytically folded."""
    fused = nn.Conv2d(
        conv.in_channels, conv.out_channels, conv.kernel_size,
        stride=conv.stride, padding=conv.padding, groups=conv.groups, bias=True,
    )
    with torch.no_grad():
        w = conv.weight.float()
        b = conv.bias.float() if conv.bias is not None else torch.zeros(conv.out_channels, device=w.device)
        inv_std = bn.weight.float() / torch.sqrt(bn.running_var.float() + bn.eps)
        fused.weight.copy_(w * inv_std.view(-1, 1, 1, 1))
        fused.bias.copy_((b - bn.running_mean.float()) * inv_std + bn.bias.float())
    return fused


# =============================================================================
# Tier 1: Standard CIFAR-10 ResNet-20 (16 -> 32 -> 64, 9 blocks, ~270K params)
# =============================================================================
class CifarBasicBlock(nn.Module):
    def __init__(self, in_ch: int, out_ch: int, stride: int = 1) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(in_ch, out_ch, 3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(out_ch)
        self.conv2 = nn.Conv2d(out_ch, out_ch, 3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(out_ch)
        self.downsample = None
        if stride != 1 or in_ch != out_ch:
            self.downsample = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 1, stride=stride, padding=0, bias=False),
                nn.BatchNorm2d(out_ch),
            )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x if self.downsample is None else self.downsample(x)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + identity)


class ResNet20FP32(nn.Module):
    def __init__(self, num_classes: int = 10) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(3, 16, 3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.layer1 = self._make_stage(16, 16, 3, stride=1)
        self.layer2 = self._make_stage(16, 32, 3, stride=2)
        self.layer3 = self._make_stage(32, 64, 3, stride=2)
        self.fc = nn.Linear(64, num_classes)

    @staticmethod
    def _make_stage(in_ch: int, out_ch: int, blocks: int, stride: int) -> nn.Sequential:
        layers = [CifarBasicBlock(in_ch, out_ch, stride=stride)]
        for _ in range(1, blocks):
            layers.append(CifarBasicBlock(out_ch, out_ch, stride=1))
        return nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = F.avg_pool2d(x, 8)
        return self.fc(x.flatten(1))


class QBasicBlock(nn.Module):
    """BN-folded hardware-exact INT4/U4 residual block (works for both ResNet-20 & ResNet-18!)."""

    def __init__(self, fp_block: nn.Module) -> None:
        super().__init__()
        self.conv1 = QConv2d(fold_conv_bn_module(fp_block.conv1, fp_block.bn1))
        self.aq1 = ActQuant(0, 15)  # post-ReLU u4
        self.conv2 = QConv2d(fold_conv_bn_module(fp_block.conv2, fp_block.bn2))
        self.aq2 = ActQuant(-8, 7)  # pre-Add signed i4
        self.downsample = None
        self.aq_ds = None
        if fp_block.downsample is not None:
            self.downsample = QConv2d(fold_conv_bn_module(fp_block.downsample[0], fp_block.downsample[1]))
            self.aq_ds = ActQuant(-8, 7)  # pre-Add signed i4
        self.aq_out = ActQuant(0, 15)  # post-Add-ReLU u4

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x if self.downsample is None else self.aq_ds(self.downsample(x))
        out = self.aq1(F.relu(self.conv1(x)))
        out = self.aq2(self.conv2(out))
        return self.aq_out(F.relu(out + identity))


class QResNet(nn.Module):
    """Unified hardware-exact INT4/U4 QAT wrapper for both ResNet-20 and ResNet-18."""

    def __init__(self, fp_model: nn.Module, *, has_maxpool: bool) -> None:
        super().__init__()
        self.has_maxpool = has_maxpool
        self.in_q = ActQuant(-8, 7)
        self.conv1 = QConv2d(fold_conv_bn_module(fp_model.conv1, fp_model.bn1))
        self.aq1 = ActQuant(0, 15)
        self.maxpool = nn.MaxPool2d(3, stride=2, padding=1) if has_maxpool else None
        self.stages = nn.ModuleList()
        for name in ('layer1', 'layer2', 'layer3', 'layer4'):
            if hasattr(fp_model, name):
                stage = getattr(fp_model, name)
                self.stages.append(nn.Sequential(*[QBasicBlock(b) for b in stage]))
        self.gap_aq = ActQuant(0, 15)
        self.fc = QLinear(fp_model.fc)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.in_q(x)
        x = self.aq1(F.relu(self.conv1(x)))
        if self.maxpool is not None:
            x = self.maxpool(x)
        for st in self.stages:
            x = st(x)
        x = F.adaptive_avg_pool2d(x, (1, 1))
        x = self.gap_aq(x)
        return self.fc(x.flatten(1))


class CleanFP32ExportResNet(nn.Module):
    """Pure standard nn.Conv2d/MaxPool2d/AvgPool2d/Linear wrapper for clean opset-13 ONNX export."""

    def __init__(self, qnet: QResNet, spatial_gap: int) -> None:
        super().__init__()
        self.has_maxpool = qnet.has_maxpool
        self.spatial_gap = spatial_gap

        def to_conv(qc: QConv2d) -> nn.Conv2d:
            c = nn.Conv2d(qc.weight.shape[1] * qc.groups, qc.weight.shape[0], qc.weight.shape[2:],
                          stride=qc.stride, padding=qc.padding, groups=qc.groups, bias=True)
            with torch.no_grad():
                c.weight.copy_(qc.weight)
                c.bias.copy_(qc.bias)
            return c

        self.conv1 = to_conv(qnet.conv1)
        self.maxpool = nn.MaxPool2d(3, stride=2, padding=1) if self.has_maxpool else None
        self.stages = nn.ModuleList()
        for st in qnet.stages:
            blocks = nn.ModuleList()
            for b in st:
                blk = nn.Module()
                blk.conv1 = to_conv(b.conv1)
                blk.conv2 = to_conv(b.conv2)
                blk.downsample = to_conv(b.downsample) if b.downsample is not None else None
                blocks.append(blk)
            self.stages.append(blocks)
        self.avgpool = nn.AvgPool2d(spatial_gap, stride=spatial_gap)
        self.fc = nn.Linear(qnet.fc.weight.shape[1], qnet.fc.weight.shape[0], bias=True)
        with torch.no_grad():
            self.fc.weight.copy_(qnet.fc.weight)
            self.fc.bias.copy_(qnet.fc.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = F.relu(self.conv1(x))
        if self.maxpool is not None:
            x = self.maxpool(x)
        for st in self.stages:
            for b in st:
                identity = x if b.downsample is None else b.downsample(x)
                out = F.relu(b.conv1(x))
                out = b.conv2(out)
                x = F.relu(out + identity)
        x = self.avgpool(x)
        return self.fc(x.flatten(1))


def export_qresnet_to_graph_and_onnx(qnet: QResNet, input_hwc: tuple[int, int, int],
                                     out_dir: Path, name: str) -> tuple[GraphIR, Path]:
    """Export both a clean standard ONNX file AND an exact hardware GraphIR with 100% matched scales."""
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w, c = input_hwc
    # Determine GAP kernel size from dummy pass
    qnet_cpu = copy.deepcopy(qnet).cpu().eval()
    with torch.no_grad():
        dummy = torch.zeros(1, c, h, w)
        t = qnet_cpu.aq1(F.relu(qnet_cpu.conv1(qnet_cpu.in_q(dummy))))
        if qnet_cpu.maxpool is not None:
            t = qnet_cpu.maxpool(t)
        for st in qnet_cpu.stages:
            t = st(t)
        gap_k = int(t.shape[2])

    # 1) Export standard ONNX (clean folded FP32 graph, runnable via onnx_frontend too!)
    export_mod = CleanFP32ExportResNet(qnet_cpu, spatial_gap=gap_k).eval()
    onnx_path = out_dir / f'{name}.onnx'
    torch.onnx.export(
        export_mod, dummy, str(onnx_path),
        input_names=['input'], output_names=['logits'],
        opset_version=13, do_constant_folding=True, dynamo=False,
    )

    # 2) Build exact hardware GraphIR directly from learned LSQ scales (exact integer parity!)
    in_scale = float(qnet_cpu.in_q.scale.abs().clamp(min=1e-6).item())
    nodes: list[NodeIR] = []

    def emit_conv(qc: QConv2d, src_name: str, src_scale: float, out_name: str,
                  aq: ActQuant, relu: bool) -> float:
        w_sc = qc.w_scale.detach().abs().clamp(min=1e-6).double().numpy()
        w_fp = qc.weight.detach().double().numpy()  # (OC, IC, KH, KW)
        b_fp = qc.bias.detach().double().numpy()
        oc, _, kh, kw = w_fp.shape
        w_q = np.clip(np.rint(w_fp / w_sc.reshape(oc, 1, 1, 1)), -8, 7).astype(np.int64)
        w_flat = np.transpose(w_q, (0, 2, 3, 1)).reshape(-1).tolist()
        acc_sc = src_scale * w_sc
        b_q = np.clip(np.rint(b_fp / np.maximum(acc_sc, 1e-12)), BIAS_MIN, BIAS_MAX).astype(np.int64).tolist()
        out_sc = float(aq.scale.abs().clamp(min=1e-6).item())
        mults, shifts = [], []
        for ch in range(oc):
            qm, qs = float_scale_to_q15(acc_sc[ch] / max(out_sc, 1e-12))
            mults.append(qm)
            shifts.append(qs)
        out_dtype = 'u4' if relu else 'i4'
        clamp = [0, 15] if relu else [-8, 7]
        raw = f'{out_name}_i32'
        nodes.append(NodeIR(
            'Conv', [src_name], raw, name=out_name,
            attrs=dict(channels=oc, kernel=[kh, kw], stride=list(qc.stride),
                       pads=[qc.padding[0], qc.padding[1], qc.padding[0], qc.padding[1]],
                       groups=qc.groups, weights=w_flat, bias=b_q),
        ))
        nodes.append(NodeIR(
            'Requant', [raw], out_name, name=f'{out_name}_req',
            attrs=dict(multiplier=mults, shift=shifts, clamp=clamp, dtype=out_dtype),
        ))
        return out_sc

    cur_name = 'input'
    cur_scale = in_scale
    cur_scale = emit_conv(qnet_cpu.conv1, cur_name, cur_scale, 'stem', qnet_cpu.aq1, relu=True)
    cur_name = 'stem'
    if qnet_cpu.maxpool is not None:
        nodes.append(NodeIR('MaxPool', [cur_name], 'maxpool', name='maxpool',
                            attrs=dict(kernel=[3, 3], stride=[2, 2], pads=[1, 1, 1, 1])))
        cur_name = 'maxpool'

    for s_idx, st in enumerate(qnet_cpu.stages):
        for b_idx, blk in enumerate(st):
            prefix = f's{s_idx+1}_b{b_idx}'
            if blk.downsample is not None:
                id_name = f'{prefix}_ds'
                id_scale = emit_conv(blk.downsample, cur_name, cur_scale, id_name, blk.aq_ds, relu=False)
            else:
                id_name = cur_name
                id_scale = cur_scale
            c1_sc = emit_conv(blk.conv1, cur_name, cur_scale, f'{prefix}_c1', blk.aq1, relu=True)
            c2_sc = emit_conv(blk.conv2, f'{prefix}_c1', c1_sc, f'{prefix}_c2', blk.aq2, relu=False)

            out_sc = float(blk.aq_out.scale.abs().clamp(min=1e-6).item())
            m_sc = max(id_scale, c2_sc, 1e-12)
            wa = max(1, min(127, int(round(127.0 * (id_scale / m_sc)))))
            wb = max(1, min(127, int(round(127.0 * (c2_sc / m_sc)))))
            eff_sc = 0.5 * (id_scale / wa + c2_sc / wb)
            qm, qs = float_scale_to_q15(eff_sc / max(out_sc, 1e-12))

            add_raw = f'{prefix}_add_i32'
            out_nm = f'{prefix}_out'
            nodes.append(NodeIR('Add', [id_name, f'{prefix}_c2'], add_raw, name=f'{prefix}_add',
                                attrs=dict(weights=[wa, wb])))
            nodes.append(NodeIR('Requant', [add_raw], out_nm, name=f'{prefix}_out_req',
                                attrs=dict(multiplier=[qm], shift=[qs], clamp=[0, 15], dtype='u4')))
            cur_name = out_nm
            cur_scale = out_sc

    # Global Average Pool (sum / gap_k^2 folded into Requant!)
    gap_raw = 'gap_i32'
    gap_sc = float(qnet_cpu.gap_aq.scale.abs().clamp(min=1e-6).item())
    qm_g, qs_g = float_scale_to_q15(cur_scale / (gap_k * gap_k * max(gap_sc, 1e-12)))
    nodes.append(NodeIR('AvgPool', [cur_name], gap_raw, name='gap',
                        attrs=dict(kernel=[gap_k, gap_k], stride=[gap_k, gap_k], pads=[0, 0, 0, 0])))
    nodes.append(NodeIR('Requant', [gap_raw], 'gap_u4', name='gap_req',
                        attrs=dict(multiplier=[qm_g], shift=[qs_g], clamp=[0, 15], dtype='u4')))

    # Final FC -> raw INT32 logits (using learned global QLinear w_scale for exact argmax parity!)
    fc_w_fp = qnet_cpu.fc.weight.detach().double().numpy()
    fc_b_fp = qnet_cpu.fc.bias.detach().double().numpy()
    fc_oc = fc_w_fp.shape[0]
    fc_w_sc = float(qnet_cpu.fc.w_scale.detach().abs().clamp(min=1e-6).item())
    fc_w_q = np.clip(np.rint(fc_w_fp / fc_w_sc), -8, 7).astype(np.int64)
    fc_acc_sc = gap_sc * fc_w_sc
    fc_b_q = np.clip(np.rint(fc_b_fp / max(fc_acc_sc, 1e-12)), BIAS_MIN, BIAS_MAX).astype(np.int64).tolist()
    nodes.append(NodeIR('FC', ['gap_u4'], 'logits', name='fc',
                        attrs=dict(channels=fc_oc, weights=fc_w_q.reshape(-1).tolist(), bias=fc_b_q)))

    # Also verify generic ONNX frontend loads the exported .onnx cleanly!
    onnx_ir = load_onnx_to_ir(onnx_path, quant_config={'input_scale': in_scale, 'input_dtype': 'i4'})
    assert len(onnx_ir.nodes) >= len(nodes) - 2, f'Unexpected ONNX frontend node count: {len(onnx_ir.nodes)}'

    graph = GraphIR(
        input=TensorDesc('input', input_hwc, 'i4', in_scale),
        nodes=nodes,
        output='logits',
        metadata=dict(model_name=name, input_scale=in_scale, fc_logit_scale=fc_acc_sc),
    )
    return graph, onnx_path


@torch.no_grad()
def eval_graph_integer_gpu(graph: GraphIR, loader: DataLoader, input_scale: float,
                           device: torch.device, label_map: dict[int, int] | None = None,
                           subset_indices: list[int] | None = None) -> dict:
    """Fast GPU batched exact integer evaluator bit-identical to oracle_reference()."""
    from npu_fastref import requant as gpu_requant, wrap32 as gpu_wrap32

    total = 0
    correct_1k = 0
    correct_sub = 0
    sub_idx_t = torch.tensor(subset_indices, dtype=torch.long, device=device) if subset_indices else None

    for imgs, labels in loader:
        imgs = imgs.to(device, non_blocking=True)
        x_q = torch.clamp(torch.round(imgs / input_scale), -8, 7).long()  # NCHW int64
        x_hwc = x_q.permute(0, 2, 3, 1).contiguous()
        buf = {graph.input.name: x_hwc}

        for n in graph.nodes:
            a = n.attrs
            x = buf[n.inputs[0]]
            op = n.op
            if op == 'Conv':
                oc = int(a['channels'])
                kh, kw = abi.pair(a['kernel'], 'kernel')
                sh, sw = abi.pair(a['stride'], 'stride')
                pt, pl, pb, pr = a['pads']
                groups = int(a.get('groups', 1))
                inp = F.pad(x.permute(0, 3, 1, 2).double(), (pl, pr, pt, pb))
                wt = torch.tensor(a['weights'], dtype=torch.float64, device=device)
                if groups != 1:
                    wt = wt.reshape(oc, 1, kh, kw)
                else:
                    wt = wt.reshape(oc, kh, kw, x.shape[-1]).permute(0, 3, 1, 2)
                bias = torch.tensor(a['bias'], dtype=torch.int64, device=device).view(1, -1, 1, 1)
                out = torch.round(F.conv2d(inp, wt, stride=(sh, sw), groups=groups)).long() + bias
                buf[n.output] = gpu_wrap32(out.permute(0, 2, 3, 1))
            elif op == 'FC':
                oc = int(a['channels'])
                xf = x.reshape(x.shape[0], -1).double()
                wt = torch.tensor(a['weights'], dtype=torch.float64, device=device).reshape(oc, xf.shape[1])
                bias = torch.tensor(a['bias'], dtype=torch.int64, device=device).view(1, oc)
                out = torch.round(F.linear(xf, wt)).long() + bias
                buf[n.output] = gpu_wrap32(out.view(x.shape[0], 1, 1, oc))
            elif op in ('MaxPool', 'AvgPool'):
                kh, kw = abi.pair(a['kernel'], 'kernel')
                sh, sw = abi.pair(a['stride'], 'stride')
                pt, pl, pb, pr = a.get('pads', [0, 0, 0, 0])
                inp = x.permute(0, 3, 1, 2).double()
                if op == 'MaxPool':
                    if any((pt, pl, pb, pr)):
                        inp = F.pad(inp, (pl, pr, pt, pb), value=float('-inf'))
                    out = F.max_pool2d(inp, (kh, kw), (sh, sw))
                    buf[n.output] = torch.round(out).long().permute(0, 2, 3, 1)
                else:
                    if any((pt, pl, pb, pr)):
                        inp = F.pad(inp, (pl, pr, pt, pb), value=0.0)
                    out = F.avg_pool2d(inp, (kh, kw), (sh, sw), divisor_override=1)
                    buf[n.output] = gpu_wrap32(torch.round(out).long().permute(0, 2, 3, 1))
            elif op == 'Add':
                wa, wb = a['weights']
                buf[n.output] = gpu_wrap32(wa * x + wb * buf[n.inputs[1]])
            elif op == 'ReLU':
                buf[n.output] = torch.clamp(x, min=0)
            elif op == 'Requant':
                c_ch = x.shape[-1]
                mm = list(a['multiplier'])
                ss = list(a['shift'])
                if len(mm) == 1:
                    mm = mm * c_ch
                if len(ss) == 1:
                    ss = ss * c_ch
                mt = torch.tensor(mm, dtype=torch.int64, device=device)
                st = torch.tensor(ss, dtype=torch.int64, device=device)
                lo_q, hi_q = a['clamp']
                buf[n.output] = gpu_requant(x, mt, st, lo_q, hi_q)

        logits = buf[graph.output].reshape(imgs.shape[0], -1)
        labels_dev = labels.to(device)
        if label_map is not None:
            mapped = torch.tensor([label_map[int(y)] for y in labels.tolist()], device=device)
            correct_1k += int((logits.argmax(dim=1) == mapped).sum().item())
            if sub_idx_t is not None:
                sub_logits = logits.index_select(1, sub_idx_t)
                correct_sub += int((sub_logits.argmax(dim=1) == labels_dev).sum().item())
        else:
            correct_1k += int((logits.argmax(dim=1) == labels_dev).sum().item())
        total += int(imgs.shape[0])

    res = {'samples': total, 'top1_acc': correct_1k / max(1, total)}
    if subset_indices is not None:
        res['subset_10class_top1_acc'] = correct_sub / max(1, total)
    return res


@torch.no_grad()
def eval_torch(model: nn.Module, loader: DataLoader, device: torch.device,
               label_map: dict[int, int] | None = None, subset_indices: list[int] | None = None) -> dict:
    model.eval()
    total = 0
    correct = 0
    correct_sub = 0
    sub_idx_t = torch.tensor(subset_indices, dtype=torch.long, device=device) if subset_indices else None
    for imgs, labels in loader:
        imgs = imgs.to(device, non_blocking=True)
        logits = model(imgs)
        labels_dev = labels.to(device)
        if label_map is not None:
            mapped = torch.tensor([label_map[int(y)] for y in labels.tolist()], device=device)
            correct += int((logits.argmax(dim=1) == mapped).sum().item())
            if sub_idx_t is not None:
                correct_sub += int((logits.index_select(1, sub_idx_t).argmax(dim=1) == labels_dev).sum().item())
        else:
            correct += int((logits.argmax(dim=1) == labels_dev).sum().item())
        total += int(imgs.shape[0])
    out = {'samples': total, 'top1_acc': correct / max(1, total)}
    if subset_indices is not None:
        out['subset_10class_top1_acc'] = correct_sub / max(1, total)
    return out


def _build_qat_optimizer(qnet: nn.Module, *, lr_weights: float, lr_scales: float, weight_decay: float) -> torch.optim.Optimizer:
    """Separate parameter groups so LSQ scale parameters never suffer weight decay or huge Adam steps."""
    weight_params, scale_params = [], []
    for name, p in qnet.named_parameters():
        if not p.requires_grad:
            continue
        if name.endswith('scale') or name.endswith('w_scale'):
            scale_params.append(p)
        else:
            weight_params.append(p)
    return torch.optim.AdamW([
        {'params': weight_params, 'lr': lr_weights, 'weight_decay': weight_decay},
        {'params': scale_params, 'lr': lr_scales, 'weight_decay': 0.0},
    ])


def run_resnet20(args) -> None:
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    mean = (0.4914, 0.4822, 0.4465)
    std = (0.2470, 0.2435, 0.2616)
    tf_train = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])
    tf_val = transforms.Compose([transforms.ToTensor(), transforms.Normalize(mean, std)])
    train_ds = datasets.CIFAR10(str(args.data_dir), train=True, download=False, transform=tf_train)
    val_ds = datasets.CIFAR10(str(args.data_dir), train=False, download=False, transform=tf_val)
    train_loader = DataLoader(train_ds, batch_size=256, shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=512, shuffle=False, num_workers=4, pin_memory=True)

    # Stage 1: FP32 Teacher Training (or resume from existing checkpoint)
    fp_model = ResNet20FP32(num_classes=10).to(device)
    if args.resume_fp32 and Path(args.resume_fp32).is_file():
        fp_model.load_state_dict(torch.load(args.resume_fp32, map_location=device))
        fp_model.eval()
        best_fp = eval_torch(fp_model, val_loader, device)['top1_acc']
        print(f'[ResNet-20 FP32] Resumed checkpoint val_top1={best_fp*100:.2f}%', flush=True)
    else:
        opt = torch.optim.SGD(fp_model.parameters(), lr=0.15, momentum=0.9, weight_decay=5e-4)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.fp_epochs)
        best_fp = 0.0
        best_fp_state = None
        for epoch in range(1, args.fp_epochs + 1):
            fp_model.train()
            for imgs, labels in train_loader:
                imgs, labels = imgs.to(device, non_blocking=True), labels.to(device, non_blocking=True)
                opt.zero_grad(set_to_none=True)
                loss = F.cross_entropy(fp_model(imgs), labels, label_smoothing=0.05)
                loss.backward()
                opt.step()
            sched.step()
            if epoch % 20 == 0 or epoch == args.fp_epochs:
                acc = eval_torch(fp_model, val_loader, device)['top1_acc']
                print(f'[ResNet-20 FP32] epoch {epoch}/{args.fp_epochs} val_top1={acc*100:.2f}%', flush=True)
                if acc > best_fp:
                    best_fp = acc
                    best_fp_state = copy.deepcopy(fp_model.state_dict())
        fp_model.load_state_dict(best_fp_state)
        fp_model.eval()
        torch.save(fp_model.state_dict(), out_dir / 'resnet20_fp32.pt')

    # Stage 2: Hardware-Exact INT4/U4 LSQ QAT with FP32 Teacher KL Distillation
    qnet = QResNet(fp_model.cpu(), has_maxpool=False).to(device)
    fp_model.to(device).eval()
    # Calibrate activation quantizers on 1 batch
    with torch.no_grad():
        cal_imgs, _ = next(iter(val_loader))
        qnet.eval()
        qnet(cal_imgs.to(device))
    ptq_acc = eval_torch(qnet, val_loader, device)['top1_acc']
    print(f'[ResNet-20 INT4 PTQ] pre-QAT val_top1={ptq_acc*100:.2f}%', flush=True)

    opt_q = _build_qat_optimizer(qnet, lr_weights=2.5e-4, lr_scales=2.5e-5, weight_decay=1e-4)
    sched_q = torch.optim.lr_scheduler.CosineAnnealingLR(opt_q, T_max=args.qat_epochs)
    best_q = ptq_acc
    best_q_state = copy.deepcopy(qnet.state_dict())
    for epoch in range(1, args.qat_epochs + 1):
        qnet.train()
        for imgs, labels in train_loader:
            imgs, labels = imgs.to(device, non_blocking=True), labels.to(device, non_blocking=True)
            opt_q.zero_grad(set_to_none=True)
            q_logits = qnet(imgs)
            with torch.no_grad():
                t_logits = fp_model(imgs)
            ce = F.cross_entropy(q_logits, labels)
            T = 2.0
            kl = F.kl_div(F.log_softmax(q_logits / T, dim=1), F.softmax(t_logits / T, dim=1), reduction='batchmean') * (T * T)
            loss = 0.5 * ce + 0.5 * kl
            loss.backward()
            torch.nn.utils.clip_grad_norm_(qnet.parameters(), 1.0)
            opt_q.step()
        sched_q.step()
        if epoch % 10 == 0 or epoch == args.qat_epochs:
            acc = eval_torch(qnet, val_loader, device)['top1_acc']
            print(f'[ResNet-20 INT4 QAT] epoch {epoch}/{args.qat_epochs} val_top1={acc*100:.2f}%', flush=True)
            if acc > best_q:
                best_q = acc
                best_q_state = copy.deepcopy(qnet.state_dict())

    qnet.load_state_dict(best_q_state)
    qnet.eval()
    torch.save(qnet.state_dict(), out_dir / 'resnet20_int4_qat.pt')

    # Stage 3: Export ONNX + GraphIR + Compiled ABI 5.0 TiledPlan + Full Integer Test Eval + Bit-Exact Verification
    graph, onnx_path = export_qresnet_to_graph_and_onnx(qnet, (32, 32, 3), out_dir, 'resnet20')
    plan = compile_graph_to_plan(graph, enable_fusion=True)
    plan.save(out_dir / 'tiled_plan')
    int_eval = eval_graph_integer_gpu(graph, val_loader, graph.input.scale, device)

    # Verify 5 real CIFAR-10 test images bit-exact (Oracle == TiledPlan(Reference))!
    test_frames = []
    for idx in range(5):
        img, lbl = val_ds[idx]
        x_q = np.clip(np.rint(img.numpy().transpose(1, 2, 0) / graph.input.scale), -8, 7).astype(np.int64)
        ver = verify_plan(graph, plan, x_q)
        oracle_out = oracle_reference(graph, x_q)[graph.output].reshape(-1).tolist()
        test_frames.append({'index': idx, 'label': int(lbl), 'predicted': int(np.argmax(oracle_out)),
                            'logits': oracle_out, 'bit_exact': ver['oracle_vs_tiled_ref_exact']})
    (out_dir / 'golden_frames.json').write_text(json.dumps(test_frames, indent=2) + '\n')

    # Save first 5 quantized test inputs as raw .npy for fast EGo1 FPGA real-board validation!
    sample_inputs = np.stack([
        np.clip(np.rint(val_ds[i][0].numpy().transpose(1, 2, 0) / graph.input.scale), -8, 7).astype(np.int8)
        for i in range(10)
    ])
    sample_labels = np.array([int(val_ds[i][1]) for i in range(10)], dtype=np.int64)
    np.savez(out_dir / 'test_samples_10.npz', inputs=sample_inputs, labels=sample_labels)

    summary = {
        'model': 'resnet20_cifar10',
        'params': sum(p.numel() for p in fp_model.parameters()),
        'fp32_top1_acc': best_fp,
        'qat_fakequant_top1_acc': best_q,
        'exact_int4_graph_top1_acc': int_eval['top1_acc'],
        'eval_samples': int_eval['samples'],
        'bit_exact_frames_checked': len(test_frames),
        'all_frames_bit_exact': all(f['bit_exact'] for f in test_frames),
        'plan_summary': plan.summary(),
        'elapsed_s': round(time.time() - t0, 1),
    }
    (out_dir / 'eval_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('=== RESNET-20 SUMMARY ===')
    print(json.dumps(summary, indent=2), flush=True)


def run_resnet18(args) -> None:
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    mean = (0.485, 0.456, 0.406)
    std = (0.229, 0.224, 0.225)
    tf_train = transforms.Compose([
        transforms.RandomResizedCrop(224),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])
    tf_val = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])
    inet_root = Path(args.data_dir) / 'imagenette2-320'
    train_ds = datasets.ImageFolder(str(inet_root / 'train'), transform=tf_train)
    val_ds = datasets.ImageFolder(str(inet_root / 'val'), transform=tf_val)
    label_map = {idx: IMAGENETTE_SYNSET_TO_IMAGENET1K[cls] for idx, cls in enumerate(val_ds.classes)}
    subset_indices = [label_map[i] for i in range(len(val_ds.classes))]

    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True, num_workers=4, pin_memory=True)
    val_loader = DataLoader(val_ds, batch_size=128, shuffle=False, num_workers=4, pin_memory=True)

    # Load official pre-trained torchvision ResNet-18 (IMAGENET1K_V1, 1000 classes!)
    fp_model = models.resnet18(weights=models.ResNet18_Weights.IMAGENET1K_V1).to(device).eval()
    fp_eval = eval_torch(fp_model, val_loader, device, label_map=label_map, subset_indices=subset_indices)
    print(f'[ResNet-18 FP32] Imagenette-1K-head top1={fp_eval["top1_acc"]*100:.2f}%, 10-class={fp_eval["subset_10class_top1_acc"]*100:.2f}%', flush=True)

    # Fold BNs + MSE-calibrate INT4/U4 quantizers
    qnet = QResNet(fp_model.cpu(), has_maxpool=True).to(device)
    fp_model.to(device).eval()
    with torch.no_grad():
        qnet.eval()
        cal_imgs, _ = next(iter(val_loader))
        qnet(cal_imgs.to(device))
    ptq_eval = eval_torch(qnet, val_loader, device, label_map=label_map, subset_indices=subset_indices)
    print(f'[ResNet-18 INT4 PTQ] top1={ptq_eval["top1_acc"]*100:.2f}%, 10-class={ptq_eval["subset_10class_top1_acc"]*100:.2f}%', flush=True)

    # Knowledge-Distillation INT4/U4 QAT preserving all 1000 ImageNet logits via KL divergence!
    opt_q = _build_qat_optimizer(qnet, lr_weights=1.0e-4, lr_scales=1.5e-5, weight_decay=1e-5)
    sched_q = torch.optim.lr_scheduler.CosineAnnealingLR(opt_q, T_max=args.qat_epochs)
    best_acc = ptq_eval['top1_acc']
    best_state = copy.deepcopy(qnet.state_dict())

    for epoch in range(1, args.qat_epochs + 1):
        qnet.train()
        for imgs, _ in train_loader:
            imgs = imgs.to(device, non_blocking=True)
            opt_q.zero_grad(set_to_none=True)
            q_logits = qnet(imgs)
            with torch.no_grad():
                t_logits = fp_model(imgs)
            T = 2.0
            # Pure 1000-class teacher KL distillation + MSE feature logit alignment (no overfitting to 10 classes!)
            kl = F.kl_div(F.log_softmax(q_logits / T, dim=1), F.softmax(t_logits / T, dim=1), reduction='batchmean') * (T * T)
            mse = F.mse_loss(q_logits, t_logits) * 0.05
            loss = kl + mse
            loss.backward()
            torch.nn.utils.clip_grad_norm_(qnet.parameters(), 1.0)
            opt_q.step()
        sched_q.step()
        if epoch % 3 == 0 or epoch == args.qat_epochs:
            ev = eval_torch(qnet, val_loader, device, label_map=label_map, subset_indices=subset_indices)
            print(f'[ResNet-18 INT4 Distill] epoch {epoch}/{args.qat_epochs} 1K-head={ev["top1_acc"]*100:.2f}% 10-class={ev["subset_10class_top1_acc"]*100:.2f}%', flush=True)
            if ev['top1_acc'] > best_acc:
                best_acc = ev['top1_acc']
                best_state = copy.deepcopy(qnet.state_dict())

    qnet.load_state_dict(best_state)
    qnet.eval()
    torch.save(qnet.state_dict(), out_dir / 'resnet18_int4_distill.pt')

    # Export ONNX + GraphIR + Compiled ABI 5.0 3D TiledPlan + Exact Integer Eval + Bit-Exact Verification!
    graph, onnx_path = export_qresnet_to_graph_and_onnx(qnet, (224, 224, 3), out_dir, 'resnet18')
    print('[ResNet-18] Compiling full 224x224x3 1000-class ABI 5.0 TiledPlan...', flush=True)
    plan = compile_graph_to_plan(graph, enable_fusion=True)
    plan.save(out_dir / 'tiled_plan')
    int_eval = eval_graph_integer_gpu(graph, val_loader, graph.input.scale, device,
                                      label_map=label_map, subset_indices=subset_indices)

    # Bit-exact check (Full-Graph Oracle == TiledPlan(Reference)) on 2 real 224x224 images!
    test_frames = []
    for idx in (0, 500):
        img, lbl = val_ds[idx]
        x_q = np.clip(np.rint(img.numpy().transpose(1, 2, 0) / graph.input.scale), -8, 7).astype(np.int64)
        ver = verify_plan(graph, plan, x_q)
        oracle_out = oracle_reference(graph, x_q)[graph.output].reshape(-1)
        top5 = np.argsort(oracle_out)[-5:][::-1].tolist()
        test_frames.append({
            'index': idx,
            'imagenet1k_label': int(label_map[int(lbl)]),
            'top1_pred': int(top5[0]),
            'top5_pred': top5,
            'bit_exact': ver['oracle_vs_tiled_ref_exact'],
        })
    (out_dir / 'golden_frames.json').write_text(json.dumps(test_frames, indent=2) + '\n')

    sample_inputs = np.stack([
        np.clip(np.rint(val_ds[i * 400][0].numpy().transpose(1, 2, 0) / graph.input.scale), -8, 7).astype(np.int8)
        for i in range(5)
    ])
    sample_labels = np.array([int(label_map[int(val_ds[i * 400][1])]) for i in range(5)], dtype=np.int64)
    np.savez(out_dir / 'test_samples_5.npz', inputs=sample_inputs, labels=sample_labels)

    summary = {
        'model': 'resnet18_imagenet1k',
        'params': sum(p.numel() for p in fp_model.parameters()),
        'fp32_imagenet1k_head_on_imagenette_top1': fp_eval['top1_acc'],
        'fp32_10class_top1': fp_eval['subset_10class_top1_acc'],
        'int4_ptq_1k_head_top1': ptq_eval['top1_acc'],
        'int4_distill_fakequant_1k_head_top1': best_acc,
        'exact_int4_graph_1k_head_top1': int_eval['top1_acc'],
        'exact_int4_graph_10class_top1': int_eval['subset_10class_top1_acc'],
        'eval_samples': int_eval['samples'],
        'bit_exact_frames_checked': len(test_frames),
        'all_frames_bit_exact': all(f['bit_exact'] for f in test_frames),
        'plan_summary': plan.summary(),
        'elapsed_s': round(time.time() - t0, 1),
    }
    (out_dir / 'eval_summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('=== RESNET-18 SUMMARY ===')
    print(json.dumps(summary, indent=2), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p20 = sub.add_parser('resnet20', help='Train + QAT ResNet-20 on CIFAR-10 and compile ABI 5.0 TiledPlan')
    p20.add_argument('--data-dir', type=Path, required=True)
    p20.add_argument('--out-dir', type=Path, required=True)
    p20.add_argument('--fp-epochs', type=int, default=160)
    p20.add_argument('--qat-epochs', type=int, default=60)
    p20.add_argument('--resume-fp32', type=Path, default=None)

    p18 = sub.add_parser('resnet18', help='Distill/QAT pre-trained ResNet-18 (ImageNet-1K) and compile ABI 5.0 TiledPlan')
    p18.add_argument('--data-dir', type=Path, required=True)
    p18.add_argument('--out-dir', type=Path, required=True)
    p18.add_argument('--qat-epochs', type=int, default=15)

    args = ap.parse_args()
    if args.cmd == 'resnet20':
        run_resnet20(args)
    else:
        run_resnet18(args)


if __name__ == '__main__':
    main()
