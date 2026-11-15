"""Local YuNet and NPU dense face-head preprocessing/decoding."""

import math
from pathlib import Path

import numpy as np
from PIL import Image

from demos.signals import quantize


MODEL_ROOT = Path(__file__).with_name("models")


def prepare_image(image, scale, size=32):
    image = image.convert("L").resize((size, size), Image.Resampling.BILINEAR)
    return quantize(np.asarray(image, dtype=np.float32) / 127.5 - 1, scale)


def letterbox(image, side):
    if image.width <= 0 or image.height <= 0:
        raise ValueError("图像尺寸无效")
    ratio = side / max(image.size)
    width, height = max(1, round(image.width * ratio)), max(1, round(image.height * ratio))
    offset = ((side - width) // 2, (side - height) // 2)
    frame = Image.new("RGB", (side, side))
    frame.paste(image.convert("RGB").resize((width, height), Image.Resampling.BILINEAR), offset)
    return frame, (width / image.width, height / image.height, *offset)


def unmap_boxes(boxes, transform, size):
    sx, sy, ox, oy = transform
    width, height = size
    result = []
    for x1, y1, x2, y2, score in boxes:
        box = [max(0., min(float(width), (x1-ox)/sx)), max(0., min(float(height), (y1-oy)/sy)),
               max(0., min(float(width), (x2-ox)/sx)), max(0., min(float(height), (y2-oy)/sy)), float(score)]
        if box[2] > box[0] and box[3] > box[1]:
            result.append([float(value) for value in box])
    return result


def overlap(a, b):
    inter = max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter / max(1e-9, (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)


def decode_faces(scores, metadata, size, threshold=None):
    rows, cols = metadata["grid"]
    values = np.asarray(scores, dtype=float)
    if values.size != rows * cols * 5 or not np.isfinite(values).all():
        raise ValueError("人脸检测头输出形状/数值无效")
    scale = metadata["output_scale"]
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("人脸检测头 output_scale 无效")
    values = values.reshape(rows, cols, 5) * scale
    threshold = metadata.get("threshold", .6) if threshold is None else threshold
    if not 0 < threshold < 1:
        raise ValueError("人脸阈值须介于 0 和 1")
    width, height = size
    boxes = []
    for row in range(rows):
        for col in range(cols):
            logit, x, y, w, h = values[row, col]
            score = 1 / (1 + math.exp(-max(-60., min(60., logit))))
            if score < threshold or w <= 0 or h <= 0:
                continue
            cx, cy = (col + np.clip(x,0,1))/cols, (row + np.clip(y,0,1))/rows
            w, h = min(w,1), min(h,1)
            box = [max(0,cx-w/2)*width, max(0,cy-h/2)*height,
                   min(1,cx+w/2)*width, min(1,cy+h/2)*height, score]
            if box[2] > box[0] and box[3] > box[1]:
                boxes.append(box)
    kept = []
    for box in sorted(boxes,key=lambda b:-b[4]):
        if not any(overlap(box, old) > .3 for old in kept):
            kept.append([float(v) for v in box])
    return kept


class YuNet:
    def __init__(self, path):
        import cv2
        if not Path(path).is_file():
            raise ValueError("缺少 YuNet 模型。先运行 demos/fetch_assets.py yunet，或通过 --yunet 指定文件。")
        self.detector = cv2.FaceDetectorYN.create(str(path), "", (320,320), .8, .3, 5000)

    def detect(self, image):
        frame, transform = letterbox(image,320)
        _, faces = self.detector.detect(np.asarray(frame)[:,:,::-1].copy())
        if faces is None:
            return []
        boxes = [[f[0], f[1], f[0]+f[2], f[1]+f[3], f[-1]] for f in faces]
        return unmap_boxes(boxes, transform, image.size)


def face_crop(image, box):
    x1,y1,x2,y2 = box[:4]
    margin = .08 * max(x2-x1,y2-y1)
    bounds=(max(0,int(x1-margin)),max(0,int(y1-margin)),min(image.width,math.ceil(x2+margin)),min(image.height,math.ceil(y2+margin)))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("人脸裁剪框无效")
    return image.crop(bounds)
