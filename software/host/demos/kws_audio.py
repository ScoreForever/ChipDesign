"""PCM frontend for the Speech Commands INT4 model."""

from math import gcd
import wave

import numpy as np


RATE = 16000
WINDOW = 640
HOP = 320
MELS = 40
COEFFICIENTS = 10

# The training transform uses a periodic Hann window, HTK mel triangles,
# a power spectrogram in dB with top_db=80, and an orthonormal DCT-II.
HANN = np.hanning(WINDOW + 1)[:-1].astype(np.float32)
FREQUENCIES = np.linspace(0, RATE / 2, WINDOW // 2 + 1, dtype=np.float32)
MEL_EDGES = 700 * (10 ** (np.linspace(0, 2595 * np.log10(1 + RATE / 1400),
                                     MELS + 2, dtype=np.float32) / 2595) - 1)
DISTANCES = MEL_EDGES[None, :] - FREQUENCIES[:, None]
FILTERS = np.maximum(0, np.minimum(-DISTANCES[:, :-2] / np.diff(MEL_EDGES)[:-1],
                                  DISTANCES[:, 2:] / np.diff(MEL_EDGES)[1:]))
DCT = (np.cos(np.pi / MELS * (np.arange(MELS)[:, None] + 0.5)
              * np.arange(COEFFICIENTS)) * np.sqrt(2 / MELS)).astype(np.float32)
DCT[:, 0] /= np.sqrt(2)


def read_pcm(source):
    with wave.open(source, "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError("请选择 16 位 PCM WAV 文件。")
        rate = wav.getframerate()
        data = np.frombuffer(wav.readframes(rate), dtype="<i2")
        if not data.size:
            raise ValueError("WAV 文件没有音频样本。")
        samples = data.reshape(-1, wav.getnchannels()).mean(axis=1, dtype=np.float32) / 32768
        return samples, rate


def one_second(samples, rate):
    """Convert the first second to 16 kHz and zero pad shorter recordings."""
    samples = np.asarray(samples[:rate], dtype=np.float32)
    if rate != RATE and samples.size:
        from scipy.signal import resample_poly
        factor = gcd(rate, RATE)
        samples = resample_poly(samples, RATE // factor, rate // factor)
    frame = np.zeros(RATE, dtype=np.float32)
    frame[:len(samples)] = samples[:RATE]
    return frame


def mfcc(waveforms):
    """Compute the training frontend for one waveform or a batch."""
    windows = np.lib.stride_tricks.sliding_window_view(waveforms, WINDOW, axis=-1)[..., ::HOP, :]
    spectrum = np.abs(np.fft.rfft(windows * HANN, axis=-1)) ** 2
    db = 10 * np.log10(np.maximum(spectrum @ FILTERS, 1e-10))
    db = np.maximum(db, db.max(axis=(-2, -1), keepdims=True) - 80)
    return db @ DCT


def quantize(features, mean, std, scale):
    return np.rint((features - mean) / (std * scale)).clip(-8, 7).astype(np.int8)
