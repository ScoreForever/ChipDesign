"""用无增强训练集重新估计 BatchNorm 运行统计，并与原 checkpoint 对照。"""

import argparse
import os
import sys

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dataset import SpeechCommandsDataset, collate, TASK_LABELS  # noqa: E402
from model import TinyCNN8  # noqa: E402
from train import evaluate, checkpoint  # noqa: E402


def recalibrate(model, loader, device):
    batch_norms = [module for module in model.modules()
                   if isinstance(module, nn.BatchNorm2d)]
    old_momenta = [module.momentum for module in batch_norms]
    for module in batch_norms:
        module.reset_running_stats()
        module.momentum = None  # cumulative moving average over clean batches
    model.train()
    with torch.no_grad():
        for features, _ in loader:
            model(features.to(device))
    for module, momentum in zip(batch_norms, old_momenta):
        module.momentum = momentum
    model.eval()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--device', default='auto')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()

    device = ('cuda' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
    saved = torch.load(args.checkpoint, map_location='cpu')
    labels = list(saved.get('labels', TASK_LABELS['four_class']))
    num_classes = saved.get('num_classes', len(labels))
    task = 'four_class' if labels == TASK_LABELS['four_class'] else 'six_class'
    model = TinyCNN8(num_classes=num_classes,
                     head=saved.get('model_head', 'flatten')).to(device)
    model.load_state_dict(saved['state_dict'])
    loss_fn = nn.CrossEntropyLoss()

    train = SpeechCommandsDataset(args.data_dir, 'train', args.seed, augment=False, task=task)
    val = SpeechCommandsDataset(args.data_dir, 'validation', args.seed, augment=False, task=task)
    test = SpeechCommandsDataset(args.data_dir, 'test', args.seed, augment=False, task=task)
    train_loader = DataLoader(train, args.batch_size, False, collate_fn=collate)
    val_loader = DataLoader(val, args.batch_size, False, collate_fn=collate)
    test_loader = DataLoader(test, args.batch_size, False, collate_fn=collate)

    before = evaluate(model, val_loader, loss_fn, device)
    recalibrate(model, train_loader, device)
    after = evaluate(model, val_loader, loss_fn, device)
    test_metrics = evaluate(model, test_loader, loss_fn, device)
    print(f"validation before: acc={before['accuracy']:.4f} macro_f1={before['macro_f1']:.4f}")
    print(f"validation after:  acc={after['accuracy']:.4f} macro_f1={after['macro_f1']:.4f}")
    print(f"test after:        acc={test_metrics['accuracy']:.4f} macro_f1={test_metrics['macro_f1']:.4f}")
    print(test_metrics['confusion'])
    torch.save(checkpoint(saved['epoch'], model, after, labels,
                          saved.get('augmentation', 'strong')), args.output)
    print(f'saved recalibrated candidate to {args.output}')


if __name__ == '__main__':
    main()
