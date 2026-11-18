"""ECG/HAR replay UI sharing one resident backend and a bounded job queue."""

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
import time

import numpy as np

from demos.inference import InferenceSession
from demos.signals import ecg_record, golden_samples, har_dataset, phone_csv
from demos.window import ACCENT, BACKGROUND, BORDER, DRAWING, ERROR, MUTED, PANEL


LABELS = {"ecg": ("N · 正常/束支", "S · 室上性", "V · 室性", "F · 融合", "Q · 其他"),
          "har": ("步行", "上楼", "下楼", "坐", "站立", "躺卧")}


class ReplayDemo(tk.Frame):
    def __init__(self, parent, app, name):
        self.session = InferenceSession(app.args, name)
        super().__init__(parent, bg=BACKGROUND)
        self.app, self.name, self.worker = app, name, app.worker
        self.samples = golden_samples(name)
        self.index = 0
        self.records = []
        self.future = self.poll = self.timer = None
        self.ready = self.playing = False
        self.run_started = None
        self.follow_path = self.phone_timer = None
        self.last_phone_time = float('-inf')
        title = "ECG 心拍分类" if name == "ecg" else "人体活动识别"
        app.label(self, title, 24, bold=True).pack(anchor="w")
        description = ("按标注定位心拍并回放；五类结果仅用于课程工程演示。" if name == "ecg" else
                       "回放六类惯性信号，或导入固定腰部佩戴的手机 IMU 数据。")
        app.label(self, description, fg=MUTED).pack(anchor="w", pady=(4, 12))
        tools = tk.Frame(self, bg=BACKGROUND)
        tools.pack(fill="x", pady=(0, 12))
        self.buttons = []
        for text, command in (("发布样本", self.use_golden),
                              ("打开 MIT-BIH" if name == "ecg" else "打开 UCI HAR", self.choose_data),
                              ("识别当前", self.submit), ("下一条", self.next_sample),
                              ("连续回放", self.toggle_play)):
            b = ttk.Button(tools, text=text, command=command, style="Demo.TButton")
            b.pack(side="left", padx=(0, 6))
            self.buttons.append(b)
        self.play_button = self.buttons[-1]
        if name == "har":
            phone_tools = tk.Frame(self, bg=BACKGROUND)
            phone_tools.pack(fill="x", pady=(0, 8))
            for text, command in (("导入手机 CSV", self.choose_phone),
                                  ("跟随手机 CSV", lambda: self.choose_phone(live=True)),
                                  ("停止跟随", self.stop)):
                button = ttk.Button(phone_tools, text=text, command=command, style="Demo.TButton")
                button.pack(side="left", padx=(0, 8))
                self.buttons.append(button)
            self.stop_phone_button = button
        self.sample_name = app.label(self, "", 12, bold=True)
        self.sample_name.pack(anchor="w")
        self.waveform = tk.Canvas(self, width=700, height=190, bg=DRAWING,
                                  highlightthickness=1, highlightbackground=BORDER)
        self.waveform.pack(fill="both", expand=True, pady=(8, 10))
        self.waveform.bind("<Configure>", lambda _: self.draw())
        result = tk.Frame(self, bg=PANEL, padx=16, pady=12)
        result.pack(fill="x")
        self.result = app.label(result, "—", 23, fg=ACCENT, bold=True)
        self.result.pack(anchor="w")
        self.scores = app.label(result, "", 10, justify="left", wraplength=700)
        self.scores.pack(anchor="w", pady=(5, 0))
        self.latency = app.label(result, self.session.source, 10, fg=MUTED)
        self.latency.pack(anchor="w", pady=(6, 0))
        self.status = app.label(self, "", 10, fg=MUTED, wraplength=700, justify="left")
        self.status.pack(anchor="w", pady=(10, 5))
        footer = tk.Frame(self, bg=BACKGROUND)
        footer.pack(fill="x")
        self.retry = ttk.Button(footer, text="重新连接", command=self.connect, style="Demo.TButton")
        ttk.Button(footer, text="保存本次结果", command=self.save, style="Demo.TButton").pack(side="right")
        self.show_sample()
        self.connect()

    def controls(self, enabled):
        for b in self.buttons:
            can_stop = (b is self.play_button and self.playing) or (b is getattr(self,'stop_phone_button',None) and self.follow_path)
            b.state(["!disabled"] if enabled or can_stop else ["disabled"])

    def run(self, job, callback, text):
        if self.future is not None:
            return
        self.controls(False)
        self.status.config(text=text, fg=MUTED)
        self.future = self.worker.submit(job)
        self.poll = self.after(30, self.finish, callback)

    def finish(self, callback):
        self.poll = None
        if not self.future.done():
            self.poll = self.after(30, self.finish, callback)
            return
        future, self.future = self.future, None
        try:
            result = future.result()
        except Exception as exc:
            self.stop()
            self.status.config(text=str(exc), fg=ERROR)
            if callback == self.show_result or callback == self.connected:
                self.ready = False
                self.worker.submit(self.session.close)
                self.retry.pack(side="left")
            self.controls(self.ready)
            return
        self.controls(self.ready)
        callback(result)

    def connect(self):
        if self.future is not None:
            return
        self.ready = False
        self.retry.pack_forget()
        self.run(self.session.connect, self.connected, f"正在加载模型 · {self.session.source}")

    def connected(self, _):
        self.ready = True
        self.controls(True)
        self.status.config(text="发布样本已就绪；可识别当前样本或打开原始数据。")

    def use_golden(self):
        self.loaded(golden_samples(self.name))

    def loaded(self, samples):
        self.stop()
        self.samples, self.index = samples, 0
        self.show_sample()
        self.status.config(text=f"已载入 {len(samples)} 个窗口")

    def choose_data(self):
        types = [("MIT-BIH header", "*.hea")] if self.name == "ecg" else [("UCI HAR zip", "*.zip")]
        path = filedialog.askopenfilename(parent=self, filetypes=types)
        if path:
            self.stop()
            loader = ecg_record if self.name == "ecg" else har_dataset
            self.run(lambda: loader(path, self.session.scale), self.loaded, "正在读取并预处理原始数据")

    def choose_phone(self, live=False):
        path = filedialog.askopenfilename(parent=self, filetypes=[("Phone IMU CSV", "*.csv")])
        if path:
            self.stop()
            if live:
                self.follow_path = path
                self.last_phone_time = float('-inf')
                self.poll_phone()
                return
            rotation = getattr(self.app.args, "imu_rotation", None)
            self.run(lambda: phone_csv(path, self.session.scale, rotation), self.loaded, "正在对齐手机时间戳、单位与轴方向")

    def poll_phone(self):
        self.phone_timer = None
        if not self.follow_path:
            return
        if self.future is not None:
            self.phone_timer = self.after(500,self.poll_phone)
            return
        path = self.follow_path
        def job():
            try:
                # Bound memory/filter cost during a long capture. Keep timestamps.
                samples = phone_csv(path,self.session.scale,getattr(self.app.args,'imu_rotation',None))
                return samples, None
            except (OSError,ValueError) as exc:
                return [], str(exc)
        self.run(job,self.phone_loaded,"正在跟随手机采集")

    def phone_loaded(self, result):
        if not self.follow_path:
            return
        samples,error = result
        fresh = [s for s in samples if s.timestamp > self.last_phone_time]
        if fresh:
            # Display the most recent complete window; do not queue stale windows.
            self.samples,self.index = [fresh[-1]],0
            self.last_phone_time = fresh[-1].timestamp
            self.show_sample()
            self.submit()
        else:
            self.status.config(text="等待新手机窗口" + (f" · {error}" if error else ""))
            self.phone_timer = self.after(1000,self.poll_phone)

    def show_sample(self):
        sample = self.samples[self.index]
        truth = "无标签" if sample.expected is None else LABELS[self.name][sample.expected]
        self.sample_name.config(text=f"{self.index + 1}/{len(self.samples)} · {sample.name} · 标注：{truth}")
        self.result.config(text="—")
        self.scores.config(text="")
        self.draw()

    def draw(self):
        canvas = self.waveform
        canvas.delete("all")
        values = np.asarray(self.samples[self.index].signal)
        width, height = canvas.winfo_width(), canvas.winfo_height()
        channels = [0] if self.name == "ecg" else [0, 1, 2, 6, 7, 8]
        colors = (ACCENT, "#9ab8ff", "#f6c683", "#d9a1ef", "#ffb8af", "#a8d19a")
        for row, channel in enumerate(channels):
            line = values[:, channel]
            y = (row + 0.5) * height / len(channels)
            extent = max(float(np.ptp(line)), 0.01)
            points = []
            for i, v in enumerate(line):
                center = (float(line.min()) + float(line.max())) / 2
                points.extend((i * (width - 1) / max(1, len(line) - 1), y - (v - center) / extent * height / len(channels) * 0.7))
            canvas.create_line(0, y, width, y, fill=BORDER)
            canvas.create_line(*points, fill=colors[row], width=2)
        if self.name == "ecg":
            x = 48 / 128 * width
            canvas.create_line(x, 0, x, height, fill="#ffb8af", dash=(4, 4))
        else:
            canvas.create_text(8, 10, anchor="nw", text="身体加速度 xyz / 总加速度 xyz", fill=MUTED)

    def submit(self):
        if not self.ready or self.future is not None:
            return
        self.run_started = time.perf_counter()
        self.run(lambda: self.session.infer(self.samples[self.index].values), self.show_result, "正在推理")

    def show_result(self, result):
        sample = self.samples[self.index]
        exact = None if sample.expected_scores is None else list(result.scores) == sample.expected_scores
        self.result.config(text=LABELS[self.name][result.index], fg=ERROR if exact is False else ACCENT)
        self.scores.config(text="原始 INT32 分数（非概率）：\n" + "  ·  ".join(
            f"{label}: {score}" for label, score in zip(self.session.metadata["classes"], result.scores)))
        self.latency.config(text=f"{self.session.source} · {result.timing_text()}")
        self.status.config(text=f"预处理 {sample.preprocess_ms:.2f} ms" +
                           (" · 黄金输出逐项一致" if exact else " · 黄金输出不一致" if exact is False else " · 无逐位黄金输出"),
                           fg=ERROR if exact is False else MUTED)
        self.records.append(dict(model=self.name, backend=self.app.args.backend, sample=sample.name,
                                 expected=sample.expected, exact=exact, preprocess_ms=sample.preprocess_ms,
                                 details=sample.details, **result.to_dict()))
        if exact is False:
            self.stop()
        if self.follow_path:
            self.phone_timer = self.after(500,self.poll_phone)
        if self.playing:
            if self.index + 1 == len(self.samples):
                self.stop()
            else:
                next_time = self.samples[self.index + 1].timestamp
                delta = next_time - sample.timestamp if next_time is not None and sample.timestamp is not None else (0.8 if self.name == "ecg" else 1.28)
                spent = time.perf_counter() - self.run_started
                self.timer = self.after(max(20, int((delta - spent) * 1000)), self.advance)

    def next_sample(self):
        self.stop()
        self.index = (self.index + 1) % len(self.samples)
        self.show_sample()

    def advance(self):
        self.timer = None
        if self.playing:
            self.index += 1
            self.show_sample()
            self.submit()

    def toggle_play(self):
        if self.playing:
            self.stop()
        else:
            self.playing = True
            self.play_button.config(text="暂停回放")
            self.submit()

    def stop(self):
        self.playing = False
        self.follow_path = None
        if self.phone_timer is not None:
            self.after_cancel(self.phone_timer)
            self.phone_timer = None
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None
        if hasattr(self, "play_button"):
            self.play_button.config(text="连续回放")

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile=f"{self.name}_demo_results.json")
        if path:
            try:
                Path(path).write_text(json.dumps(dict(backend=self.app.args.backend, records=self.records), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                self.status.config(text=str(exc), fg=ERROR)

    def destroy(self):
        self.stop()
        if self.poll is not None:
            self.after_cancel(self.poll)
        if self.future is not None:
            self.future.cancel()
        self.worker.submit(self.session.close)
        super().destroy()
