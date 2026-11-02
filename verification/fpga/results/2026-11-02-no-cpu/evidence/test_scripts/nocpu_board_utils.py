"""ABI 2.1 tensor encoding used by the physical-board stress checks."""
import struct


def encode_tensor(values, dtype):
    values = list(values)
    if dtype not in ('i8', 'i32'):
        raise ValueError('ABI 2.1 supports i8/i32 tensors')
    return struct.pack('<' + ('b' if dtype == 'i8' else 'i') * len(values), *values)
