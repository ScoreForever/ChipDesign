"""Shared deployment/input validation for the bare-metal workload tools."""
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'software'))


def build_directory(value):
    path = Path(value).resolve()
    if not path.is_relative_to(ROOT / 'build') or path == ROOT / 'build':
        raise ValueError('output must be a subdirectory of the worktree build/')
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_package(path):
    with np.load(path, allow_pickle=False) as f:
        pkg = {k: f[k] for k in f.files}
    if pkg['model_head'].item() != 'flatten' or int(pkg['num_classes']) != 4:
        raise ValueError('requires the four-class Flatten deployment ABI')
    shapes = {'weight_mem': (256, 8), 'conv1_w': (9, 8),
              'conv2_w': (72, 8), 'fc_w': (160, 8), 'fc_bias': (4,),
              'fc_cmp_mult': (4,), 'fc_cmp_shift': (4,)}
    for layer in ('conv1', 'conv2'):
        for field in ('bias', 'mult', 'shift'):
            shapes[f'{layer}_{field}'] = (8,)
    for name, shape in shapes.items():
        array = pkg[name]
        if array.shape != shape or array.dtype.kind not in 'iu':
            raise ValueError(f'{name}: expected integer array {shape}')
        low, high = (-128, 127) if name.endswith('_w') or name == 'weight_mem' else (
            (-31, 31) if name.endswith('shift') else (-2**31, 2**31 - 1))
        if np.any(array < low) or np.any(array > high):
            raise ValueError(f'{name}: outside [{low}, {high}]')
        if name.endswith('mult') and np.any(array < 0):
            raise ValueError(f'{name}: multipliers must be nonnegative')
    packed = np.concatenate([pkg['conv1_w'], pkg['conv2_w'], pkg['fc_w']])
    if not np.array_equal(pkg['weight_mem'][:241], packed):
        raise ValueError('weight_mem disagrees with golden-model weights')
    for key, expected in [('conv1_weight_base', 0), ('conv2_weight_base', 9),
                          ('fc_weight_base', 81), ('weight_words_used', 241)]:
        if key in pkg and int(pkg[key]) != expected:
            raise ValueError(f'{key}: expected {expected}')
    return pkg


def load_inputs(path):
    path = Path(path)
    if path.suffix == '.npy':
        data = np.load(path, allow_pickle=False)
        if data.dtype != np.int8 or data.size % 320:
            raise ValueError('inputs.npy must contain INT8 frames of 320 bytes')
    else:
        values = [int(token, 16) for token in path.read_text().split()]
        if any(v < 0 or v > 255 for v in values) or len(values) % 320:
            raise ValueError('inputs.hex must contain 320 two-digit bytes per frame')
        data = np.array(values, dtype=np.uint8).view(np.int8)
    return data.reshape(-1, 320)
