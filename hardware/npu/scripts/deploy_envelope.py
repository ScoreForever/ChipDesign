#!/usr/bin/env python3
"""Deployability envelope: which common TinyML network families fit ABI 5.0.

Compile-only (no RTL). Builds parameterised reference architectures with
random INT4 weights, sweeps width multiplier and input resolution, and records
the first binding constraint reported by the real compiler (model bytes,
activation capacity, descriptor count, kernel/channel/K limits). Weights are
random, so only structure/capacity is evaluated, not accuracy.
"""
import argparse
import json
import math
from pathlib import Path
import random
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'software/host'))
import npu_program as npc


class Net:
    def __init__(self, shape, seed=1):
        self.r = random.Random(seed)
        self.nodes, self.cur, self.shape, self.n = [], 'x', list(shape), 0
        self.input = shape

    def _out(self):
        self.n += 1
        return f'n{self.n}'

    def conv(self, oc, k=3, s=1, depthwise=False, relu=True, pad=True):
        kh, kw = (k, k) if isinstance(k, int) else k
        h, w, c = self.shape
        oc = c if depthwise else oc
        pads = [kh // 2, kw // 2, (kh - 1) // 2, (kw - 1) // 2] if pad else [0] * 4
        kk = kh * kw * (1 if depthwise else c)
        out = self._out()
        node = dict(op='Conv', input=self.cur, output=out, channels=oc, kernel=[kh, kw], stride=s, pads=pads,
                    weights=[self.r.randrange(-8, 8) for _ in range(kk * oc)], bias=[0] * oc)
        if depthwise:
            node['groups'] = c
        self.nodes.append(node)
        sh, sw = (s, s) if isinstance(s, int) else s
        self.shape = [(h + pads[0] + pads[2] - kh) // sh + 1, (w + pads[1] + pads[3] - kw) // sw + 1, oc]
        self.cur = out
        self.requant(relu)

    def requant(self, relu=True):
        c = self.shape[2]
        out = self._out()
        node = dict(op='Requant', input=self.cur, output=out, multiplier=[0x4000] * c, shift=[-6] * c)
        if relu:
            node.update(dtype='u4')
        self.nodes.append(node)
        self.cur = out

    def pool(self, k=2, avg=False):
        out = self._out()
        h, w, c = self.shape
        kh, kw = (k, k) if isinstance(k, int) else k
        self.nodes.append(dict(op='AvgPool' if avg else 'MaxPool', input=self.cur, output=out,
                               kernel=[kh, kw], stride=[kh, kw]))
        self.shape = [h // kh, w // kw, c]
        self.cur = out
        if avg:
            self.requant()

    def gap(self):
        self.pool(k=self.shape[:2], avg=True)

    def add(self, other):
        out = self._out()
        self.nodes.append(dict(op='Add', input=self.cur, input2=other, output=out, weights=[1, 1]))
        self.cur = out
        self.requant()

    def fc(self, oc, requant=True):
        k = math.prod(self.shape)
        out = self._out()
        self.nodes.append(dict(op='FC', input=self.cur, output=out, channels=oc,
                               weights=[self.r.randrange(-8, 8) for _ in range(k * oc)], bias=[0] * oc))
        self.shape = [1, 1, oc]
        self.cur = out
        if requant:
            self.requant()

    def model(self):
        if self.nodes[-1]['op'] == 'Requant':
            # Final logits stay INT32: drop a trailing Requant after FC.
            if len(self.nodes) > 1 and self.nodes[-2]['op'] == 'FC':
                self.nodes.pop()
        return dict(input=dict(name='x', shape=self.input, dtype='u4'), nodes=self.nodes)


def ch(base, alpha):
    return max(1, int(round(base * alpha)))


def ds_cnn(alpha=1.0, res=1.0):          # MLPerf Tiny KWS (kernel 10x4 replaced by 7x3: ABI max 7)
    n = Net([max(7, int(49 * res)), max(3, int(10 * res)), 1])
    c = 8 * max(1, round(64 * alpha / 8))
    n.conv(c, k=[7, 3], s=2)
    for _ in range(4):
        n.conv(c, depthwise=True)
        n.conv(c, k=1)
    n.gap(); n.fc(12, requant=False)
    return n.model()


def mobilenet_v1(alpha=0.25, res=96):    # MLPerf Tiny VWW
    n = Net([res, res, 3])
    cfg = [(64, 1), (128, 2), (128, 1), (256, 2), (256, 1), (512, 2)] + [(512, 1)] * 5 + [(1024, 2), (1024, 1)]
    n.conv(8 * max(1, round(32 * alpha / 8)), s=2)
    for oc, s in cfg:
        n.conv(0, s=s, depthwise=True)
        n.conv(8 * max(1, round(oc * alpha / 8)), k=1)
    n.gap(); n.fc(2, requant=False)
    return n.model()


def resnet8(alpha=1.0, res=32):          # MLPerf Tiny image classification
    n = Net([res, res, 3])
    n.conv(ch(16, alpha))
    for oc, s in ((16, 1), (32, 2), (64, 2)):
        oc = ch(oc, alpha)
        skip, skip_shape = n.cur, list(n.shape)
        n.conv(oc, s=s); n.conv(oc, relu=False)
        main, main_shape = n.cur, list(n.shape)
        if s != 1 or skip_shape[2] != oc:
            # Projection shortcut (1x1, stride s) so both Add operands match.
            n.cur, n.shape = skip, skip_shape
            n.conv(oc, k=1, s=s, pad=False, relu=False)
            skip = n.cur
        n.cur, n.shape = main, main_shape
        n.add(skip)
    n.gap(); n.fc(10, requant=False)
    return n.model()


def autoencoder(alpha=1.0, k=640):       # MLPerf Tiny anomaly detection (FC)
    n = Net([1, 1, k])
    for oc in (128, 128, 128, 128, 8, 128, 128, 128, 128):
        n.fc(ch(oc, alpha))
    n.fc(k, requant=False)
    return n.model()


def lenet5(alpha=1.0, res=28):
    n = Net([res, res, 1])
    n.conv(ch(6, alpha), k=5, pad=False); n.pool()
    n.conv(ch(16, alpha), k=5, pad=False); n.pool()
    n.fc(ch(120, alpha)); n.fc(ch(84, alpha)); n.fc(10, requant=False)
    return n.model()


def tcn(alpha=1.0, length=128, ch_in=9):  # 1D temporal conv (HAR/ECG style)
    n = Net([1, length, ch_in])
    c = 8 * max(1, round(32 * alpha / 8))
    n.conv(c, k=[1, 5])
    for _ in range(3):
        n.conv(c, k=[1, 5], depthwise=True); n.conv(c, k=1); n.pool(k=[1, 2])
    n.gap(); n.fc(6, requant=False)
    return n.model()


FAMILIES = {
    'ds_cnn_kws': (ds_cnn, [dict(alpha=a, res=1.0) for a in (0.125, 0.25, 0.5, 0.75, 1.0)]),
    'mobilenet_v1_vww': (mobilenet_v1, [dict(alpha=a, res=r) for a in (0.125, 0.25) for r in (32, 48, 64, 96)]),
    'resnet8_cifar': (resnet8, [dict(alpha=a, res=r) for a in (0.125, 0.25, 0.5, 1.0) for r in (16, 32)]),
    'fc_autoencoder': (autoencoder, [dict(alpha=a, k=k) for a in (0.0625, 0.125, 0.25, 1.0) for k in (128, 640)]),
    'lenet5': (lenet5, [dict(alpha=a, res=28) for a in (0.25, 0.5, 1.0)]),
    'tcn_1d': (tcn, [dict(alpha=a, length=l) for a in (0.25, 0.5, 1.0) for l in (128, 256)]),
}


def params_of(model):
    return sum(len(n['weights']) for n in model['nodes'] if n['op'] in ('Conv', 'FC'))


def classify(msg):
    for key, pat in (('model_bytes', r'model needs'), ('activation', r'activation capacity|input exceeds'),
                     ('descriptors', r'too many physical'), ('shape', r'shape exceeds|kernel/stride'),
                     ('channels', r'channels')):
        if re.search(pat, msg):
            return key
    return 'other'


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    rows = []
    for fam, (fn, grid) in FAMILIES.items():
        for kw in grid:
            model = fn(**kw)
            row = dict(family=fam, **kw, params=params_of(model),
                       weight_bytes=(params_of(model) + 1) // 2,
                       layers=sum(n['op'] in ('Conv', 'FC') for n in model['nodes']))
            try:
                pkg = npc.compile_model(model, cols=8)
                row.update(fits=True, model_bytes=len(pkg.image), descriptors=pkg.manifest['op_count'],
                           activation_peak=pkg.manifest['activation_peak'],
                           activation_usable=16384 - len(pkg.image))
            except ValueError as e:
                row.update(fits=False, constraint=classify(str(e)), reason=str(e)[:160])
            rows.append(row)
            print(json.dumps(row))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=1) + '\n')


if __name__ == '__main__':
    main()
