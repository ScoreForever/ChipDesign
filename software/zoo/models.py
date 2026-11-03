"""Hardware-friendly network specs for the INT4 NPU model zoo.

Constraints the specs respect (ABI 4.0, 4x8 array, 8 KiB model and
activation SRAMs, 16 descriptors): kernels up to 7x7 with stride 1/2,
depthwise channels a multiple of 8, fused 2x2 or 1x2 max pooling after a
convolution, global average pooling, residual Add of two INT4 tensors.
"""

def ds(out, k=(3, 3), pool=None, s=(1, 1)):
    """Depthwise k x k (same padding) then pointwise 1x1 to `out` channels."""
    kh, kw = k
    p = [(kh - 1) // 2, (kw - 1) // 2, kh // 2, kw // 2]
    return [dict(type='conv', dw=True, k=list(k), s=list(s), p=p),
            dict(type='conv', out=out, k=[1, 1], pool=pool)]


def res(name, ch, k=(3, 3)):
    """Residual block: x + PW(DW(x)), ReLU after the Add."""
    kh, kw = k
    p = [(kh - 1) // 2, (kw - 1) // 2, kh // 2, kw // 2]
    return [dict(type='save', name=name),
            dict(type='conv', dw=True, k=list(k), p=p),
            dict(type='conv', out=ch, k=[1, 1], relu=False),
            dict(type='add', name=name)]


SPECS = {
    'mnist': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=8, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *ds(16, pool='max2'),
        *ds(32),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    'fashion': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=16, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r1', 16),
        *ds(32, pool='max2'),
        *res('r2', 32),
        *ds(64),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    'kws': dict(input=(49, 10, 1), classes=12, spec=[
        dict(type='conv', out=32, k=[7, 3], s=[2, 2], p=[3, 1, 3, 1]),
        *ds(32), *ds(32), *ds(32), *ds(32),
        dict(type='gap'),
        dict(type='fc', out=12, last=True)]),
    'har': dict(input=(1, 128, 9), classes=6, spec=[
        dict(type='conv', out=32, k=[1, 5], p=[0, 2, 0, 2], pool='max1x2'),
        *ds(32, k=(1, 5), pool='max1x2'),
        *ds(64, k=(1, 5), pool='max1x2'),
        dict(type='gap'),
        dict(type='fc', out=6, last=True)]),
    'ecg': dict(input=(1, 128, 1), classes=5, spec=[
        dict(type='conv', out=16, k=[1, 7], p=[0, 3, 0, 3], pool='max1x2'),
        *res('r1', 16, k=(1, 5)),
        *ds(32, k=(1, 5), pool='max1x2'),
        *res('r2', 32, k=(1, 5)),
        *ds(32, k=(1, 5), pool='max1x2'),
        dict(type='gap'),
        dict(type='fc', out=5, last=True)]),
    'gtsrb': dict(input=(32, 32, 3), classes=43, spec=[
        dict(type='conv', out=16, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *ds(32, pool='max2'),
        *res('r1', 32),
        *ds(48, pool='max2'),
        dict(type='gap'),
        dict(type='fc', out=43, last=True)]),
    'fashion_l': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=24, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r1', 24),
        *ds(48, pool='max2'),
        *res('r2', 48),
        *ds(56),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    'kws_l': dict(input=(49, 10, 1), classes=12, spec=[
        dict(type='conv', out=48, k=[7, 3], s=[2, 2], p=[3, 1, 3, 1]),
        *ds(48), *ds(48), *ds(48),
        dict(type='gap'),
        dict(type='fc', out=12, last=True)]),
    'mnist_l': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=16, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *ds(32, pool='max2'),
        *res('r1', 32),
        *ds(64),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    'kws_m': dict(input=(49, 10, 1), classes=12, spec=[
        dict(type='conv', out=40, k=[7, 3], s=[2, 2], p=[3, 1, 3, 1]),
        *ds(40), *ds(40), *ds(40), *ds(40),
        dict(type='gap'),
        dict(type='fc', out=12, last=True)]),
    'gtsrb_l': dict(input=(32, 32, 3), classes=43, spec=[
        dict(type='conv', out=24, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *ds(48, pool='max2'),
        *res('r1', 48),
        *ds(56, pool='max2'),
        dict(type='gap'),
        dict(type='fc', out=43, last=True)]),
    'fashion_xl': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=24, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r1', 24),
        *ds(48, pool='max2'),
        *res('r2', 48),
        *ds(72),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    # Float-only teacher for distillation (not deployed; no SRAM limit).
    'fashion_teacher': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=96, k=[3, 3], p=[1, 1, 1, 1]),
        dict(type='conv', out=96, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r1', 96),
        dict(type='conv', out=192, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r2', 192),
        dict(type='conv', out=256, k=[3, 3], p=[1, 1, 1, 1]),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    # Plain convolutions (no depthwise) for the hardest task.
    'fashion_cnn': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=16, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        dict(type='conv', out=32, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        dict(type='conv', out=32, k=[3, 3], p=[1, 1, 1, 1]),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
    'fashion_cnn2': dict(input=(28, 28, 1), classes=10, spec=[
        dict(type='conv', out=16, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        dict(type='conv', out=32, k=[3, 3], p=[1, 1, 1, 1], pool='max2'),
        *res('r1', 32),
        *ds(64),
        dict(type='gap'),
        dict(type='fc', out=10, last=True)]),
}
