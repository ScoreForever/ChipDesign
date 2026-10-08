"""在 GSCD 验证/测试集上评估导出的 INT8 部署包。"""

import argparse
import os
import sys

import numpy as np

SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOFTWARE_DIR)
sys.path.insert(0, os.path.join(SOFTWARE_DIR, 'train'))
import golden_model  # noqa: E402
import preprocess  # noqa: E402
from dataset import SpeechCommandsDataset, TASK_LABELS  # noqa: E402


def evaluate(pkg, dataset, labels):
    num_classes = len(labels)
    confusion = np.zeros((num_classes, num_classes), dtype=np.int64)
    for index in range(len(dataset)):
        feature, truth = dataset[index]
        input_int8 = preprocess.quantize_feature(feature.squeeze(0).numpy(), pkg['input_scale'])
        logits = golden_model.infer(pkg, input_int8)
        pred = golden_model.argmax_with_fc_compare(
            logits, pkg['fc_cmp_mult'], pkg['fc_cmp_shift']
        )
        confusion[int(truth), pred] += 1
        if (index + 1) % 250 == 0:
            print(f'evaluated {index + 1}/{len(dataset)}', flush=True)
    tp = np.diag(confusion).astype(np.float64)
    support = confusion.sum(axis=1).astype(np.float64)
    predicted = confusion.sum(axis=0).astype(np.float64)
    recall = np.divide(tp, support, out=np.zeros_like(tp), where=support > 0)
    precision = np.divide(tp, predicted, out=np.zeros_like(tp), where=predicted > 0)
    f1 = np.divide(2 * precision * recall, precision + recall,
                   out=np.zeros_like(tp), where=(precision + recall) > 0)
    return confusion, float(tp.sum() / confusion.sum()), float(f1.mean()), recall


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--split', choices=('validation', 'test'), default='test')
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()
    with np.load(args.package, allow_pickle=False) as loaded:
        pkg = {key: loaded[key] for key in loaded.files}
    labels = pkg['labels'].tolist()
    if pkg['model_head'].item() != 'flatten' or labels != TASK_LABELS['four_class']:
        raise ValueError('expected the four-class Flatten production package')
    if int(pkg['fc_input_features']) != 160 or tuple(pkg['fc_w'].shape) != (160, 8):
        raise ValueError('deployment package does not match the Flatten FC ABI')
    task = 'four_class'
    dataset = SpeechCommandsDataset(
        args.data_dir, args.split, seed=args.seed, augment=False, task=task
    )
    confusion, accuracy, macro_f1, recall = evaluate(pkg, dataset, labels)
    print(f'{args.split} accuracy={accuracy:.4f} macro_f1={macro_f1:.4f}')
    print('confusion matrix (rows=true, cols=pred):')
    print(confusion)
    print('per-class recall: ' + ', '.join(
        f'{labels[i]}={recall[i]:.4f}' for i in range(len(labels))
    ))


if __name__ == '__main__':
    main()
