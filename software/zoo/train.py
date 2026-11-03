#!/usr/bin/env python3
"""Train one zoo model: float training, BN folding, INT4 QAT, export, and
bit-exact integer scoring of the exported NPU model.

Model selection uses the validation split only; the test split is scored
once on the selected export. Outputs <out>/<name>/{model.json,result.json}.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import random
import sys
import time
from pathlib import Path

import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'software/host'), str(ROOT / 'software')]
from npu_program import compile_model, reference  # noqa: E402
import npu_fastref  # noqa: E402
from zoo import data as datasets  # noqa: E402
from zoo.models import SPECS  # noqa: E402
from zoo.qat import Net, annotate_shapes, export  # noqa: E402


def augment(name, x):
    """Light, label-preserving augmentation on a GPU batch [N,C,H,W]."""
    n = x.shape[0]
    if name in ('mnist', 'fashion', 'gtsrb'):
        pad = 2 if name != 'gtsrb' else 3
        xp = F.pad(x, (pad,) * 4, value=float(x.min()))
        dy, dx = random.randint(0, 2 * pad), random.randint(0, 2 * pad)
        x = xp[:, :, dy:dy + x.shape[2], dx:dx + x.shape[3]]
        if name == 'fashion' and random.random() < 0.5:
            x = x.flip(3)
        if name == 'gtsrb':
            x = x * (0.8 + 0.4 * torch.rand(n, 1, 1, 1, device=x.device)) + 0.2 * (torch.rand(n, 1, 1, 1, device=x.device) - 0.5)
    elif name in ('har', 'ecg'):
        shift = random.randint(-8, 8)
        x = torch.roll(x, shift, 3)
        x = x * (0.9 + 0.2 * torch.rand(n, 1, 1, 1, device=x.device))
    elif name == 'kws':
        shift = random.randint(-5, 5)
        x = torch.roll(x, shift, 2)
        x = x + 0.05 * torch.randn_like(x)
    return x


def batches(x, y, size, shuffle):
    order = torch.randperm(len(y), device=y.device) if shuffle else torch.arange(len(y), device=y.device)
    for i in range(0, len(y), size):
        idx = order[i:i + size]
        yield x[idx], y[idx]


def float_accuracy(net, x, y):
    net.eval()
    correct = 0
    with torch.no_grad():
        for xb, yb in batches(x, y, 2048, False):
            correct += (net(xb).argmax(1) == yb).sum().item()
    return correct / len(y)


def quantize_input(x, scale):
    """Float NCHW -> INT4 NHWC frames, as the host does before upload."""
    return torch.clamp(torch.round(x / scale), -8, 7).long().permute(0, 2, 3, 1)


def int_accuracy(model, x, y, device, per_class=None):
    frames = quantize_input(x, model['metadata']['input_scale'])
    preds = []
    for i in range(0, len(y), 1024):
        out = npu_fastref.run(model, frames[i:i + 1024].to(device), device)
        preds.append(out.reshape(out.shape[0], -1).argmax(1).cpu())
    pred = torch.cat(preds)
    acc = (pred == y.cpu()).float().mean().item()
    if per_class is None:
        return acc, pred
    recall = [((pred == c) & (y.cpu() == c)).sum().item() / max(1, (y.cpu() == c).sum().item()) for c in range(per_class)]
    return acc, pred, recall


def fit(net, data, name, epochs, lr, batch, device, qat, log):
    params = [p for p in net.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=lr, weight_decay=0 if qat else 5e-4)
    steps = epochs * math.ceil(len(data['train'][1]) / batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, lr, total_steps=steps, pct_start=0.15 if not qat else 0.05)
    x, y = data['train']
    best, best_state = -1, None
    weights = data.get('class_weights')
    for epoch in range(epochs):
        net.train()
        for xb, yb in batches(x, y, batch, True):
            xb = augment(name, xb)
            loss = F.cross_entropy(net(xb), yb, weight=weights, label_smoothing=0.05)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            sched.step()
        fake = float_accuracy(net, *data['val'])
        if qat:
            model = export(net, name)
            acc, _ = int_accuracy(model, *data['val'], device)
        else:
            acc = fake
        if acc > best:
            best, best_state = acc, copy.deepcopy(net.state_dict())
        log(f'{"qat" if qat else "fp"} epoch {epoch + 1}/{epochs} loss {loss.item():.3f} val {acc:.4f}'
            + (f' (fake-quant {fake:.4f})' if qat else '') + f' best {best:.4f}')
    net.load_state_dict(best_state)
    return best


def check_hardware(model, frames, logical_count=16):
    """Bit-exact fastref vs pure-Python reference; capacity for both builds."""
    out_name = model['nodes'][-1]['output']
    batch = npu_fastref.run(model, frames[:logical_count])
    for i in range(min(logical_count, len(frames))):
        ref = reference(model, frames[i].flatten().tolist())[out_name]
        if ref != batch[i].flatten().tolist():
            raise AssertionError(f'fastref differs from reference on frame {i}')
    report = {}
    for cols in (8, 4):
        pkg = compile_model(model, cols=cols)
        report[f'c{cols}'] = dict(model_bytes=pkg.manifest['model_bytes'], activation_peak=pkg.manifest['activation_peak'],
                                  op_count=pkg.manifest['op_count'], groups=pkg.manifest['groups'])
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('name', choices=sorted(SPECS))
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--fp-epochs', type=int, default=40)
    ap.add_argument('--qat-epochs', type=int, default=30)
    ap.add_argument('--lr', type=float, default=3e-3)
    ap.add_argument('--qat-lr', type=float, default=5e-4)
    ap.add_argument('--batch', type=int, default=256)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--balanced', action='store_true', help='inverse-frequency class weights')
    args = ap.parse_args()
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    out = args.out / args.name
    out.mkdir(parents=True, exist_ok=True)
    logfile = (out / 'train.log').open('w')

    def log(msg):
        print(msg, flush=True)
        logfile.write(msg + '\n')
        logfile.flush()
    started = time.time()
    raw = datasets.LOADERS[args.name](args.data)
    data = {k: (raw[k][0].to(device), raw[k][1].to(device)) for k in ('train', 'val', 'test')}
    cfg = SPECS[args.name]
    if args.balanced:
        counts = torch.bincount(data['train'][1], minlength=cfg['classes']).float()
        data['class_weights'] = (counts.sum() / (counts * len(counts))).sqrt().to(device)
    net = Net(cfg['spec'], cfg['input']).to(device)
    annotate_shapes(net)
    log(f'{args.name}: {net.weight_count()} weights, train {len(data["train"][1])} val {len(data["val"][1])} test {len(data["test"][1])}')
    fp_val = fit(net, data, args.name, args.fp_epochs, args.lr, args.batch, device, False, log)
    fp_test = float_accuracy(net, *data['test'])
    log(f'float val {fp_val:.4f} test {fp_test:.4f}')
    net.to_qat()
    net.train()
    with torch.no_grad():  # calibrate activation scales on one batch
        net(data['train'][0][:2048])
    qat_val = fit(net, data, args.name, args.qat_epochs, args.qat_lr, args.batch, device, True, log)
    model = export(net, args.name, dict(classes=[str(c) for c in raw['classes']], preprocess=raw['preprocess'],
                                        dataset=args.name))
    test_acc, pred, recall = int_accuracy(model, *data['test'], device, per_class=cfg['classes'])
    frames = quantize_input(data['test'][0][:16].cpu(), model['metadata']['input_scale'])
    hw = check_hardware(model, frames)
    model['metadata'].update(accuracy_validated=True, int4_test_accuracy=test_acc, int4_val_accuracy=qat_val)
    (out / 'model.json').write_text(json.dumps(model) + '\n')
    torch.save(net.state_dict(), out / 'qat_state.pt')
    torch.save(dict(frames=quantize_input(data['test'][0].cpu(), model['metadata']['input_scale']).to(torch.int8),
                    labels=data['test'][1].cpu().to(torch.int16)), out / 'test_frames.pt')
    result = dict(name=args.name, weights=net.weight_count(), float_val=fp_val, float_test=fp_test,
                  int4_val=qat_val, int4_test=test_acc, int4_test_recall=recall,
                  int4_test_balanced=sum(recall) / len(recall), hardware=hw,
                  test_samples=len(data['test'][1]), seconds=time.time() - started, args={k: str(v) for k, v in vars(args).items()})
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    log(json.dumps(result))


if __name__ == '__main__':
    main()
