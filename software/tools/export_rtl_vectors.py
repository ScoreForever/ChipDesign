"""把 Flatten INT8 部署包与真实语音导出为 RTL `$readmemh` 回归向量。"""

import argparse
import os
import sys

import numpy as np

SOFTWARE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOFTWARE_DIR)
sys.path.insert(0, os.path.join(SOFTWARE_DIR, 'train'))
import golden_model  # noqa: E402
import preprocess  # noqa: E402
from dataset import SpeechCommandsDataset  # noqa: E402


def write_hex(path, values, digits):
    mask = (1 << (digits * 4)) - 1
    with open(path, 'w', encoding='ascii', newline='\n') as handle:
        for value in values:
            handle.write(f'{int(value) & mask:0{digits}x}\n')


def packed_words(rows):
    for row in np.asarray(rows, dtype=np.int8):
        word = 0
        for lane, value in enumerate(row):
            word |= (int(value) & 0xff) << (8 * lane)
        yield word


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--output-dir', required=True)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--seed', type=int, default=0)
    args = parser.parse_args()

    if not 1 <= args.count <= 100:
        raise ValueError('--count must be between 1 and 100 for the RTL testbench')

    with np.load(args.package, allow_pickle=False) as loaded:
        pkg = {key: loaded[key] for key in loaded.files}
    if pkg['model_head'].item() != 'flatten':
        raise ValueError('RTL vectors require the Flatten baseline package')
    if int(pkg['num_classes']) != 4:
        raise ValueError('baseline real-vector regression requires four classes')
    if tuple(pkg['weight_mem'].shape) != (256, 8):
        raise ValueError('expected a 256x8 production weight image')
    if tuple(pkg['fc_w'].shape) != (160, 8):
        raise ValueError('expected Flatten FC weights with shape 160x8')

    dataset = SpeechCommandsDataset(
        args.data_dir, 'test', seed=args.seed, augment=False, task='four_class'
    )
    count = min(args.count, len(dataset))
    os.makedirs(args.output_dir, exist_ok=True)

    inputs = []
    logits = []
    labels = []
    for index in range(count):
        feature, label = dataset[index]
        input_int8 = preprocess.quantize_feature(
            feature.squeeze(0).numpy(), pkg['input_scale']
        )
        raw_logits = golden_model.infer(pkg, input_int8)
        inputs.extend(input_int8.reshape(-1).tolist())
        logits.extend(raw_logits.tolist())
        labels.append(int(label))

    write_hex(os.path.join(args.output_dir, 'weights.hex'),
              packed_words(pkg['weight_mem']), 16)
    write_hex(os.path.join(args.output_dir, 'inputs.hex'), inputs, 2)
    write_hex(os.path.join(args.output_dir, 'logits.hex'), logits, 8)
    write_hex(os.path.join(args.output_dir, 'labels.hex'), labels, 1)
    for prefix in ('conv1', 'conv2'):
        write_hex(os.path.join(args.output_dir, f'{prefix}_bias.hex'),
                  pkg[f'{prefix}_bias'], 8)
        write_hex(os.path.join(args.output_dir, f'{prefix}_mult.hex'),
                  pkg[f'{prefix}_mult'], 8)
        write_hex(os.path.join(args.output_dir, f'{prefix}_shift.hex'),
                  pkg[f'{prefix}_shift'], 2)
    fc_bias = np.zeros(8, dtype=np.int32)
    fc_bias[:len(pkg['fc_bias'])] = pkg['fc_bias']
    write_hex(os.path.join(args.output_dir, 'fc_bias.hex'), fc_bias, 8)
    write_hex(os.path.join(args.output_dir, 'count.hex'), [count], 8)
    print(f'saved {count} real-speech RTL vectors to {args.output_dir}')


if __name__ == '__main__':
    main()
