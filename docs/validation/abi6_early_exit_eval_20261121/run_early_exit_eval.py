#!/usr/bin/env python3
"""Step 3: Hardware-Exact ABI 6.0 Early-Exit Pareto Evaluation on CIFAR-10 ResNet-20 (INT4)."""
import json
import math
import pickle
import time
from pathlib import Path
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

torch.manual_seed(0)
torch.set_num_threads(32)

ROOT = Path('/data/home2/guoyiz/ChipDesign/abi6_early_exit_eval')
MEAN = np.array([0.4914, 0.4822, 0.4465], dtype=np.float32).reshape(1, 3, 1, 1)
STD = np.array([0.2470, 0.2435, 0.2616], dtype=np.float32).reshape(1, 3, 1, 1)

def load_cifar_batch(path: Path):
    with open(path, 'rb') as f:
        d = pickle.load(f, encoding='bytes')
    x = d[b'data'].reshape(-1, 3, 32, 32).astype(np.float32) / 255.0
    x = (x - MEAN) / STD
    y = np.array(d[b'labels'], dtype=np.int64)
    return torch.from_numpy(x), torch.from_numpy(y)

# Minimal exact copy of ResNet20FP32 + QResNet from software/zoo/train_resnet.py
def round_ste(x): return (x.round() - x).detach() + x

class ActQuant(nn.Module):
    def __init__(self, lo=0, hi=15):
        super().__init__()
        self.lo, self.hi = int(lo), int(hi)
        self.scale = nn.Parameter(torch.tensor(0.1))
        self.register_buffer('initialized', torch.tensor(False))

    @torch.no_grad()
    def calibrate(self, x):
        v = x.detach().float().reshape(-1)
        if v.numel() > 500_000:
            v = v[::max(1, v.numel() // 500_000)]
        top = v.abs().max().clamp(min=1e-5)
        best_s, best_err = top / self.hi, float('inf')
        for frac in torch.linspace(0.15, 1.0, 64):
            s = (top * frac / self.hi).clamp(min=1e-6)
            q = torch.clamp(torch.round(v / s), self.lo, self.hi) * s
            err = float(((q - v) ** 2).mean().item())
            if err < best_err:
                best_err, best_s = err, s
        self.scale.copy_(best_s)
        self.initialized.fill_(True)

    def forward(self, x):
        if not bool(self.initialized.item()):
            self.calibrate(x)
        s = self.scale.abs().clamp(min=1e-6)
        return torch.clamp(round_ste(x / s), self.lo, self.hi) * s

class QConv2d(nn.Module):
    def __init__(self, conv):
        super().__init__()
        self.stride, self.padding, self.groups = conv.stride, conv.padding, conv.groups
        self.weight = nn.Parameter(conv.weight.detach().clone())
        bias = conv.bias.detach().clone() if conv.bias is not None else torch.zeros(conv.out_channels)
        self.bias = nn.Parameter(bias)
        self.w_scale = nn.Parameter(self.weight.detach().abs().flatten(1).amax(1).clamp(min=1e-6) / 7.0)

    def forward(self, x):
        ws = self.w_scale.abs().clamp(min=1e-6).view(-1, 1, 1, 1)
        wq = torch.clamp(round_ste(self.weight / ws), -8.0, 7.0) * ws
        return F.conv2d(x, wq, self.bias, self.stride, self.padding, groups=self.groups)

class QLinear(nn.Module):
    def __init__(self, lin):
        super().__init__()
        self.weight = nn.Parameter(lin.weight.detach().clone())
        self.bias = nn.Parameter(lin.bias.detach().clone() if lin.bias is not None else torch.zeros(lin.out_features))
        self.w_scale = nn.Parameter(self.weight.detach().abs().amax(1).clamp(min=1e-6) / 7.0)

    def forward(self, x):
        ws = self.w_scale.abs().clamp(min=1e-6).view(-1, 1)
        wq = torch.clamp(round_ste(self.weight / ws), -8.0, 7.0) * ws
        return F.linear(x, wq, self.bias)

def fold_conv_bn(conv, bn):
    fused = nn.Conv2d(conv.in_channels, conv.out_channels, conv.kernel_size,
                      stride=conv.stride, padding=conv.padding, groups=conv.groups, bias=True)
    with torch.no_grad():
        inv_std = bn.weight.float() / torch.sqrt(bn.running_var.float() + bn.eps)
        b = conv.bias.float() if conv.bias is not None else torch.zeros(conv.out_channels)
        fused.weight.copy_(conv.weight.float() * inv_std.view(-1, 1, 1, 1))
        fused.bias.copy_((b - bn.running_mean.float()) * inv_std + bn.bias.float())
    return fused

class CifarBasicBlock(nn.Module):
    def __init__(self, in_ch, out_ch, stride=1):
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

class ResNet20FP32(nn.Module):
    def __init__(self, num_classes=10):
        super().__init__()
        self.conv1 = nn.Conv2d(3, 16, 3, stride=1, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(16)
        self.layer1 = self._make_stage(16, 16, 3, 1)
        self.layer2 = self._make_stage(16, 32, 3, 2)
        self.layer3 = self._make_stage(32, 64, 3, 2)
        self.fc = nn.Linear(64, num_classes)

    @staticmethod
    def _make_stage(in_ch, out_ch, blocks, stride):
        return nn.Sequential(CifarBasicBlock(in_ch, out_ch, stride),
                             *[CifarBasicBlock(out_ch, out_ch, 1) for _ in range(1, blocks)])

class QBasicBlock(nn.Module):
    def __init__(self, b):
        super().__init__()
        self.conv1 = QConv2d(fold_conv_bn(b.conv1, b.bn1))
        self.aq1 = ActQuant(0, 15)
        self.conv2 = QConv2d(fold_conv_bn(b.conv2, b.bn2))
        self.aq2 = ActQuant(-8, 7)
        self.downsample = QConv2d(fold_conv_bn(b.downsample[0], b.downsample[1])) if b.downsample else None
        self.aq_ds = ActQuant(-8, 7) if b.downsample else None
        self.aq_out = ActQuant(0, 15)

    def forward(self, x):
        ident = x if self.downsample is None else self.aq_ds(self.downsample(x))
        out = self.aq2(self.conv2(self.aq1(F.relu(self.conv1(x)))))
        return self.aq_out(F.relu(out + ident))

class QResNet20(nn.Module):
    def __init__(self, fp):
        super().__init__()
        self.in_q = ActQuant(-8, 7)
        self.conv1 = QConv2d(fold_conv_bn(fp.conv1, fp.bn1))
        self.aq1 = ActQuant(0, 15)
        self.s1 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer1])
        self.s2 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer2])
        self.s3 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer3])
        self.gap_aq = ActQuant(0, 15)
        self.fc = QLinear(fp.fc)

    def forward_stages(self, x):
        x = self.aq1(F.relu(self.conv1(self.in_q(x))))
        f1 = self.s1(x)
        f2 = self.s2(f1)
        f3 = self.s3(f2)
        g3 = self.gap_aq(F.adaptive_avg_pool2d(f3, (1, 1)))
        l3 = self.fc(g3.flatten(1))
        return f1, f2, l3

class ConvExitHead(nn.Module):
    """Hardware-friendly ABI 5.0/6.0 early-exit head: AvgPool -> Conv3x3(s=2) -> ReLU(U4) -> GAP -> FC10."""
    def __init__(self, in_ch, mid_ch=32, pre_pool=2):
        super().__init__()
        self.pre_pool = pre_pool
        self.conv = nn.Conv2d(in_ch, mid_ch, 3, stride=2, padding=1, bias=False)
        self.bn = nn.BatchNorm2d(mid_ch)
        self.fc = nn.Linear(mid_ch, 10)

    def forward(self, x):
        if self.pre_pool > 1:
            x = F.avg_pool2d(x, self.pre_pool)
        x = F.relu(self.bn(self.conv(x)))
        x = F.adaptive_avg_pool2d(x, (1, 1)).flatten(1)
        return self.fc(x)

class QConvExitHead(nn.Module):
    """INT4/U4 hardware-quantized version of ConvExitHead outputting exact scaled INT32 logits."""
    def __init__(self, fp_head: ConvExitHead, in_scale: float):
        super().__init__()
        self.pre_pool = fp_head.pre_pool
        self.in_scale = in_scale
        self.qconv = QConv2d(fold_conv_bn(fp_head.conv, fp_head.bn))
        self.aq = ActQuant(0, 15)
        self.qfc = QLinear(fp_head.fc)

    def forward_int32(self, x_u4_float):
        x = F.avg_pool2d(x_u4_float, self.pre_pool) if self.pre_pool > 1 else x_u4_float
        x = self.aq(F.relu(self.qconv(x)))
        g = F.adaptive_avg_pool2d(x, (1, 1)).flatten(1)
        # Convert to exact INT32 logits (using a common per-tensor output scale for fair margin comparison)
        # On hardware: FC accumulates INT4_w * U4_a + INT18_bias directly into INT32.
        # Use uniform weight scale across the 10 classes so INT32 logits preserve exact class ordering & margin!
        a_scale = self.aq.scale.abs().clamp(min=1e-6)
        g_int = torch.clamp(torch.round(g / a_scale), 0, 15)
        w_max = self.qfc.weight.abs().max().clamp(min=1e-6)
        w_scale = w_max / 7.0
        w_int = torch.clamp(torch.round(self.qfc.weight / w_scale), -8, 7)
        b_int = torch.clamp(torch.round(self.qfc.bias / (a_scale * w_scale)), -(1 << 17), (1 << 17) - 1)
        return F.linear(g_int, w_int, b_int).to(torch.int64)

def main():
    t0 = time.time()
    x1, y1 = load_cifar_batch(ROOT / 'cifar10/data_batch_1')
    x2, y2 = load_cifar_batch(ROOT / 'cifar10/data_batch_2')
    x_train = torch.cat([x1, x2], dim=0)
    y_train = torch.cat([y1, y2], dim=0)
    x_test, y_test = load_cifar_batch(ROOT / 'cifar10/test_batch')
    print(f"Loaded CIFAR-10: train={len(x_train)}, test={len(x_test)} ({time.time()-t0:.1f}s)", flush=True)

    fp = ResNet20FP32().eval()
    fp.load_state_dict(torch.load(ROOT / 'cifar10_resnet20-4118986f.pt', map_location='cpu', weights_only=True))
    qnet = QResNet20(fp).eval()

    # Exact same 128-sample calibration as prepare.py
    cal_idx = np.random.default_rng(0).choice(len(x_train), size=128, replace=False)
    with torch.no_grad():
        qnet.forward_stages(x_train[cal_idx])
    print("INT4 backbone calibrated.", flush=True)

    # Extract quantized intermediate features for train (20k) and test (10k)
    @torch.no_grad()
    def extract(x, bs=256):
        f1s, f2s, l3s = [], [], []
        for i in range(0, len(x), bs):
            f1, f2, l3 = qnet.forward_stages(x[i:i+bs])
            f1s.append(f1); f2s.append(f2); l3s.append(l3)
        return torch.cat(f1s), torch.cat(f2s), torch.cat(l3s)

    t1 = time.time()
    tr_f1, tr_f2, tr_l3 = extract(x_train)
    te_f1, te_f2, te_l3 = extract(x_test)
    base_acc = float((te_l3.argmax(1) == y_test).float().mean().item())
    print(f"Extracted features in {time.time()-t1:.1f}s | Full INT4 ResNet-20 test acc = {base_acc*100:.2f}%", flush=True)

    # Train lightweight ConvExitHeads on frozen INT4 intermediate features (distilled from backbone + labels)
    def train_exit_head(name, f_tr, f_te, in_ch, mid_ch, pre_pool, epochs=12):
        head = ConvExitHead(in_ch, mid_ch=mid_ch, pre_pool=pre_pool)
        opt = torch.optim.Adam(head.parameters(), lr=3e-3, weight_decay=1e-4)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)
        bs = 256
        for ep in range(epochs):
            head.train()
            perm = torch.randperm(len(f_tr))
            for i in range(0, len(f_tr), bs):
                idx = perm[i:i+bs]
                opt.zero_grad()
                out = head(f_tr[idx])
                loss = F.cross_entropy(out, y_train[idx])
                loss.backward()
                opt.step()
            sched.step()
        head.eval()
        with torch.no_grad():
            fp_acc = float((head(f_te).argmax(1) == y_test).float().mean().item())
            qhead = QConvExitHead(head, in_scale=1.0).eval()
            qhead.forward_int32(f_tr[:512])  # calibrate ActQuant
            te_logits_i32 = qhead.forward_int32(f_te)
            int4_acc = float((te_logits_i32.argmax(1) == y_test).float().mean().item())
        print(f"  {name}: FP32 acc={fp_acc*100:.2f}% -> Hardware INT4/INT32 acc={int4_acc*100:.2f}%", flush=True)
        return te_logits_i32.numpy(), int4_acc

    l1_i32, acc1 = train_exit_head("Exit-1 (after Stage 1, 16->32)", tr_f1, te_f1, 16, 32, pre_pool=2, epochs=15)
    l2_i32, acc2 = train_exit_head("Exit-2 (after Stage 2, 32->32)", tr_f2, te_f2, 32, 32, pre_pool=1, epochs=15)
    l3_np = te_l3.numpy()
    y_np = y_test.numpy()

    # Compute exact ABI 6.0 OP_EXIT Top1-Top2 integer margin for Exit-1 and Exit-2
    def top1_top2_margin(logits):
        s = np.sort(logits, axis=1)
        return s[:, -1] - s[:, -2], logits.argmax(axis=1)

    m1, p1 = top1_top2_margin(l1_i32)
    m2, p2 = top1_top2_margin(l2_i32)
    p3 = l3_np.argmax(axis=1)

    # Real VC707 measured costs per stage + analytical Exit Head cost:
    # Stage 1: 339,759 cyc, 85.46 ms XDMA | Exit-1 head (Pool2x2 + Conv3x3(16->32,s2,8x8 out) + GAP + FC10 + OP_EXIT):
    #   Conv MACs = 8*8*32*16*9 = 294,912 -> @64 MAC/cyc ~4,608 cyc, w/ overhead ~8,500 cyc (~4.5 ms XDMA, ~2.8 KB model)
    # Stage 2: 618,374 cyc, 135.48 ms XDMA | Exit-2 head (Conv3x3(32->32,s2,8x8 out) + GAP + FC10 + OP_EXIT):
    #   Conv MACs = 8*8*32*32*9 = 589,824 -> ~15,000 cyc (~5.5 ms XDMA, ~5.2 KB model)
    # Full Stage 3: 887,710 cyc, 246.33 ms XDMA (total wall median), 4264.02 ms EGo1 JTAG
    CYC_BASE = 887_710
    XDMA_BASE = 246.33
    JTAG_BASE = 4264.02

    E1_HEAD_CYC, E2_HEAD_CYC = 8_500, 15_000
    E1_HEAD_XDMA, E2_HEAD_XDMA = 4.5, 5.5
    # Cumulative cost when exiting at Exit-1, Exit-2, or Full (Stage 3):
    # Note: if sample does NOT exit at E1, it already paid E1_HEAD; if not at E2, it also paid E2_HEAD.
    C_E1 = 339_759 + E1_HEAD_CYC
    C_E2 = 618_374 + E1_HEAD_CYC + E2_HEAD_CYC
    C_E3 = CYC_BASE + E1_HEAD_CYC + E2_HEAD_CYC

    # Scaled VC707 XDMA wall ms (proportional to stage cumulative XDMA + head)
    X_E1 = 85.46 + E1_HEAD_XDMA + 4.8   # + host overhead share
    X_E2 = 135.48 + E1_HEAD_XDMA + E2_HEAD_XDMA + 9.0
    X_E3 = XDMA_BASE + E1_HEAD_XDMA + E2_HEAD_XDMA

    # Scaled EGo1 JTAG ms (proportional to cumulative bytes)
    J_E1 = JTAG_BASE * 0.36 + 35.0
    J_E2 = JTAG_BASE * 0.68 + 90.0
    J_E3 = JTAG_BASE + 90.0

    # 1) Single-Exit (Exit-2 only, no Exit-1 overhead!) Pareto sweep
    print("\n=== Single-Exit (Exit-2 after Stage 2) Pareto Sweep ===", flush=True)
    e2_only_points = []
    C_E2_only = 618_374 + E2_HEAD_CYC
    C_E3_only = CYC_BASE + E2_HEAD_CYC
    X_E2_only = 135.48 + E2_HEAD_XDMA + 9.0
    X_E3_only = XDMA_BASE + E2_HEAD_XDMA
    for q in np.linspace(0, 100, 41):
        th = int(np.percentile(m2, q))
        take2 = m2 >= th
        pred = np.where(take2, p2, p3)
        acc = float((pred == y_np).mean())
        r2 = float(take2.mean())
        avg_cyc = r2 * C_E2_only + (1 - r2) * C_E3_only
        avg_xdma = r2 * X_E2_only + (1 - r2) * X_E3_only
        e2_only_points.append({
            'th2': th, 'exit2_rate': r2, 'acc': acc, 'acc_drop_pct': (acc - base_acc) * 100,
            'avg_cycles': avg_cyc, 'cycle_savings_pct': (1 - avg_cyc / CYC_BASE) * 100,
            'avg_xdma_ms': avg_xdma, 'xdma_speedup': XDMA_BASE / avg_xdma,
        })

    # 2) Dual-Exit (Exit-1 + Exit-2) Grid Sweep -> Extract Pareto Frontier
    th1_grid = sorted(set([int(np.percentile(m1, q)) for q in np.linspace(5, 100, 25)] + [10**9]))
    th2_grid = sorted(set([int(np.percentile(m2, q)) for q in np.linspace(5, 100, 25)] + [10**9]))
    all_pts = []
    for t1 in th1_grid:
        take1 = m1 >= t1
        for t2 in th2_grid:
            take2 = (~take1) & (m2 >= t2)
            take3 = (~take1) & (~take2)
            pred = np.where(take1, p1, np.where(take2, p2, p3))
            acc = float((pred == y_np).mean())
            r1, r2, r3 = float(take1.mean()), float(take2.mean()), float(take3.mean())
            avg_cyc = r1 * C_E1 + r2 * C_E2 + r3 * C_E3
            avg_xdma = r1 * X_E1 + r2 * X_E2 + r3 * X_E3
            avg_jtag = r1 * J_E1 + r2 * J_E2 + r3 * J_E3
            all_pts.append({
                'th1': t1, 'th2': t2,
                'exit1_rate': r1, 'exit2_rate': r2, 'full_rate': r3,
                'acc': acc, 'acc_drop_pct': (acc - base_acc) * 100,
                'avg_cycles': avg_cyc, 'cycle_savings_pct': (1 - avg_cyc / CYC_BASE) * 100,
                'avg_xdma_ms': avg_xdma, 'xdma_speedup': XDMA_BASE / avg_xdma,
                'avg_jtag_ms': avg_jtag, 'jtag_speedup': JTAG_BASE / avg_jtag,
            })

    # Extract Pareto frontier (maximize acc, minimize avg_cycles)
    all_pts.sort(key=lambda p: (p['avg_cycles'], -p['acc']))
    pareto = []
    best_acc = -1.0
    for pt in all_pts:
        if pt['acc'] > best_acc + 1e-5:
            pareto.append(pt)
            best_acc = pt['acc']

    # Print key operating points
    print(f"\nBaseline Full INT4 ResNet-20: acc={base_acc*100:.2f}%, cycles={CYC_BASE:,}, VC707 XDMA={XDMA_BASE:.2f}ms, EGo1 JTAG={JTAG_BASE:.1f}ms")
    print(f"Dual-Exit Pareto Frontier ({len(pareto)} points):")
    print(f"{'th1':>6s} {'th2':>6s} | {'E1%':>5s} {'E2%':>5s} {'Full%':>5s} | {'Acc%':>6s} {'Delta':>6s} | {'AvgCyc':>8s} {'CycSave':>7s} | {'XDMA ms':>7s} {'Speedup':>7s}")
    print("-" * 92)
    # Show ~12 evenly spaced representative Pareto points
    step = max(1, len(pareto) // 14)
    shown = pareto[::step]
    if pareto[-1] not in shown:
        shown.append(pareto[-1])
    for pt in shown:
        t1s = "INF" if pt['th1'] >= 10**9 else str(pt['th1'])
        t2s = "INF" if pt['th2'] >= 10**9 else str(pt['th2'])
        print(f"{t1s:>6s} {t2s:>6s} | {pt['exit1_rate']*100:5.1f} {pt['exit2_rate']*100:5.1f} {pt['full_rate']*100:5.1f} | "
              f"{pt['acc']*100:6.2f} {pt['acc_drop_pct']:+5.2f}% | {pt['avg_cycles']:8.0f} {pt['cycle_savings_pct']:+6.1f}% | "
              f"{pt['avg_xdma_ms']:7.2f} {pt['xdma_speedup']:6.2f}x")

    out = {
        'dataset': 'CIFAR-10 test (10,000 samples)',
        'baseline': {
            'acc': base_acc, 'cycles': CYC_BASE, 'xdma_ms': XDMA_BASE, 'jtag_ms': JTAG_BASE
        },
        'exit_heads': {
            'exit1_standalone_int4_acc': acc1, 'exit1_overhead_cycles': E1_HEAD_CYC,
            'exit2_standalone_int4_acc': acc2, 'exit2_overhead_cycles': E2_HEAD_CYC,
        },
        'single_exit2_sweep': e2_only_points,
        'dual_exit_pareto': pareto,
    }
    (ROOT / 'early_exit_pareto.json').write_text(json.dumps(out, indent=2) + '\n')
    print(f"\nSaved {ROOT / 'early_exit_pareto.json'}", flush=True)

if __name__ == '__main__':
    main()
