"""Exact, hardware-motivated rewrites of exported models.

pad_input_channels: an RGB (IC=3) first convolution forms K groups of four
that straddle pixels, so most groups need several scalar activation reads.
Appending zero input channels (with zero weights) up to four makes every
K group one aligned pixel, read in a single access. Results are identical.
"""
from __future__ import annotations
import copy


def pad_input_channels(model, to=4):
    h, w, c = model['input']['shape']
    if c >= to or c == 1:
        return model, c
    out = copy.deepcopy(model)
    out['input']['shape'] = [h, w, to]
    name = out['input']['name']
    for node in out['nodes']:
        if node['input'] != name:
            continue
        if node['op'] != 'Conv' or node.get('groups', 1) != 1:
            raise ValueError('input padding needs a dense first convolution')
        kh, kw = (node['kernel'], node['kernel']) if isinstance(node['kernel'], int) else node['kernel']
        padded = []
        for row in node['weights']:
            flat = [v for v in row] if not isinstance(row[0], list) else [v for r in row for v in r]
            taps = [flat[i:i + c] for i in range(0, len(flat), c)]
            padded.append([v for tap in taps for v in tap + [0] * (to - c)])
        assert all(len(r) == kh * kw * to for r in padded)
        node['weights'] = padded
    out.setdefault('metadata', {})['input_channel_padding'] = dict(original=c, padded=to)
    return out, c


def pad_frames(frames, original, to):
    """frames: int tensor [N,H,W,C] -> zero channels appended."""
    import torch
    if original == to or original == 1:
        return frames
    return torch.cat([frames, torch.zeros(*frames.shape[:-1], to - original, dtype=frames.dtype)], -1)


def frames_for(model, frames):
    """Pad host frames as the model's input padding requires."""
    pad = model.get('metadata', {}).get('input_channel_padding')
    return pad_frames(frames, pad['original'], pad['padded']) if pad else frames
