"""log-mel 特征提取参考实现（numpy + 标准库 wave）。

与 NPU 输入契约对齐：16 kHz 采样、50 ms 帧 / 50 ms 帧移、1024 点 FFT、
16 个 mel 滤波器（80 Hz–7600 Hz）、log 压缩。1 秒音频正好得到覆盖完整
时间范围的 20×16 特征图，不再截掉关键词前后部分。
本实现只依赖 numpy 与标准库，便于后续移植为 CV32E40P 的 C 实现；
C 版本必须复现这里的每个公式与舍入方式。
"""

import wave
import numpy as np

# ---- 冻结的预处理参数（与 docs/MODEL_TRAINING_PLAN.md 第 2 节一致）----
SAMPLE_RATE = 16000
CLIP_SAMPLES = 16000     # 固定 1 秒输入
FRAME_LEN = 800          # 50 ms @ 16 kHz
FRAME_HOP = 800          # 50 ms，无重叠；1 秒正好 20 帧
N_FFT = 1024
N_MELS = 16
FMIN = 80.0              # Hz
FMAX = 7600.0            # Hz
N_FRAMES = 20
EPS = 1e-10              # log 压缩的数值下限


def hz_to_mel(f):
    return 2595.0 * np.log10(1.0 + f / 700.0)


def mel_to_hz(m):
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _build_mel_filterbank():
    """返回 (N_MELS, N_FFT//2 + 1) 的三角 mel 滤波器组。"""
    fft_freqs = np.linspace(0.0, SAMPLE_RATE / 2.0, N_FFT // 2 + 1)
    mel_points = np.linspace(hz_to_mel(FMIN), hz_to_mel(FMAX), N_MELS + 2)
    hz_points = mel_to_hz(mel_points)
    bins = np.floor((N_FFT + 1) * hz_points / SAMPLE_RATE).astype(np.int64)
    filters = np.zeros((N_MELS, N_FFT // 2 + 1), dtype=np.float64)
    for m in range(N_MELS):
        left, center, right = bins[m], bins[m + 1], bins[m + 2]
        for k in range(left, center):
            filters[m, k] = (k - left) / max(center - left, 1)
        for k in range(center, right):
            filters[m, k] = (right - k) / max(right - center, 1)
    return filters


MEL_FILTERBANK = _build_mel_filterbank()
HAMMING = np.hamming(FRAME_LEN)


def read_wav(path):
    """读取 16 kHz、16-bit PCM WAV，返回 float32 数组（归一化到 [-1, 1]）。"""
    with wave.open(str(path), 'rb') as w:
        if w.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{path}: expected {SAMPLE_RATE} Hz, got {w.getframerate()}")
        n_channels = w.getnchannels()
        n_frames = w.getnframes()
        raw = w.readframes(n_frames)
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels > 1:
            x = x.reshape(-1, n_channels).mean(axis=1)
    return x


def logmel(x):
    """1-D 音频 -> (n_frames, N_MELS) 的 log-mel 特征（float）。"""
    if len(x) < FRAME_LEN:
        x = np.pad(x, (0, FRAME_LEN - len(x)))
    n_frames = 1 + (len(x) - FRAME_LEN) // FRAME_HOP
    frames = np.lib.stride_tricks.as_strided(
        x,
        shape=(n_frames, FRAME_LEN),
        strides=(x.strides[0] * FRAME_HOP, x.strides[0]),
    ).copy()
    frames *= HAMMING
    spec = np.fft.rfft(frames, n=N_FFT, axis=1)          # (n_frames, 513)
    power = (spec.real ** 2 + spec.imag ** 2).astype(np.float64)
    mel = power @ MEL_FILTERBANK.T                        # (n_frames, N_MELS)
    mel = np.log(mel + EPS)
    return mel.astype(np.float32)


def extract_feature(x):
    """1-D 音频 -> (20, 16) 特征图。

    短于 1 秒时尾部补零，长于 1 秒时保留前 1 秒。GSCD v2 已把关键词
    对齐到 1 秒；显式定长可保证 Python 与后续 C 实现拥有相同边界语义。
    """
    x = np.asarray(x, dtype=np.float32)
    if len(x) < CLIP_SAMPLES:
        x = np.pad(x, (0, CLIP_SAMPLES - len(x)))
    elif len(x) > CLIP_SAMPLES:
        x = x[:CLIP_SAMPLES]
    feats = logmel(x)
    if feats.shape != (N_FRAMES, N_MELS):
        raise RuntimeError(f'unexpected feature shape {feats.shape}')
    return feats.astype(np.float32)


def quantize_feature(feats, input_scale):
    """把 log-mel 特征量化为 signed INT8（对称、zero point 0）。

    input_int8 = clamp(round(feats / input_scale), -128, 127)
    """
    q = np.clip(np.rint(feats / input_scale), -128, 127)
    return q.astype(np.int8)


def calibrate_input_scale(feature_list):
    """在校准特征集合上求固定 input_scale = max(abs(x)) / 127。"""
    mx = max(float(np.abs(f).max()) for f in feature_list)
    return mx / 127.0
