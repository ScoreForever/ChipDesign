"""Training-compatible ECG/HAR frontends and replay inputs (time-major NHWC)."""

from dataclasses import dataclass, field
from collections import deque
import csv
import io
import json
from pathlib import Path
import time
import zipfile

import numpy as np


MODELS = Path(__file__).resolve().parents[2] / "zoo" / "pretrained"
AXES = ("body_acc_x", "body_acc_y", "body_acc_z", "body_gyro_x", "body_gyro_y",
        "body_gyro_z", "total_acc_x", "total_acc_y", "total_acc_z")
AAMI = {**dict.fromkeys("NLRej", 0), **dict.fromkeys("AaJS", 1),
        **dict.fromkeys("VE", 2), "F": 3, **dict.fromkeys("/fQ", 4)}


@dataclass
class Sample:
    name: str
    values: list
    signal: np.ndarray
    expected: int | None = None
    expected_scores: list | None = None
    timestamp: float | None = None
    preprocess_ms: float = 0.0
    details: dict = field(default_factory=dict)


def quantize(signal, scale):
    signal = np.asarray(signal)
    if not np.isfinite(signal).all() or not np.isfinite(scale) or scale <= 0:
        raise ValueError("输入包含非有限数值，或量化 scale 无效")
    return np.rint(signal / scale).clip(-8, 7).astype(np.int8).reshape(-1).tolist()


def golden_samples(name):
    golden = json.loads((MODELS / name / "golden.json").read_text(encoding="utf-8"))
    channels = golden["input_shape"][-1]
    return [Sample(f"golden #{f['index']}", f["input"],
                   np.asarray(f["input"], dtype=float).reshape(-1, channels) * golden["input_scale"],
                   f["label"], f["output"], details={"source": "published quantized golden; not raw sensor data"})
            for f in golden["frames"]]


def prepare_ecg(signal, peak, scale):
    """Exactly data.mitbih: [R-96:R+160], pair average, population z-score."""
    signal = np.asarray(signal, dtype=np.float64)
    if signal.ndim != 1 or peak < 96 or peak + 160 > len(signal):
        raise ValueError("R 峰两侧需要 96/160 个原始采样点")
    beat = signal[peak - 96:peak + 160].reshape(128, 2).mean(1)
    beat = ((beat - beat.mean()) / (beat.std() + 1e-6)).astype(np.float32)
    return beat, quantize(beat, scale)


def ecg_record(path, scale, limit=200):
    import wfdb
    path = Path(path).with_suffix("")
    record = wfdb.rdrecord(str(path))
    if record.fs != 360:
        raise ValueError("当前 ECG 模型要求 360 Hz MIT-BIH 记录；其他设备需另行校准")
    ann = wfdb.rdann(str(path), "atr")
    samples = []
    for peak, symbol in zip(ann.sample, ann.symbol):
        if symbol not in AAMI or peak < 96 or peak + 160 > len(record.p_signal):
            continue
        started = time.perf_counter()
        beat, values = prepare_ecg(record.p_signal[:, 0], int(peak), scale)
        samples.append(Sample(f"{path.name} · R={peak} · {symbol}", values, beat[:, None],
                              AAMI[symbol], timestamp=float(peak) / 360,
                              preprocess_ms=(time.perf_counter() - started) * 1000,
                              details={"record": path.name, "peak": int(peak), "symbol": symbol,
                                       "lead": record.sig_name[0], "sample_rate": 360}))
        if len(samples) >= limit:
            break
    if not samples:
        raise ValueError("记录中没有满足窗口条件的 AAMI 心拍")
    return samples


def har_stats(path=None):
    source = Path(path) if path else Path(__file__).with_name("har_normalization.json")
    stats = json.loads(source.read_text(encoding="utf-8"))
    mean, std = np.asarray(stats["mean"], dtype=np.float32), np.asarray(stats["std"], dtype=np.float32)
    if mean.shape != (9,) or std.shape != (9,) or not np.isfinite(mean).all() or not np.isfinite(std).all() or (std <= 0).any():
        raise ValueError("HAR 归一化参数必须包含 9 路训练集 mean/std")
    return mean, std


def prepare_har(window, scale, stats=None):
    window = np.asarray(window, dtype=np.float32)
    if window.shape != (128, 9):
        raise ValueError("HAR 窗口必须是 128×9，按时间排列")
    mean, std = stats if stats is not None else har_stats()
    return quantize((window - mean) / std, scale)


def har_dataset(path, scale, limit=200):
    """Read the official test split directly from a UCI zip or dataset folder."""
    path = Path(path)
    archive = open_uci_zip(path) if path.suffix.lower() == ".zip" else None
    try:
        def load(relative):
            if archive:
                names = [n for n in archive.namelist() if n.endswith("UCI HAR Dataset/" + relative)]
                if len(names) != 1:
                    raise ValueError(f"UCI 文件缺失或重复：{relative}")
                with archive.open(names[0]) as handle:
                    return np.loadtxt(handle, max_rows=limit)
            base = path / "UCI HAR Dataset" if (path / "UCI HAR Dataset").is_dir() else path
            return np.loadtxt(base / relative, max_rows=limit)
        axes = [np.atleast_2d(load(f"test/Inertial Signals/{axis}_test.txt")) for axis in AXES]
        windows = np.stack(axes, axis=-1).astype(np.float32)
        labels = np.atleast_1d(load("test/y_test.txt")).astype(int) - 1
        if windows.shape[1:] != (128, 9) or len(windows) != len(labels) or ((labels < 0) | (labels > 5)).any():
            raise ValueError("UCI 测试数据形状或类别错误")
        return [Sample(f"UCI test #{i}", prepare_har(w, scale), w, int(y),
                       details={"source": "UCI HAR official test split", "sample_rate": 50})
                for i, (w, y) in enumerate(zip(windows, labels))]
    finally:
        if archive:
            archive.close()


def open_uci_zip(path):
    archive = zipfile.ZipFile(path)
    # The current UCI download wraps the original dataset in a second zip.
    if "UCI HAR Dataset.zip" in archive.namelist():
        payload = archive.read("UCI HAR Dataset.zip")
        archive.close()
        return zipfile.ZipFile(io.BytesIO(payload))
    return archive


def phone_windows(timestamps, acceleration, gyro, scale, *, rotation=None):
    """6-axis phone SI data -> 50 Hz, body/total acceleration (g), gyro (rad/s).

    The fixed rotation maps phone axes to waist-mounted training axes. This
    approximation must be validated on participants; it is not the UCI frontend.
    """
    from scipy.signal import butter, medfilt, sosfiltfilt
    t = np.asarray(timestamps, dtype=float)
    acc, gyr = np.asarray(acceleration, dtype=float), np.asarray(gyro, dtype=float)
    if t.ndim != 1 or len(t) < 128 or acc.shape != (len(t), 3) or gyr.shape != acc.shape:
        raise ValueError("手机输入需要至少 128 个时间戳和各 3 轴加速度/角速度")
    if not np.isfinite(t).all() or not np.isfinite(acc).all() or not np.isfinite(gyr).all():
        raise ValueError("手机输入包含非有限数值")
    dt = np.diff(t)
    if (dt <= 0).any() or dt.max() > 0.1 or np.median(dt) > 0.025:
        raise ValueError("时间戳须递增、采样至少 40 Hz，且不能有超过 100 ms 的缺口")
    transform = np.eye(3) if rotation is None else np.asarray(rotation, dtype=float)
    if transform.shape != (3, 3) or not np.allclose(transform @ transform.T, np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(transform), 1, atol=1e-5):
        raise ValueError("轴映射必须是右手正交旋转矩阵")
    grid = np.arange(t[0], t[-1], 1 / 50)
    if len(grid) < 153:
        raise ValueError("手机输入至少需要 3.1 秒，包含滤波预热")
    raw = np.column_stack((acc @ transform.T / 9.80665, gyr @ transform.T))
    aligned = np.stack([np.interp(grid, t, raw[:, c]) for c in range(6)], axis=1)
    filtered = sosfiltfilt(butter(3, 20, fs=50, output="sos"), medfilt(aligned, (3, 1)), axis=0)
    total, gyro_out = filtered[:, :3], filtered[:, 3:]
    gravity = sosfiltfilt(butter(3, 0.3, fs=50, output="sos"), total, axis=0)
    channels = np.column_stack((total - gravity, gyro_out, total)).astype(np.float32)
    samples = []
    for at in range(25, len(grid) - 127, 64):
        w = channels[at:at + 128]
        samples.append(Sample(f"手机 · {grid[at] - grid[0]:.2f} s", prepare_har(w, scale), w,
                              timestamp=float(grid[at]), details={"source": "phone approximation; participant validation pending", "sample_rate": 50}))
    return samples


def phone_csv(path, scale, rotation=None):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        # Keep only the last 40 s for a live capture; no unbounded filter input.
        rows = list(deque(csv.DictReader(handle),maxlen=2000))
    try:
        values = np.array([[float(r[k]) for k in ("time_s", "ax", "ay", "az", "gx", "gy", "gz")] for r in rows])
    except (KeyError, ValueError) as exc:
        raise ValueError("手机 CSV 需要 time_s,ax,ay,az,gx,gy,gz；单位 s、m/s²、rad/s") from exc
    if not len(rows):
        raise ValueError("手机 CSV 没有数据")
    return phone_windows(values[:, 0], values[:, 1:4], values[:, 4:7], scale, rotation=rotation)
