"""Course candidate workloads (PKU full-stack AI chip final project).

Four small INT4 networks matching the published candidate scenarios, built
from deterministic random weights. They measure generality and performance;
they are not trained models (KWS optionally uses the trained TinyCNN NPZ).
Unsupported layers are lowered here with generic graph rewrites only:
  * Conv1D(k=3)  -> Conv 3x3 on an H=1 tensor, zero top/bottom kernel rows
  * GlobalAvgPool -> FC with constant 0/1 weights followed by a Requant(1/L)
  * Argmax        -> host side
"""
from __future__ import annotations
import math
import random
from npu_program import reference


def _calibrate(model, frame, target=6):
    """Pick per-layer Requant shifts so typical outputs use the INT4 range."""
    for i, node in enumerate(model['nodes']):
        if node['op'] != 'Requant' or node.get('_fixed'):
            continue
        probe = dict(input=model['input'], nodes=[{k: v for k, v in n.items() if k != '_fixed'} for n in model['nodes'][:i]])
        try:
            values = reference(probe, frame)[node['input']]
        except ValueError:
            break  # an unsupported layer; the compiler reports it later

        peak = max(1, max(abs(v) for v in values))
        # out ~= x * 0.5 * 2**shift ; choose shift so peak maps near target.
        node['shift'] = [max(-31, min(31, round(math.log2(2 * target / peak))))]
    for node in model['nodes']:
        node.pop('_fixed', None)
    return model


def _conv(r, src, dst, ic, oc, kernel=3, pads=(1, 1, 1, 1), stride=1):
    k = kernel * kernel * ic
    return dict(op='Conv', input=src, output=dst, channels=oc, kernel=kernel, pads=list(pads), stride=stride,
                weights=[r.randrange(-8, 8) for _ in range(oc * k)], bias=[r.randrange(-64, 65) for _ in range(oc)])


def _conv1d(r, src, dst, ic, oc):
    """1-D convolution (k=3, same padding) expressed as a 3x3 Conv on H=1."""
    weights = []
    for _ in range(oc):
        taps = [[r.randrange(-8, 8) for _ in range(ic)] for _ in range(3)]
        zero = [[0] * ic for _ in range(3)]
        weights.append([zero, taps, zero])  # [OC][KH][KW][IC]
    return dict(op='Conv', input=src, output=dst, channels=oc, kernel=3, pads=[1, 1, 1, 1],
                weights=weights, bias=[r.randrange(-64, 65) for _ in range(oc)])


def _fc(r, src, dst, k, oc):
    return dict(op='FC', input=src, output=dst, channels=oc,
                weights=[r.randrange(-8, 8) for _ in range(oc * k)], bias=[r.randrange(-64, 65) for _ in range(oc)])


def _requant(src, dst, channels=1):
    return dict(op='Requant', input=src, output=dst, multiplier=[0x4000] * channels, shift=[0])


def _global_avg_pool(src, dst, positions, channels):
    """GAP as FC(K=L*C -> C) with 0/1 weights plus an exact 1/L Requant."""
    weights = [[1 if k % channels == o else 0 for k in range(positions * channels)] for o in range(channels)]
    fc = dict(op='FC', input=src, output=dst + '_sum', channels=channels, weights=weights, bias=[0] * channels)
    mult = min(2**15 - 1, round(2**15 / positions))
    rq = dict(op='Requant', input=dst + '_sum', output=dst, multiplier=[mult], shift=[0], _fixed=True)
    return [fc, rq]


def ecg(seed=1):
    """Scenario 1: 128-point ECG, Conv1D/ReLU/MaxPool1D/GAP/FC, 2 classes."""
    r = random.Random(seed)
    nodes = [_conv1d(r, 'x', 'c1', 1, 8), _requant('c1', 'q1'), dict(op='ReLU', input='q1', output='r1'),
             dict(op='MaxPool', input='r1', output='p1', kernel=[1, 2], stride=[1, 2]),
             _conv1d(r, 'p1', 'c2', 8, 8), _requant('c2', 'q2'), dict(op='ReLU', input='q2', output='r2'),
             dict(op='MaxPool', input='r2', output='p2', kernel=[1, 2], stride=[1, 2]),
             *_global_avg_pool('p2', 'gap', 32, 8),
             _fc(r, 'gap', 'y', 8, 2)]
    frame = [r.randrange(-8, 8) for _ in range(128)]
    return dict(input=dict(name='x', shape=[1, 128, 1]), nodes=nodes), frame


def mnist(seed=2):
    """Scenario 2: 28x28 digit, Conv2D/ReLU/MaxPool/Flatten/FC, 10 classes."""
    r = random.Random(seed)
    nodes = [_conv(r, 'x', 'c1', 1, 4), _requant('c1', 'q1'), dict(op='ReLU', input='q1', output='r1'),
             dict(op='MaxPool', input='r1', output='p1'),
             _conv(r, 'p1', 'c2', 4, 4), _requant('c2', 'q2'), dict(op='ReLU', input='q2', output='r2'),
             dict(op='MaxPool', input='r2', output='p2'),
             _fc(r, 'p2', 'y', 7 * 7 * 4, 10)]
    frame = [r.randrange(-8, 8) for _ in range(28 * 28)]
    return dict(input=dict(name='x', shape=[28, 28, 1]), nodes=nodes), frame


def kws(seed=3, npz=None):
    """Scenario 3: 20x16 acoustic features, TinyCNN-8 topology, 4 keywords."""
    r = random.Random(seed)
    if npz is not None:
        from npu_program import from_tinycnn_npz
        return from_tinycnn_npz(npz), [r.randrange(-8, 8) for _ in range(320)]
    nodes = [_conv(r, 'x', 'c1', 1, 8), _requant('c1', 'q1'), dict(op='ReLU', input='q1', output='r1'),
             dict(op='MaxPool', input='r1', output='p1'),
             _conv(r, 'p1', 'c2', 8, 8), _requant('c2', 'q2'), dict(op='ReLU', input='q2', output='r2'),
             dict(op='MaxPool', input='r2', output='p2'),
             _fc(r, 'p2', 'y', 5 * 4 * 8, 4)]
    frame = [r.randrange(-8, 8) for _ in range(320)]
    return dict(input=dict(name='x', shape=[20, 16, 1]), nodes=nodes), frame


def har(seed=4):
    """Scenario 4: 64-d IMU feature MLP 64->32->6 (Argmax on host)."""
    r = random.Random(seed)
    nodes = [_fc(r, 'x', 'h', 64, 32), _requant('h', 'q'), dict(op='ReLU', input='q', output='a'),
             _fc(r, 'a', 'y', 32, 6)]
    frame = [r.randrange(-8, 8) for _ in range(64)]
    return dict(input=dict(name='x', shape=[1, 1, 64]), nodes=nodes), frame


def course_models(npz=None):
    """Yield (name, model, frame) with calibrated Requant shifts."""
    for name, build in (('ecg', ecg), ('mnist', mnist), ('kws', lambda: kws(npz=npz)), ('har', har)):
        model, frame = build()
        if not (name == 'kws' and npz is not None):
            _calibrate(model, frame)
        yield name, model, frame


def argmax(values):
    return max(range(len(values)), key=lambda i: values[i])
