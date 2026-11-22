#!/usr/bin/env python3
"""Step 3B: Joint Multi-Exit QAT + Teacher KD on Full CIFAR-10 (50k train / 10k test) for ABI 6.0."""
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
torch.set_num_threads(40)

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

def round_ste(x): return (x.round() - x).detach() + x
def grad_scale(x, scale): return (x - x * scale).detach() + x * scale

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
        g = 1.0 / math.sqrt(max(1.0, x.numel() / max(1, x.shape[0]) * self.hi))
        s = grad_scale(self.scale.abs().clamp(min=1e-6), g)
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
        g = 1.0 / math.sqrt(max(1.0, self.weight[0].numel() * 7.0))
        ws = grad_scale(self.w_scale.abs().clamp(min=1e-6), g).view(-1, 1, 1, 1)
        wq = torch.clamp(round_ste(self.weight / ws), -8.0, 7.0) * ws
        return F.conv2d(x, wq, self.bias, self.stride, self.padding, groups=self.groups)

class QLinear(nn.Module):
    def __init__(self, lin, per_tensor=False):
        super().__init__()
        self.per_tensor = per_tensor
        self.weight = nn.Parameter(lin.weight.detach().clone())
        self.bias = nn.Parameter(lin.bias.detach().clone() if lin.bias is not None else torch.zeros(lin.out_features))
        if per_tensor:
            self.w_scale = nn.Parameter(self.weight.detach().abs().max().clamp(min=1e-6) / 7.0)
        else:
            self.w_scale = nn.Parameter(self.weight.detach().abs().amax(1).clamp(min=1e-6) / 7.0)

    def forward(self, x):
        if self.per_tensor:
            ws = self.w_scale.abs().clamp(min=1e-6)
            wq = torch.clamp(round_ste(self.weight / ws), -8.0, 7.0) * ws
        else:
            ws = self.w_scale.abs().clamp(min=1e-6).view(-1, 1)
            wq = torch.clamp(round_ste(self.weight / ws), -8.0, 7.0) * ws
        return F.linear(x, wq, self.bias)

    @torch.no_grad()
    def forward_int32(self, x_float, a_scale):
        x_int = torch.clamp(torch.round(x_float / a_scale), 0, 15)
        ws = self.w_scale.abs().clamp(min=1e-6)
        if not self.per_tensor:
            ws = self.weight.abs().max().clamp(min=1e-6) / 7.0
        w_int = torch.clamp(torch.round(self.weight / ws), -8, 7)
        b_int = torch.clamp(torch.round(self.bias / (a_scale * ws)), -(1 << 17), (1 << 17) - 1)
        return F.linear(x_int, w_int, b_int).to(torch.int64)

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

    def forward(self, x):
        ident = x if self.downsample is None else self.downsample(x)
        out = F.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return F.relu(out + ident)

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

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = self.layer3(self.layer2(self.layer1(x)))
        return self.fc(F.avg_pool2d(x, 8).flatten(1))

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

class QExitHead(nn.Module):
    """Hardware-exact INT4/U4 exit head with per-tensor FC scale for exact INT32 Top1-Top2 margin."""
    def __init__(self, in_ch, mid_ch=32, pre_pool=1):
        super().__init__()
        self.pre_pool = pre_pool
        conv = nn.Conv2d(in_ch, mid_ch, 3, stride=2, padding=1, bias=True)
        nn.init.kaiming_normal_(conv.weight, nonlinearity='relu')
        nn.init.zeros_(conv.bias)
        self.qconv = QConv2d(conv)
        self.aq = ActQuant(0, 15)
        lin = nn.Linear(mid_ch, 10)
        nn.init.normal_(lin.weight, std=0.05)
        nn.init.zeros_(lin.bias)
        self.qfc = QLinear(lin, per_tensor=True)

    def forward(self, x):
        if self.pre_pool > 1:
            x = F.avg_pool2d(x, self.pre_pool)
        x = self.aq(F.relu(self.qconv(x)))
        g = F.adaptive_avg_pool2d(x, (1, 1)).flatten(1)
        return self.qfc(g)

    @torch.no_grad()
    def forward_int32(self, x):
        if self.pre_pool > 1:
            x = F.avg_pool2d(x, self.pre_pool)
        x = self.aq(F.relu(self.qconv(x)))
        g = F.adaptive_avg_pool2d(x, (1, 1)).flatten(1)
        return self.qfc.forward_int32(g, self.aq.scale.abs().clamp(min=1e-6))

class JointEarlyExitQResNet20(nn.Module):
    def __init__(self, fp):
        super().__init__()
        self.in_q = ActQuant(-8, 7)
        self.conv1 = QConv2d(fold_conv_bn(fp.conv1, fp.bn1))
        self.aq1 = ActQuant(0, 15)
        self.s1 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer1])
        self.s2 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer2])
        self.s3 = nn.Sequential(*[QBasicBlock(b) for b in fp.layer3])
        self.gap_aq = ActQuant(0, 15)
        self.fc = QLinear(fp.fc, per_tensor=True)
        # Two hardware-friendly early-exit heads
        self.exit1 = QExitHead(16, mid_ch=32, pre_pool=2)
        self.exit2 = QExitHead(32, mid_ch=32, pre_pool=1)

    def forward(self, x):
        x = self.aq1(F.relu(self.conv1(self.in_q(x))))
        f1 = self.s1(x)
        l1 = self.exit1(f1)
        f2 = self.s2(f1)
        l2 = self.exit2(f2)
        f3 = self.s3(f2)
        g3 = self.gap_aq(F.adaptive_avg_pool2d(f3, (1, 1)))
        l3 = self.fc(g3.flatten(1))
        return l1, l2, l3

    @torch.no_grad()
    def forward_int32_exits(self, x):
        x = self.aq1(F.relu(self.conv1(self.in_q(x))))
        f1 = self.s1(x)
        l1_i32 = self.exit1.forward_int32(f1)
        f2 = self.s2(f1)
        l2_i32 = self.exit2.forward_int32(f2)
        f3 = self.s3(f2)
        g3 = self.gap_aq(F.adaptive_avg_pool2d(f3, (1, 1)))
        l3_i32 = self.fc.forward_int32(g3.flatten(1), self.gap_aq.scale.abs().clamp(min=1e-6))
        return l1_i32, l2_i32, l3_i32

def kd_loss(logits, teacher_logits, y, T=3.0, alpha=0.5):
    ce = F.cross_entropy(logits, y)
    kl = F.kl_div(F.log_softmax(logits / T, dim=1), F.softmax(teacher_logits / T, dim=1), reduction='batchmean') * (T * T)
    return (1.0 - alpha) * ce + alpha * kl

def main():
    t0 = time.time()
    xs, ys = [], []
    for i in range(1, 6):
        xi, yi = load_cifar_batch(ROOT / f'cifar10/data_batch_{i}')
        xs.append(xi); ys.append(yi)
    x_train = torch.cat(xs, dim=0)
    y_train = torch.cat(ys, dim=0)
    x_test, y_test = load_cifar_batch(ROOT / 'cifar10/test_batch')
    print(f"Loaded FULL CIFAR-10: train={len(x_train)}, test={len(x_test)} ({time.time()-t0:.1f}s)", flush=True)

    teacher = ResNet20FP32().eval()
    teacher.load_state_dict(torch.load(ROOT / 'cifar10_resnet20-4118986f.pt', map_location='cpu', weights_only=True))

    # Precompute FP32 teacher logits (orig + hflip) for fast KD
    t_kd = time.time()
    @torch.no_grad()
    def get_teacher(x, bs=512):
        outs = [teacher(x[i:i+bs]) for i in range(0, len(x), bs)]
        return torch.cat(outs, dim=0)
    t_orig = get_teacher(x_train)
    t_flip = get_teacher(torch.flip(x_train, dims=[3]))
    print(f"Precomputed FP32 teacher logits (orig+flip) in {time.time()-t_kd:.1f}s", flush=True)

    model = JointEarlyExitQResNet20(teacher)
    # Calibrate quantizers
    cal_idx = np.random.default_rng(0).choice(len(x_train), size=256, replace=False)
    model.eval()
    with torch.no_grad():
        model(x_train[cal_idx])

    @torch.no_grad()
    def evaluate_int32(bs=512):
        model.eval()
        l1s, l2s, l3s = [], [], []
        for i in range(0, len(x_test), bs):
            l1, l2, l3 = model.forward_int32_exits(x_test[i:i+bs])
            l1s.append(l1); l2s.append(l2); l3s.append(l3)
        l1 = torch.cat(l1s).numpy()
        l2 = torch.cat(l2s).numpy()
        l3 = torch.cat(l3s).numpy()
        y = y_test.numpy()
        return l1, l2, l3, float((l1.argmax(1)==y).mean()), float((l2.argmax(1)==y).mean()), float((l3.argmax(1)==y).mean())

    # Stage A: 2-epoch head warmup (backbone frozen)
    for p in model.parameters(): p.requires_grad = False
    for p in list(model.exit1.parameters()) + list(model.exit2.parameters()): p.requires_grad = True
    opt_w = torch.optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=3e-3, weight_decay=1e-4)
    print("\n--- Stage A: Warmup Exit Heads (2 epochs) ---", flush=True)
    for ep in range(2):
        te = time.time()
        model.train()
        perm = torch.randperm(len(x_train))
        for i in range(0, len(x_train), 256):
            idx = perm[i:i+256]
            flip = (torch.rand(len(idx)) < 0.5)
            xb = x_train[idx].clone()
            xb[flip] = torch.flip(xb[flip], dims=[3])
            tb = torch.where(flip.unsqueeze(1), t_flip[idx], t_orig[idx])
            yb = y_train[idx]
            opt_w.zero_grad()
            l1, l2, _ = model(xb)
            loss = kd_loss(l1, tb, yb) + kd_loss(l2, tb, yb)
            loss.backward()
            opt_w.step()
        _, _, _, a1, a2, a3 = evaluate_int32()
        print(f"  Warmup ep {ep+1}/2 ({time.time()-te:.1f}s): E1={a1*100:.2f}%, E2={a2*100:.2f}%, Full={a3*100:.2f}%", flush=True)

    # Stage B: 10-epoch Joint QAT of FULL INT4 Backbone + Exit-1 + Exit-2
    for p in model.parameters(): p.requires_grad = True
    epochs = 10
    opt = torch.optim.AdamW(model.parameters(), lr=6e-4, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=1e-5)
    print(f"\n--- Stage B: Joint Multi-Exit QAT + KD ({epochs} epochs on 50k CIFAR-10) ---", flush=True)
    for ep in range(epochs):
        te = time.time()
        model.train()
        perm = torch.randperm(len(x_train))
        for i in range(0, len(x_train), 256):
            idx = perm[i:i+256]
            flip = (torch.rand(len(idx)) < 0.5)
            xb = x_train[idx].clone()
            xb[flip] = torch.flip(xb[flip], dims=[3])
            tb = torch.where(flip.unsqueeze(1), t_flip[idx], t_orig[idx])
            yb = y_train[idx]
            opt.zero_grad()
            l1, l2, l3 = model(xb)
            loss = 0.30 * kd_loss(l1, tb, yb) + 0.40 * kd_loss(l2, tb, yb) + 1.00 * kd_loss(l3, tb, yb)
            loss.backward()
            opt.step()
        sched.step()
        _, _, _, a1, a2, a3 = evaluate_int32()
        print(f"  Joint QAT ep {ep+1:2d}/{epochs} ({time.time()-te:.1f}s): E1={a1*100:.2f}%, E2={a2*100:.2f}%, Full={a3*100:.2f}%", flush=True)

    # Final hardware-exact INT32 Pareto sweep
    l1_i32, l2_i32, l3_i32, acc1, acc2, base_acc = evaluate_int32()
    y_np = y_test.numpy()

    def top1_top2_margin(logits):
        s = np.sort(logits, axis=1)
        return s[:, -1] - s[:, -2], logits.argmax(axis=1)

    m1, p1 = top1_top2_margin(l1_i32)
    m2, p2 = top1_top2_margin(l2_i32)
    p3 = l3_i32.argmax(axis=1)

    CYC_BASE, XDMA_BASE, JTAG_BASE = 887_710, 246.33, 4264.02
    E1_HEAD_CYC, E2_HEAD_CYC = 8_500, 15_000
    E1_HEAD_XDMA, E2_HEAD_XDMA = 4.5, 5.5
    C_E1 = 339_759 + E1_HEAD_CYC
    C_E2 = 618_374 + E1_HEAD_CYC + E2_HEAD_CYC
    C_E3 = CYC_BASE + E1_HEAD_CYC + E2_HEAD_CYC
    X_E1 = 85.46 + E1_HEAD_XDMA + 4.8
    X_E2 = 135.48 + E1_HEAD_XDMA + E2_HEAD_XDMA + 9.0
    X_E3 = XDMA_BASE + E1_HEAD_XDMA + E2_HEAD_XDMA
    J_E1 = JTAG_BASE * 0.36 + 35.0
    J_E2 = JTAG_BASE * 0.68 + 90.0
    J_E3 = JTAG_BASE + 90.0

    th1_grid = sorted(set([int(np.percentile(m1, q)) for q in np.linspace(5, 100, 30)] + [10**9]))
    th2_grid = sorted(set([int(np.percentile(m2, q)) for q in np.linspace(5, 100, 30)] + [10**9]))
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

    all_pts.sort(key=lambda p: (p['avg_cycles'], -p['acc']))
    pareto = []
    best_acc = -1.0
    for pt in all_pts:
        if pt['acc'] > best_acc + 1e-5:
            pareto.append(pt)
            best_acc = pt['acc']

    print(f"\n=== Joint QAT Full INT4 ResNet-20: E1={acc1*100:.2f}%, E2={acc2*100:.2f}%, Full={base_acc*100:.2f}% ===")
    print(f"Dual-Exit Joint-QAT Pareto Frontier ({len(pareto)} points):")
    print(f"{'th1':>6s} {'th2':>6s} | {'E1%':>5s} {'E2%':>5s} {'Full%':>5s} | {'Acc%':>6s} {'Delta':>6s} | {'AvgCyc':>8s} {'CycSave':>7s} | {'XDMA ms':>7s} {'Speedup':>7s}")
    print("-" * 92)
    step = max(1, len(pareto) // 16)
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
        'dataset': 'CIFAR-10 full (50,000 train / 10,000 test)',
        'training': '2ep head warmup + 10ep Joint Multi-Exit INT4 QAT + FP32 Teacher KD',
        'baseline_joint_qat_full': {
            'acc': base_acc, 'cycles': CYC_BASE, 'xdma_ms': XDMA_BASE, 'jtag_ms': JTAG_BASE
        },
        'exit_heads': {
            'exit1_standalone_int4_acc': acc1, 'exit1_overhead_cycles': E1_HEAD_CYC,
            'exit2_standalone_int4_acc': acc2, 'exit2_overhead_cycles': E2_HEAD_CYC,
        },
        'dual_exit_pareto': pareto,
    }
    (ROOT / 'early_exit_joint_qat_pareto.json').write_text(json.dumps(out, indent=2) + '\n')
    torch.save(model.state_dict(), ROOT / 'resnet20_joint_qat_early_exit.pt')
    print(f"\nSaved {ROOT / 'early_exit_joint_qat_pareto.json'} and checkpoint.", flush=True)

if __name__ == '__main__':
    main()
