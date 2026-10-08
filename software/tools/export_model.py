"""TinyCNN-8 模型导出器：FP32 checkpoint -> INT8 部署包。

流程：加载 checkpoint -> BN 折叠 -> 校准各层激活 scale -> 量化权重/偏置 ->
编码 multiplier/shift -> 按 RTL 布局打包权重 -> 保存 .npz 部署包。
"""

import os
import sys
import argparse

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'train'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model import TinyCNN8                      # noqa: E402
from quantize import quantize_multiplier        # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preprocess                                # noqa: E402
from dataset import SpeechCommandsDataset, TASK_LABELS  # noqa: E402


def fold_bn(weight, bn):
    """把 BN 折叠进卷积权重/偏置，返回 (W_fold, b_fold)。weight: (out,in,kh,kw)。"""
    eps = bn.eps
    scale = bn.weight / torch.sqrt(bn.running_var + eps)
    W_fold = weight * scale.view(-1, 1, 1, 1)
    b_fold = (0.0 - bn.running_mean) * scale + bn.bias
    return W_fold.detach(), b_fold.detach()


def quantize_weight(W_fold):
    """逐输出通道对称 INT8 量化，返回 (W_int8, weight_scale)。"""
    wscale = W_fold.abs().amax(dim=(1, 2, 3)) / 127.0
    wscale = torch.clamp(wscale, min=torch.finfo(W_fold.dtype).eps)
    W_int = torch.clamp(torch.round(W_fold / wscale.view(-1, 1, 1, 1)), -128, 127).to(torch.int8)
    return W_int, wscale


def pack_conv1(W_int):
    """(8,1,3,3) -> packed (9, 8)：word[spatial] lane=oc。"""
    W = W_int[:, 0, :, :].cpu().numpy()            # (8,3,3)
    packed = np.zeros((9, 8), dtype=np.int8)
    for kh in range(3):
        for kw in range(3):
            packed[kh * 3 + kw, :] = W[:, kh, kw]
    return packed


def pack_conv2(W_int):
    """(8,8,3,3) -> packed (72, 8)：word[spatial*8+ic] lane=oc。"""
    W = W_int.cpu().numpy()                        # (8,8,3,3)
    packed = np.zeros((72, 8), dtype=np.int8)
    for kh in range(3):
        for kw in range(3):
            for ic in range(8):
                packed[(kh * 3 + kw) * 8 + ic, :] = W[:, ic, kh, kw]
    return packed


def pack_fc(W_int):
    """(num_classes, input_features) -> packed (input_features, 8)。"""
    num_classes = W_int.shape[0]
    input_features = W_int.shape[1]
    packed = np.zeros((input_features, 8), dtype=np.int8)
    for ic in range(input_features):
        for oc in range(num_classes):
            packed[ic, oc] = W_int[oc, ic].item()
    return packed


def build_weight_memory(conv1_w, conv2_w, fc_w, words=256):
    """构造生产 8-lane NPU 的紧凑权重 SRAM 镜像。"""
    conv1_base = 0
    conv2_base = conv1_base + len(conv1_w)   # 9
    fc_base = conv2_base + len(conv2_w)      # 81
    used = fc_base + len(fc_w)               # 241 for Flatten(160)
    if used > words:
        raise ValueError(f'weight image needs {used} words, capacity is {words}')
    image = np.zeros((words, 8), dtype=np.int8)
    image[conv1_base:conv2_base] = conv1_w
    image[conv2_base:fc_base] = conv2_w
    image[fc_base:used] = fc_w
    return image, conv1_base, conv2_base, fc_base, used


def calibrate(model, data_dir, calib_size, device, task):
    """在校准集上跑折叠后的 FP32 前向，收集各层激活 scale。"""
    W1, b1 = fold_bn(model.conv1.weight.data, model.bn1)
    W2, b2 = fold_bn(model.conv2.weight.data, model.bn2)
    W_fc = model.fc.weight.data
    b_fc = model.fc.bias.data

    # 校准必须模拟部署输入；训练增强只用于优化权重，不应污染激活范围。
    ds = SpeechCommandsDataset(
        data_dir, 'train', seed=0, augment=False, task=task
    )
    idx = np.random.RandomState(0).choice(len(ds), size=min(calib_size, len(ds)), replace=False)

    in_max = 0.0
    act1_max = 0.0
    act3_max = 0.0
    model.eval()
    with torch.no_grad():
        for i in idx:
            x, _ = ds[i]
            x = x.unsqueeze(0).to(device)               # (1,1,20,16)
            in_max = max(in_max, float(x.abs().max()))
            h = F.conv2d(x, W1, b1, padding=1)
            h = F.relu(h)
            act1_max = max(act1_max, float(h.max()))
            h = F.max_pool2d(h, 2)
            h = F.conv2d(h, W2, b2, padding=1)
            h = F.relu(h)
            act3_max = max(act3_max, float(h.max()))
            h = F.max_pool2d(h, 2)

    return {
        'input_scale': in_max / 127.0,
        'act1_scale': act1_max / 127.0,
        'act3_scale': act3_max / 127.0,
        # Pool2 不改变 Conv2 输出 scale；Flatten 只改变布局。
        'fc_input_scale': act3_max / 127.0,
        'W1': W1, 'b1': b1, 'W2': W2, 'b2': b2, 'W_fc': W_fc, 'b_fc': b_fc,
    }


def _require_positive_finite(name, value):
    value = float(value)
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError(f'{name} must be finite and positive, got {value}')
    return value


def _int32_checked(name, tensor):
    rounded = torch.round(tensor)
    info = torch.iinfo(torch.int32)
    if not torch.isfinite(rounded).all() or (rounded < info.min).any() or (rounded > info.max).any():
        raise OverflowError(f'{name} cannot be represented as INT32')
    return rounded.to(torch.int32).cpu().numpy()


def export(checkpoint_path, data_dir, calib_size, device):
    ckpt = torch.load(checkpoint_path, map_location='cpu')
    model_head = ckpt.get('model_head', 'flatten')
    if model_head != 'flatten':
        raise ValueError(
            'current baseline RTL/export package supports only the Flatten head'
        )
    if ckpt['num_classes'] != 4:
        raise ValueError('current production baseline requires exactly four classes')
    model = TinyCNN8(num_classes=ckpt['num_classes'], head=model_head)
    model.load_state_dict(ckpt['state_dict'])
    model.to(device).eval()
    labels = list(ckpt.get('labels', TASK_LABELS['four_class']))
    if labels != TASK_LABELS['four_class']:
        raise ValueError('production class ABI must be yes/no/up/down in that order')
    task = 'four_class'

    cal = calibrate(model, data_dir, calib_size, device, task)
    input_scale = _require_positive_finite('input_scale', cal['input_scale'])
    act1_scale = _require_positive_finite('act1_scale', cal['act1_scale'])
    act3_scale = _require_positive_finite('act3_scale', cal['act3_scale'])
    fc_input_scale = _require_positive_finite('fc_input_scale', cal['fc_input_scale'])

    # ---- Conv1 ----
    W1_int, ws1 = quantize_weight(cal['W1'])          # (8,1,3,3), (8,)
    ws1 = ws1.to(device)
    b1 = cal['b1'].to(device)
    conv1_bias = _int32_checked('conv1_bias', b1 / (input_scale * ws1))
    real_m1 = (input_scale * ws1 / act1_scale).cpu().numpy()
    conv1_mult = np.zeros(8, dtype=np.int32)
    conv1_shift = np.zeros(8, dtype=np.int8)
    for oc in range(8):
        conv1_mult[oc], conv1_shift[oc] = quantize_multiplier(float(real_m1[oc]))

    # ---- Conv2 ----
    W2_int, ws2 = quantize_weight(cal['W2'])
    ws2 = ws2.to(device)
    b2 = cal['b2'].to(device)
    conv2_bias = _int32_checked('conv2_bias', b2 / (act1_scale * ws2))
    real_m2 = (act1_scale * ws2 / act3_scale).cpu().numpy()
    conv2_mult = np.zeros(8, dtype=np.int32)
    conv2_shift = np.zeros(8, dtype=np.int8)
    for oc in range(8):
        conv2_mult[oc], conv2_shift[oc] = quantize_multiplier(float(real_m2[oc]))

    # ---- FC ----
    W_fc = cal['W_fc'].to(device)                    # (num_classes, 160)
    ws_fc = W_fc.abs().amax(dim=1) / 127.0
    ws_fc = torch.clamp(ws_fc, min=torch.finfo(W_fc.dtype).eps)
    W_fc_int = torch.clamp(torch.round(W_fc / ws_fc.view(-1, 1)), -128, 127).to(torch.int8)
    b_fc = cal['b_fc'].to(device)
    fc_bias = _int32_checked('fc_bias', b_fc / (fc_input_scale * ws_fc))
    ws_fc_np = ws_fc.cpu().numpy()
    fc_cmp_mult = np.zeros(ckpt['num_classes'], dtype=np.int32)
    fc_cmp_shift = np.zeros(ckpt['num_classes'], dtype=np.int8)
    for oc in range(ckpt['num_classes']):
        fc_cmp_mult[oc], fc_cmp_shift[oc] = quantize_multiplier(float(ws_fc_np[oc]))

    conv1_w = pack_conv1(W1_int)
    conv2_w = pack_conv2(W2_int)
    fc_w = pack_fc(W_fc_int)
    weight_mem, conv1_base, conv2_base, fc_base, weight_words_used = (
        build_weight_memory(conv1_w, conv2_w, fc_w)
    )

    pkg = {
        'num_classes': ckpt['num_classes'],
        'labels': np.asarray(labels),
        'preprocess_frame_len': np.int32(preprocess.FRAME_LEN),
        'preprocess_frame_hop': np.int32(preprocess.FRAME_HOP),
        'preprocess_n_fft': np.int32(preprocess.N_FFT),
        'preprocess_n_mels': np.int32(preprocess.N_MELS),
        'preprocess_n_frames': np.int32(preprocess.N_FRAMES),
        'model_head': np.asarray('flatten'),
        'fc_input_features': np.int32(W_fc.shape[1]),
        'input_scale': np.float32(input_scale),
        'act1_scale': np.float32(act1_scale),
        'act3_scale': np.float32(act3_scale),
        'fc_input_scale': np.float32(fc_input_scale),
        'conv1_w': conv1_w, 'conv1_bias': conv1_bias,
        'conv1_mult': conv1_mult, 'conv1_shift': conv1_shift,
        'conv2_w': conv2_w, 'conv2_bias': conv2_bias,
        'conv2_mult': conv2_mult, 'conv2_shift': conv2_shift,
        'fc_w': fc_w, 'fc_bias': fc_bias,
        'fc_cmp_mult': fc_cmp_mult, 'fc_cmp_shift': fc_cmp_shift,
        'weight_mem': weight_mem,
        'conv1_weight_base': np.int32(conv1_base),
        'conv2_weight_base': np.int32(conv2_base),
        'fc_weight_base': np.int32(fc_base),
        'weight_words_used': np.int32(weight_words_used),
    }
    return pkg


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--checkpoint', required=True)
    p.add_argument('--data-dir', required=True)
    p.add_argument('--calib-size', type=int, default=2048)
    p.add_argument('--output', required=True)
    p.add_argument('--device', default='auto')
    args = p.parse_args()

    device = ('cuda' if torch.cuda.is_available() else 'cpu') if args.device == 'auto' else args.device
    pkg = export(args.checkpoint, args.data_dir, args.calib_size, device)

    np.savez(args.output, **pkg)
    print(f'saved deployment package to {args.output}')
    print(f"  num_classes      = {pkg['num_classes']}")
    print(f"  input_scale      = {pkg['input_scale']:.6f}")
    print(f"  act1_scale       = {pkg['act1_scale']:.6f}")
    print(f"  act3_scale       = {pkg['act3_scale']:.6f}")
    print(f"  fc_input_scale   = {pkg['fc_input_scale']:.6f}")
    print(f"  conv1_shift      = {pkg['conv1_shift'].tolist()}")
    print(f"  conv2_shift      = {pkg['conv2_shift'].tolist()}")
    print(f"  fc_cmp_shift     = {pkg['fc_cmp_shift'].tolist()}")
    print(f"  labels           = {pkg['labels'].tolist()}")


if __name__ == '__main__':
    main()
