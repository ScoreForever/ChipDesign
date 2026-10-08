"""TinyCNN-8 训练脚本。

用法（使用 ml conda 环境）：
  python software/train/train.py --data-dir data/speech_commands --epochs 30

输出：software/train/runs/<run>/best.pt（最佳验证 macro-F1 checkpoint）与 last.pt。
"""

import os
import sys
import time
import argparse
import random

import numpy as np

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import TinyCNN8                    # noqa: E402
from dataset import (SpeechCommandsDataset, collate, TASK_LABELS, DEFAULT_TASK,
                     get_task_labels)  # noqa: E402


def evaluate(model, dl, loss_fn, device, num_classes=None):
    model.eval()
    loss_sum, n = 0.0, 0
    if num_classes is None:
        num_classes = model.fc.out_features
    confusion = np.zeros((num_classes, num_classes), dtype=np.int64)
    with torch.no_grad():
        for x, y in dl:
            x, y = x.to(device), y.to(device)
            logits = model(x)
            loss = loss_fn(logits, y)
            loss_sum += loss.item() * y.size(0)
            n += y.size(0)
            pred = logits.argmax(1)
            for truth, guess in zip(y.cpu().numpy(), pred.cpu().numpy()):
                confusion[int(truth), int(guess)] += 1
    true_positive = np.diag(confusion).astype(np.float64)
    support = confusion.sum(axis=1).astype(np.float64)
    predicted = confusion.sum(axis=0).astype(np.float64)
    recall = np.divide(true_positive, support, out=np.zeros_like(support), where=support > 0)
    precision = np.divide(true_positive, predicted, out=np.zeros_like(predicted), where=predicted > 0)
    denom = precision + recall
    f1 = np.divide(2 * precision * recall, denom, out=np.zeros_like(denom), where=denom > 0)
    return {
        'loss': loss_sum / max(n, 1),
        'accuracy': float(true_positive.sum() / max(confusion.sum(), 1)),
        'macro_f1': float(f1.mean()),
        'recall': recall,
        'confusion': confusion,
    }


def checkpoint(epoch, model, metrics, labels, augmentation):
    return {
        'epoch': epoch,
        'state_dict': model.state_dict(),
        'num_classes': len(labels),
        'labels': list(labels),
        'model_head': model.head,
        'augmentation': augmentation,
        'val_accuracy': metrics['accuracy'],
        'val_macro_f1': metrics['macro_f1'],
        'preprocess': {
            'sample_rate': 16000, 'clip_samples': 16000,
            'frame_len': 800, 'frame_hop': 800,
            'n_fft': 1024, 'n_mels': 16, 'n_frames': 20,
            'fmin_hz': 80.0, 'fmax_hz': 7600.0,
        },
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', required=True)
    p.add_argument('--epochs', type=int, default=30)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--lr', type=float, default=1e-3)
    p.add_argument('--weight-decay', type=float, default=1e-4)
    p.add_argument('--num-workers', type=int, default=0, help='沙箱/Windows 下必须为 0')
    p.add_argument('--device', default='auto')
    p.add_argument('--output', default=None)
    p.add_argument('--seed', type=int, default=0)
    p.add_argument('--task', choices=tuple(TASK_LABELS), default=DEFAULT_TASK)
    p.add_argument('--augmentation', choices=('strong', 'weak', 'none'), default='strong')
    p.add_argument('--head', choices=('gap', 'flatten'), default='flatten')
    args = p.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    device = args.device
    if device == 'auto':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    labels = get_task_labels(args.task)
    num_classes = len(labels)
    print(f'device={device} task={args.task} num_classes={num_classes} '
          f'augmentation={args.augmentation} head={args.head}', flush=True)

    task_tag = '4class' if args.task == 'four_class' else '6class'
    default_run = f'tinycnn8_{task_tag}_{args.head}_{args.augmentation}'
    out_dir = args.output or os.path.join('software', 'train', 'runs', default_run)
    os.makedirs(out_dir, exist_ok=True)

    train_ds = SpeechCommandsDataset(
        args.data_dir, 'train', seed=args.seed, task=args.task,
        augmentation=args.augmentation
    )
    val_ds = SpeechCommandsDataset(
        args.data_dir, 'validation', seed=args.seed, augment=False, task=args.task
    )
    test_ds = SpeechCommandsDataset(
        args.data_dir, 'test', seed=args.seed, augment=False, task=args.task
    )
    print(f'train={len(train_ds)} {train_ds.class_counts()}', flush=True)
    print(f'val={len(val_ds)} {val_ds.class_counts()}', flush=True)
    print(f'test={len(test_ds)} {test_ds.class_counts()}', flush=True)

    train_dl = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                          num_workers=args.num_workers, collate_fn=collate, drop_last=True)
    val_dl = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False,
                        num_workers=args.num_workers, collate_fn=collate)
    test_dl = DataLoader(test_ds, batch_size=args.batch_size, shuffle=False,
                         num_workers=args.num_workers, collate_fn=collate)

    model = TinyCNN8(num_classes=num_classes, head=args.head).to(device)
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f'trainable params={n_params}', flush=True)

    opt = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)
    loss_fn = nn.CrossEntropyLoss()

    best_f1 = -1.0
    for epoch in range(1, args.epochs + 1):
        train_ds.set_epoch(epoch)
        model.train()
        t0 = time.time()
        total, correct, loss_sum, n = 0, 0, 0.0, 0
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            logits = model(x)
            loss = loss_fn(logits, y)
            loss.backward()
            opt.step()
            total += y.size(0)
            correct += (logits.argmax(1) == y).sum().item()
            loss_sum += loss.item() * y.size(0)
            n += y.size(0)
        sched.step()
        train_acc = correct / total
        train_loss = loss_sum / n
        val = evaluate(model, val_dl, loss_fn, device, num_classes)
        print(f'epoch {epoch:3d}/{args.epochs}  '
              f'train loss {train_loss:.4f} acc {train_acc:.4f}  '
              f"val loss {val['loss']:.4f} acc {val['accuracy']:.4f} "
              f"macro_f1 {val['macro_f1']:.4f}  "
              f'{time.time() - t0:.1f}s', flush=True)
        if val['macro_f1'] > best_f1:
            best_f1 = val['macro_f1']
            torch.save(checkpoint(epoch, model, val, labels, args.augmentation),
                       os.path.join(out_dir, 'best.pt'))
    torch.save(checkpoint(args.epochs, model, val, labels, args.augmentation),
               os.path.join(out_dir, 'last.pt'))

    best = torch.load(os.path.join(out_dir, 'best.pt'), map_location=device)
    model.load_state_dict(best['state_dict'])
    test = evaluate(model, test_dl, loss_fn, device, num_classes)
    print(f"best epoch={best['epoch']} val macro_f1={best['val_macro_f1']:.4f}", flush=True)
    print(f"test loss={test['loss']:.4f} acc={test['accuracy']:.4f} "
          f"macro_f1={test['macro_f1']:.4f}", flush=True)
    print('test confusion matrix (rows=true, cols=pred):', flush=True)
    print(test['confusion'], flush=True)
    print('test per-class recall: ' + ', '.join(
        f"{labels[i]}={test['recall'][i]:.4f}" for i in range(num_classes)
    ), flush=True)
    print(f'saved checkpoints to {out_dir}', flush=True)


if __name__ == '__main__':
    main()
