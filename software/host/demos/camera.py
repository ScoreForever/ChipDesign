"""Camera/image demos: host face boxes, NPU boxes, or NPU expression scores."""

import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, ttk

from PIL import Image, ImageDraw, ImageOps

from demos.inference import InferenceSession
from demos.vision import MODEL_ROOT, YuNet, decode_faces, face_crop, letterbox, prepare_image, unmap_boxes
from demos.window import ACCENT, BACKGROUND, DRAWING, ERROR, MUTED, PANEL


EMOTIONS = dict(angry="生气", disgust="厌恶", fear="害怕", happy="开心", sad="难过", surprise="惊讶", neutral="平静")


class CameraDemo(tk.Frame):
    def __init__(self, parent, app, name):
        self.app, self.name = app, name
        self.mode = app.args.face_mode if name == "face" else "emotion"
        models = Path(app.args.vision_models) if app.args.vision_models else MODEL_ROOT
        self.session = InferenceSession(app.args, name, models / name) if self.mode != "host" else None
        super().__init__(parent, bg=BACKGROUND)
        self.worker = app.worker
        self.future = self.poll = self.timer = None
        self.capture = self.detector = None
        self.streaming = self.ready = False
        self.photo = self.last_image = None
        self.records = []
        app.label(self, "表情分类" if name == "emotion" else "人脸框选", 24, bold=True).pack(anchor="w")
        source = "电脑 · YuNet" if self.mode == "host" else self.session.source
        description = "电脑检测裁脸 → INT4 表情分类；模型类别分数仅描述图像表情。" if name == "emotion" else (
            "电脑 YuNet 框选；NPU 框选可用 --face-mode npu 切换。" if self.mode == "host" else
            "INT4 近景人脸检测头输出网格候选，电脑解码并抑制重叠框。")
        app.label(self, description, fg=MUTED).pack(anchor="w", pady=(4,12))
        self.preview = tk.Label(self, bg=DRAWING, width=1, height=1)
        self.preview.pack(fill="both", expand=True)
        tools = tk.Frame(self,bg=BACKGROUND)
        tools.pack(fill="x",pady=10)
        self.buttons=[]
        for title, command in (("打开图片",self.choose_image),("启动摄像头",self.start_camera),("停止摄像头",self.stop_camera)):
            b=ttk.Button(tools,text=title,command=command,style="Demo.TButton")
            b.pack(side="left",padx=(0,8))
            self.buttons.append(b)
        ttk.Button(tools,text="保存结果",command=self.save,style="Demo.TButton").pack(side="right")
        panel=tk.Frame(self,bg=PANEL,padx=14,pady=10)
        panel.pack(fill="x")
        self.result=app.label(panel,"—",20,fg=ACCENT,bold=True)
        self.result.pack(anchor="w")
        self.scores=app.label(panel,"",10,wraplength=700,justify="left")
        self.scores.pack(anchor="w",pady=(4,0))
        self.latency=app.label(panel,source,10,fg=MUTED)
        self.latency.pack(anchor="w",pady=(5,0))
        self.status=app.label(self,"",10,fg=MUTED,wraplength=700,justify="left")
        self.status.pack(anchor="w",pady=8)
        self.retry=ttk.Button(self,text="重新连接",command=self.connect,style="Demo.TButton")
        self.connect()

    def controls(self, enabled):
        for b in self.buttons[:2]:
            b.state(["!disabled"] if enabled else ["disabled"])

    def connect(self):
        if self.future is not None:
            return
        self.retry.pack_forget()
        self.run(self.initialize,self.connected,"正在准备模型（不会自动打开摄像头）")

    def initialize(self):
        if self.mode != "npu":
            self.detector=YuNet(self.app.args.yunet)
        if self.session:
            self.session.connect()

    def connected(self,_):
        self.ready=True
        self.controls(True)
        self.status.config(text="就绪；打开图片或手动启动摄像头。" +
                           (" 实验模型，尚未完成现场验收。" if self.mode != 'host' else ""))

    def run(self,job,callback,text):
        if self.future is not None:
            return
        self.controls(False)
        self.status.config(text=text,fg=MUTED)
        self.future=self.worker.submit(job)
        self.poll=self.after(30,self.finish,callback)

    def finish(self,callback):
        self.poll=None
        if not self.future.done():
            self.poll=self.after(30,self.finish,callback)
            return
        future,self.future=self.future,None
        try:
            value=future.result()
        except Exception as exc:
            self.stop_camera()
            self.status.config(text=str(exc),fg=ERROR)
            if self.session:
                self.ready=False
                self.worker.submit(self.session.close)
            self.retry.pack(anchor="w")
            self.controls(self.ready)
            return
        self.controls(self.ready and not self.streaming)
        callback(value)

    def choose_image(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[("Images","*.jpg *.jpeg *.png *.bmp")])
        if path:
            self.stop_camera()
            def job():
                with Image.open(path) as image:
                    return self.process(ImageOps.exif_transpose(image).convert("RGB"))
            self.run(job,self.show_result,"正在处理图片")

    def start_camera(self):
        if not self.ready or self.future is not None or self.streaming:
            return
        self.streaming=True
        def job():
            import cv2
            self.capture=cv2.VideoCapture(self.app.args.camera_index)
            self.capture.set(cv2.CAP_PROP_FRAME_WIDTH,640)
            self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT,480)
            if not self.capture.isOpened():
                raise OSError("无法打开摄像头，请检查设备编号和系统权限")
            return self.read_camera()
        self.run(job,self.show_result,"正在打开摄像头")

    def read_camera(self):
        ok,frame=self.capture.read()
        if not ok:
            raise OSError("无法读取摄像头画面")
        return self.process(Image.fromarray(frame[:,:,::-1]))

    def process(self,image):
        started=time.perf_counter()
        prediction=None
        if self.mode=="npu":
            frame,transform=letterbox(image,32)
            values=prepare_image(frame,self.session.scale)
            preprocess_ms=(time.perf_counter()-started)*1000
            prediction=self.session.infer(values)
            boxes=unmap_boxes(decode_faces(prediction.scores,self.session.metadata,(32,32)),transform,image.size)
        else:
            boxes=self.detector.detect(image)
            preprocess_ms=(time.perf_counter()-started)*1000
        label=f"检测到 {len(boxes)} 张人脸"
        if self.mode=="emotion" and boxes:
            box=max(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
            crop=face_crop(image,box)
            before=time.perf_counter()
            side = self.session.package.manifest['tensors']['input']['shape'][0]
            values=prepare_image(crop,self.session.scale,size=side)
            preprocess_ms+=(time.perf_counter()-before)*1000
            prediction=self.session.infer(values)
            label=EMOTIONS.get(self.session.metadata['classes'][prediction.index],"未知")
            boxes=[box]
        record=dict(task=self.name,mode=self.mode,backend="host" if self.mode=="host" else self.app.args.backend,
                    boxes=boxes,label=label,preprocess_ms=preprocess_ms,total_ms=(time.perf_counter()-started)*1000,
                    prediction=None if prediction is None else prediction.to_dict())
        marked=image.copy()
        draw=ImageDraw.Draw(marked)
        for x1,y1,x2,y2,score in boxes:
            draw.rectangle((x1,y1,x2,y2),outline="#90ecd2",width=max(2,image.width//220))
            draw.text((max(0,x1),max(0,y1-12)),f"face {score:.2f}",fill="#90ecd2")
        return marked,record,prediction

    def show_result(self,value):
        image,record,prediction=value
        self.last_image=image
        frame=image.copy()
        frame.thumbnail((700,310))
        frame=frame.convert("RGB")
        self.photo=tk.PhotoImage(master=self,data=f"P6\n{frame.width} {frame.height}\n255\n".encode()+frame.tobytes(),format="PPM")
        self.preview.config(image=self.photo,width=frame.width,height=frame.height)
        self.result.config(text=record['label'])
        if prediction and self.mode=="emotion":
            self.scores.config(text="原始 INT32 分数（非概率）："+" · ".join(f"{EMOTIONS.get(k,k)} {v}" for k,v in zip(self.session.metadata['classes'],prediction.scores)))
        else:
            self.scores.config(text="无人脸时不执行表情分类" if self.mode=="emotion" else "")
        source="电脑 · YuNet" if self.mode=="host" else self.session.source
        self.latency.config(text=source+" · "+(prediction.timing_text() if prediction else f"主机处理 {record['total_ms']:.1f} ms"))
        self.status.config(text=f"采集后处理合计 {record['total_ms']:.1f} ms · 预处理/检测 {record['preprocess_ms']:.1f} ms" +
                           (" · 实验模型，尚未现场验收" if self.mode != 'host' else ""))
        self.records.append(record)
        # Bound memory during long live demonstrations.
        self.records=self.records[-2000:]
        if self.streaming:
            self.timer=self.after(150,self.next_frame)

    def next_frame(self):
        self.timer=None
        if self.streaming and self.future is None:
            self.run(self.read_camera,self.show_result,"正在处理摄像头画面")

    def release_camera(self):
        if self.capture is not None:
            self.capture.release()
            self.capture=None

    def stop_camera(self):
        self.streaming=False
        if self.timer is not None:
            self.after_cancel(self.timer)
            self.timer=None
        self.worker.submit(self.release_camera)
        self.controls(self.ready and self.future is None)

    def save(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension=".json",initialfile=f"{self.name}_demo_results.json")
        if path:
            try:
                Path(path).write_text(json.dumps(dict(records=self.records),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
            except OSError as exc:
                self.status.config(text=str(exc),fg=ERROR)

    def destroy(self):
        self.stop_camera()
        if self.poll is not None:
            self.after_cancel(self.poll)
        if self.future is not None:
            self.future.cancel()
        if self.session:
            self.worker.submit(self.session.close)
        super().destroy()
