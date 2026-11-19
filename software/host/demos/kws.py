"""Record a one-second command and classify it with the ABI 5 KWS model."""

import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, ttk
import wave

from demos.inference import InferenceSession
from demos.kws_audio import RATE, mfcc, one_second, quantize, read_pcm
from demos.window import ACCENT, BACKGROUND, BORDER, DRAWING, ERROR, MUTED, PANEL


ACTIONS = {"yes": "确认", "no": "取消", "up": "向上", "down": "向下"}
NON_KEYWORD_LABELS = {"unknown": "未识别", "silence": "静音"}


def record():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as exc:
        raise RuntimeError("麦克风录音需要 sounddevice 和 PortAudio。") from exc
    try:
        rate = int(sd.query_devices(kind="input")["default_samplerate"])
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32") as stream:
            audio, overflow = stream.read(rate)
    except (sd.PortAudioError, ValueError) as exc:
        raise RuntimeError(f"无法录音。请检查默认麦克风和录音权限。\n{exc}") from exc
    if overflow:
        raise RuntimeError("录音缓冲区溢出，请重试。")
    return audio[:, 0], rate


class Demo(tk.Frame):
    def __init__(self, parent, app):
        self.session = InferenceSession(app.args, "kws_l")
        stats = json.loads(Path(__file__).with_name("kws_normalization.json").read_text(encoding="utf-8"))
        self.mean, self.std = stats["mean"], stats["std"]
        super().__init__(parent, bg=BACKGROUND)
        self.worker = app.worker
        self.future = self.poll = None

        app.label(self, "英文关键词识别", 24, bold=True).pack(anchor="w")
        app.label(self, "录音时说出下列任一单词，也可打开包含单个关键词的 WAV。", fg=MUTED).pack(
            anchor="w", pady=(4, 15))

        body = tk.Frame(self, bg=BACKGROUND)
        body.pack(fill="x")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        audio = tk.Frame(body, bg=PANEL, padx=16, pady=14,
                         highlightbackground=BORDER, highlightthickness=1)
        audio.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        app.label(audio, "声音输入", 12, bold=True).pack(anchor="w")
        keywords = [word for word in self.session.metadata["classes"] if word not in NON_KEYWORD_LABELS]
        app.label(audio, f"支持 {len(keywords)} 个英文关键词", 11, bold=True).pack(anchor="w", pady=(9, 3))
        app.label(audio, " · ".join(keywords), 13, fg=ACCENT, wraplength=295, justify="left").pack(
            anchor="w", pady=(0, 5))
        app.label(audio, "模型也可输出「未识别」或「静音」。", 10, fg=MUTED).pack(
            anchor="w", pady=(0, 10))
        self.waveform = tk.Canvas(audio, height=100, width=295, bg=DRAWING,
                                  highlightthickness=0)
        self.waveform.pack(fill="x")
        self.waveform.bind("<Configure>", lambda _: self.draw_waveform())
        self.envelope = None
        controls = tk.Frame(audio, bg=PANEL)
        controls.pack(fill="x", pady=(13, 0))
        self.record_button = ttk.Button(controls, text="录音 1 秒", command=self.record_clip,
                                        style="DemoPrimary.TButton")
        self.record_button.pack(side="left")
        self.file_button = ttk.Button(controls, text="打开 WAV", command=self.choose_file,
                                      style="Demo.TButton")
        self.file_button.pack(side="left", padx=(8, 0))

        result = tk.Frame(body, bg=PANEL, padx=16, pady=14,
                          highlightbackground=BORDER, highlightthickness=1)
        result.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        header = tk.Frame(result, bg=PANEL)
        header.pack(fill="x")
        app.label(header, "识别结果", 12, bold=True).pack(side="left")
        app.label(header, self.session.source, 10, fg=MUTED).pack(side="right")
        self.word = app.label(result, "—", 38, fg=ACCENT, bold=True)
        self.word.pack(pady=(13, 0))
        self.action = app.label(result, "", 12)
        self.action.pack()
        self.latency = app.label(result, "", 10, fg=MUTED)
        self.latency.pack(pady=(8, 0))
        self.scores = app.label(result, "", 10, fg=MUTED, wraplength=300, justify="left")
        self.scores.pack(pady=(8, 0))

        self.status = app.label(self, "", 10, fg=MUTED, justify="left", wraplength=690)
        self.status.pack(anchor="w", pady=(12, 5))
        self.retry = ttk.Button(self, text="重新连接", command=self.connect, style="Demo.TButton")
        self.connect()

    def set_controls(self, enabled):
        states = ["!disabled"] if enabled else ["disabled"]
        self.record_button.state(states)
        self.file_button.state(states)

    def run(self, job, callback, status):
        self.set_controls(False)
        self.status.config(text=status, fg=MUTED)
        self.future = self.worker.submit(job)
        self.poll = self.after(20, self.finish, callback)

    def connect(self):
        self.retry.pack_forget()
        self.run(self.session.connect, self.connected, f"正在加载模型 · {self.session.source}")

    def connected(self, _):
        self.set_controls(True)
        self.status.config(text="可以录音或打开 WAV")

    def choose_file(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("PCM WAV", "*.wav")])
        if path:
            self.start_clip(lambda: read_pcm(path), "正在读取 WAV")

    def record_clip(self):
        self.start_clip(record, "正在录音，请说关键词")

    def start_clip(self, capture, status):
        self.word.config(text="—")
        self.action.config(text="")
        self.latency.config(text="")
        self.scores.config(text="")
        self.envelope = None
        self.draw_waveform()
        self.run(lambda: self.prepare_clip(capture), self.submit_clip, status)

    def prepare_clip(self, capture):
        started = time.perf_counter()
        samples, rate = capture()
        captured = time.perf_counter()
        audio = one_second(samples, rate)
        values = quantize(mfcc(audio), self.mean, self.std, self.session.scale).ravel().tolist()
        timing = dict(capture_ms=(captured - started) * 1000,
                      preprocess_ms=(time.perf_counter() - captured) * 1000)
        return abs(audio.reshape(100, -1)).max(axis=1), values, timing

    def submit_clip(self, clip):
        self.envelope, values, timing = clip
        self.draw_waveform()
        self.run(lambda: (self.session.infer(values, **timing), timing), self.show_result, "正在推理")

    def draw_waveform(self):
        canvas = self.waveform
        width, height = canvas.winfo_width(), canvas.winfo_height()
        canvas.delete("all")
        center = height / 2
        canvas.create_line(0, center, width, center, fill=BORDER)
        if self.envelope is not None:
            for index, value in enumerate(self.envelope):
                x = (index + 0.5) * width / len(self.envelope)
                peak = min(float(value), 1) * (center - 5)
                canvas.create_line(x, center - peak, x, center + peak, fill=ACCENT, width=2)

    def show_result(self, completed):
        result, timing = completed
        label = self.session.metadata["classes"][result.index]
        self.word.config(text=NON_KEYWORD_LABELS.get(label, label))
        action = ACTIONS.get(label)
        self.action.config(text=f"界面动作 · {action}" if action else "")
        self.latency.config(text=result.timing_text(), wraplength=300)
        self.scores.config(text="INT32 分数（非概率）：\n" + " · ".join(
            f"{k}: {v}" for k, v in zip(self.session.metadata["classes"], result.scores)))
        self.status.config(text=f"采集/读取 {timing['capture_ms']:.1f} ms · "
                           f"预处理 {timing['preprocess_ms']:.1f} ms")
        self.set_controls(True)

    def finish(self, callback):
        self.poll = None
        if not self.future.done():
            self.poll = self.after(20, self.finish, callback)
            return
        future, self.future = self.future, None
        try:
            result = future.result()
        except (OSError, RuntimeError, ValueError, EOFError, ImportError, wave.Error) as exc:
            self.status.config(text=str(exc), fg=ERROR)
            if callback == self.submit_clip:
                self.set_controls(True)
            else:
                self.worker.submit(self.session.close)
                self.retry.pack(anchor="w")
            return
        callback(result)

    def destroy(self):
        if self.poll is not None:
            self.after_cancel(self.poll)
        if self.future is not None:
            self.future.cancel()
        self.worker.submit(self.session.close)
        super().destroy()
