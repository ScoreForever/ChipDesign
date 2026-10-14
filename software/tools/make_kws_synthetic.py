"""Create a reproducible synthetic deployment package using the normal exporter."""
import argparse
import numpy as np
import torch
from export_model import (pack_conv1, pack_conv2, pack_fc, build_weight_memory,
                          quantize_weight)
from quantize import quantize_multiplier
from kws_workload_common import build_directory


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--output', default='build/kws_synthetic')
    p.add_argument('--seed', type=int, default=5)
    args = p.parse_args()
    out = build_directory(args.output)
    torch.manual_seed(args.seed)
    rng = np.random.default_rng(args.seed)
    w1, _ = quantize_weight(torch.randn(8, 1, 3, 3))
    w2, _ = quantize_weight(torch.randn(8, 8, 3, 3))
    wf, _ = quantize_weight(torch.randn(4, 160, 1, 1))
    c1, c2, fc = pack_conv1(w1), pack_conv2(w2), pack_fc(wf.reshape(4, 160))
    memory, b1, b2, bf, used = build_weight_memory(c1, c2, fc)
    pkg = dict(model_head=np.asarray('flatten'), num_classes=np.int32(4),
               labels=np.asarray(['yes', 'no', 'up', 'down']),
               fc_input_features=np.int32(160), input_scale=np.float32(0.1),
               act1_scale=np.float32(1), act3_scale=np.float32(1),
               fc_input_scale=np.float32(1),
               preprocess_frame_len=np.int32(800), preprocess_frame_hop=np.int32(800),
               preprocess_n_fft=np.int32(1024), preprocess_n_mels=np.int32(16),
               preprocess_n_frames=np.int32(20),
               conv1_w=c1, conv2_w=c2, fc_w=fc, weight_mem=memory,
               conv1_weight_base=np.int32(b1), conv2_weight_base=np.int32(b2),
               fc_weight_base=np.int32(bf), weight_words_used=np.int32(used))
    for prefix in ('conv1', 'conv2', 'fc_cmp'):
        size = 4 if prefix == 'fc_cmp' else 8
        scales = rng.uniform(0.0008, 0.008, size)
        encoded = [quantize_multiplier(float(s)) for s in scales]
        pkg[prefix + '_mult'] = np.array([m for m, _ in encoded], dtype=np.int32)
        pkg[prefix + '_shift'] = np.array([s for _, s in encoded], dtype=np.int8)
    for prefix, size in [('conv1', 8), ('conv2', 8), ('fc', 4)]:
        pkg[prefix + '_bias'] = rng.integers(-2000, 2001, size, dtype=np.int32)
    np.savez(out / 'package.npz', **pkg)
    np.save(out / 'inputs.npy', rng.integers(-128, 128, (32, 320), dtype=np.int8))
    print(f'Synthetic package: {out / "package.npz"}; inputs: {out / "inputs.npy"}')


if __name__ == '__main__':
    main()
