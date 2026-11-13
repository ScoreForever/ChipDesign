"""Draw a digit and classify it with the ABI 5 MNIST model."""

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw

from demos.inference import InferenceSession
from demos.window import ACCENT, BACKGROUND, BORDER, DRAWING, ERROR, MUTED, PANEL, TEXT


SIDE = 360
BRUSH = 19
IDLE_MS = 450


def prepare(image, scale):
    """Fit ink into a 20-pixel box and center its intensity in a 28-pixel frame."""
    bounds = image.getbbox()
    if bounds is None:
        return None
    ink = image.crop(bounds)
    ratio = 20 / max(ink.size)
    ink = ink.resize(tuple(max(1, round(side * ratio)) for side in ink.size),
                     Image.Resampling.LANCZOS)
    pixels = list(ink.tobytes())
    mass = sum(pixels)
    if not mass:
        return None
    cx = sum((index % ink.width) * value for index, value in enumerate(pixels)) / mass
    cy = sum((index // ink.width) * value for index, value in enumerate(pixels)) / mass
    x = min(28 - ink.width, max(0, round(13.5 - cx)))
    y = min(28 - ink.height, max(0, round(13.5 - cy)))
    frame = Image.new("L", (28, 28))
    frame.paste(ink, (x, y))
    values = [max(-8, min(7, round((pixel / 255 * 2 - 1) / scale)))
              for pixel in frame.tobytes()]
    return frame, values


class Demo(tk.Frame):
    def __init__(self, parent, app):
        self.session = InferenceSession(app.args, "mnist_l")
        super().__init__(parent, bg=BACKGROUND)
        self.worker = app.worker
        self.image = Image.new("L", (SIDE, SIDE))
        self.preview_image = None
        self.previous = None
        self.timer = self.poll = None
        self.future = self.pending = None
        self.revision = 0
        self.ready = False
        self.source = self.session.source

        app.label(self, "手写数字识别", 24, bold=True).pack(anchor="w")
        app.label(self, "写下一个 0–9 的数字，停笔后自动识别。", fg=MUTED).pack(
            anchor="w", pady=(4, 15))
        body = tk.Frame(self, bg=BACKGROUND)
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)
        board = tk.Frame(body, bg=PANEL, padx=13, pady=12,
                         highlightbackground=BORDER, highlightthickness=1)
        board.grid(row=0, column=0, sticky="ns")
        tools = tk.Frame(board, bg=PANEL)
        tools.pack(fill="x", pady=(0, 9))
        app.label(tools, "书写区", 12, bold=True).pack(side="left")
        ttk.Button(tools, text="清空", command=self.clear,
                   style="Demo.TButton").pack(side="right")
        self.canvas = tk.Canvas(board, width=SIDE, height=SIDE, bg=DRAWING,
                                borderwidth=0, highlightthickness=0, cursor="crosshair")
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self.start_stroke)
        self.canvas.bind("<B1-Motion>", self.extend_stroke)
        self.canvas.bind("<ButtonRelease-1>", self.end_stroke)

        details = tk.Frame(body, bg=BACKGROUND)
        details.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        result = tk.Frame(details, bg=PANEL, padx=16, pady=11,
                          highlightbackground=BORDER, highlightthickness=1)
        result.pack(fill="x")
        title_row = tk.Frame(result, bg=PANEL)
        title_row.pack(fill="x")
        app.label(title_row, "识别结果", 12, bold=True).pack(side="left")
        app.label(title_row, self.source, 10, fg=MUTED).pack(side="right")
        self.digit = app.label(result, "—", 63, fg=ACCENT, bold=True)
        self.digit.pack()
        self.latency = app.label(result, "", 10, fg=MUTED)
        self.latency.pack(pady=(0, 3))

        input_panel = tk.Frame(details, bg=PANEL, padx=16, pady=12,
                               highlightbackground=BORDER, highlightthickness=1)
        input_panel.pack(fill="x", pady=(12, 0))
        app.label(input_panel, "模型输入", 12, bold=True).pack(anchor="w")
        image_row = tk.Frame(input_panel, bg=PANEL)
        image_row.pack(fill="x", pady=(8, 0))
        self.preview = tk.Label(image_row, bg=DRAWING, bd=0)
        self.preview.pack(side="left")
        app.label(image_row, "28 × 28\n灰度 → INT4", 10, fg=MUTED, justify="left").pack(
            side="left", padx=(13, 0))
        self.update_preview()

        self.status = app.label(details, "", 10, fg=MUTED, wraplength=300,
                                justify="left", anchor="w")
        self.status.pack(fill="x", pady=(9, 3))
        self.retry = ttk.Button(details, text="重新连接", command=self.connect,
                                style="Demo.TButton")
        self.connect()

    def update_preview(self, frame=None):
        frame = frame if frame is not None else Image.new("L", (28, 28))
        enlarged = frame.resize((84, 84), Image.Resampling.NEAREST).convert("RGB")
        ppm = b"P6\n84 84\n255\n" + enlarged.tobytes()
        self.preview_image = tk.PhotoImage(master=self, data=ppm, format="PPM")
        self.preview.config(image=self.preview_image)

    def cancel_timer(self):
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer = None

    def start_stroke(self, event):
        self.cancel_timer()
        self.revision += 1
        self.pending = None
        self.digit.config(text="—")
        self.latency.config(text="")
        self.update_preview()
        if self.ready:
            self.status.config(text="书写中", fg=MUTED)
        self.previous = (event.x, event.y)
        self.extend_stroke(event)

    def extend_stroke(self, event):
        if self.previous is None:
            return
        x, y = event.x, event.y
        radius = BRUSH // 2
        dot = (x - radius, y - radius, x + radius, y + radius)
        draw = ImageDraw.Draw(self.image)
        draw.line((self.previous, (x, y)), fill=255, width=BRUSH)
        draw.ellipse(dot, fill=255)
        self.canvas.create_line(*self.previous, x, y, fill=TEXT, width=BRUSH, capstyle="round")
        self.canvas.create_oval(*dot, fill=TEXT, outline="")
        self.previous = (x, y)

    def end_stroke(self, event):
        if self.previous is None:
            return
        self.extend_stroke(event)
        self.previous = None
        self.timer = self.after(IDLE_MS, self.submit)

    def clear(self):
        self.cancel_timer()
        self.revision += 1
        self.pending = self.previous = None
        self.image.paste(0, (0, 0, SIDE, SIDE))
        self.canvas.delete("all")
        self.update_preview()
        self.digit.config(text="—")
        self.latency.config(text="")
        if self.ready:
            self.status.config(text="等待书写", fg=MUTED)

    def submit(self):
        self.timer = None
        prepared = prepare(self.image, self.session.scale)
        if prepared is not None:
            frame, values = prepared
            self.update_preview(frame)
            self.pending = (self.revision, values)
            self.send_pending()

    def connect(self):
        if self.future is not None:
            return
        self.retry.pack_forget()
        self.status.config(text=f"正在加载模型 · {self.source}", fg=MUTED)
        self.future = self.worker.submit(self.session.connect)
        self.poll = self.after(20, self.finish, None)

    def send_pending(self):
        if self.future is not None or not self.ready or self.pending is None:
            return
        revision, values = self.pending
        self.pending = None
        self.status.config(text="正在推理", fg=MUTED)
        self.future = self.worker.submit(self.session.infer, values)
        self.poll = self.after(20, self.finish, revision)

    def finish(self, revision):
        self.poll = None
        if not self.future.done():
            self.poll = self.after(20, self.finish, revision)
            return
        future, self.future = self.future, None
        try:
            result = future.result()
        except (OSError, RuntimeError, ValueError) as exc:
            self.ready = False
            # Close an uncertain session without resending its input or START.
            self.worker.submit(self.session.close)
            self.status.config(text=f"{self.source} 失败\n{exc}", fg=ERROR)
            self.retry.pack(anchor="w")
            return
        if revision is None:
            self.ready = True
            text = "书写中" if self.previous is not None else "等待书写"
            self.status.config(text=text, fg=MUTED)
        elif revision == self.revision:
            digit, elapsed = result
            self.digit.config(text=str(digit))
            self.latency.config(text=f"推理往返  {elapsed:.0f} ms")
            self.status.config(text="识别完成", fg=MUTED)
        self.send_pending()

    def destroy(self):
        self.cancel_timer()
        if self.poll is not None:
            self.after_cancel(self.poll)
        if self.future is not None:
            self.future.cancel()
        self.worker.submit(self.session.close)
        super().destroy()
