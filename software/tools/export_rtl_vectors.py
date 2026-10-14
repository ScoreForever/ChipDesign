"""把 Flatten INT8 部署包与真实语音导出为 RTL `$readmemh` 回归向量。"""

import argparse
import hashlib
import json
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
    pools1, pools2, samples = [], [], []
    # Round-robin the four classes; 100 samples gives 25 per class. Manifest
    # selection is already deterministic and uses only the official test split.
    by_label = [[i for i, item in enumerate(dataset.manifest) if item[-1] == c]
                for c in range(4)]
    indices = [by_label[i % 4][i // 4] for i in range(count)]
    for index in indices:
        feature, label = dataset[index]
        input_int8 = preprocess.quantize_feature(
            feature.squeeze(0).numpy(), pkg['input_scale']
        )
        result = golden_model.infer_with_intermediates(pkg, input_int8)
        raw_logits = result['logits']
        inputs.extend(input_int8.reshape(-1).tolist())
        logits.extend(raw_logits.tolist())
        labels.append(int(label))
        pools1.extend(result['pool1'].reshape(-1).tolist())
        pools2.extend(result['pool2'].reshape(-1).tolist())
        wav_path = dataset.manifest[index][0]
        samples.append({'index': len(samples), 'label': int(label),
                        'wav': os.path.relpath(wav_path, args.data_dir).replace(os.sep, '/'),
                        'wav_sha256': hashlib.sha256(open(wav_path, 'rb').read()).hexdigest()})

    write_hex(os.path.join(args.output_dir, 'weights.hex'),
              packed_words(pkg['weight_mem']), 16)
    write_hex(os.path.join(args.output_dir, 'inputs.hex'), inputs, 2)
    write_hex(os.path.join(args.output_dir, 'logits.hex'), logits, 8)
    write_hex(os.path.join(args.output_dir, 'labels.hex'), labels, 1)
    write_hex(os.path.join(args.output_dir, 'pool1.hex'), pools1, 2)
    write_hex(os.path.join(args.output_dir, 'pool2.hex'), pools2, 2)
    write_hex(os.path.join(args.output_dir, 'fc_cmp_mult.hex'), pkg['fc_cmp_mult'], 8)
    write_hex(os.path.join(args.output_dir, 'fc_cmp_shift.hex'), pkg['fc_cmp_shift'], 2)
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
    files = {name: hashlib.sha256(open(os.path.join(args.output_dir, name), 'rb').read()).hexdigest()
             for name in sorted(os.listdir(args.output_dir)) if name.endswith('.hex')}
    manifest = {'kind': 'real_speech', 'dataset': 'Speech Commands v0.02', 'split': 'test',
                'model_head': 'flatten', 'labels': ['yes', 'no', 'up', 'down'],
                'count': count, 'seed': args.seed,
                'package_sha256': hashlib.sha256(open(args.package, 'rb').read()).hexdigest(),
                'checkpoint_sha256': str(pkg.get('checkpoint_sha256', 'unknown')),
                'training_epoch': int(pkg.get('training_epoch', -1)),
                'source_sha256': {os.path.relpath(path, SOFTWARE_DIR).replace(os.sep, '/'):
                    hashlib.sha256(open(path, 'rb').read()).hexdigest() for path in
                    (os.path.abspath(__file__), golden_model.__file__, preprocess.__file__)},
                'samples': samples, 'files_sha256': files}
    with open(os.path.join(args.output_dir, 'manifest.json'), 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, indent=2)
        handle.write('\n')
    print(f'saved {count} real-speech RTL vectors to {args.output_dir}')


if __name__ == '__main__':
    main()
