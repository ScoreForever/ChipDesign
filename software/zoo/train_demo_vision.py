"""Train compact FER2013 and near-field WIDER FACE INT4 demo packages.

Uses the existing NPU QAT/exporter. Training/validation/test images stay
disjoint; WIDER's annotated validation images are split for selection/scoring.
No dataset images are included in the published package.
"""

import argparse
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import time
import zipfile

import numpy as np
from PIL import Image
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "software"), str(ROOT / "software/host")]
from zoo.models import ds, res
from zoo.qat import Net, annotate_shapes, export
from zoo.train import int_accuracy, quantize_input
from npu_program import compile_model, reference, encode_tensor
import npu_fastref


CLASSES = ["angry", "disgust", "fear", "happy", "sad", "surprise", "neutral"]
EMOTION_SPEC = [dict(type="conv", out=c, k=[3,3], p=[1,1,1,1], pool="max2") for c in (16,24,16)] + [
                dict(type="fc", out=7, spatial=36, last=True)]
FACE_SPEC = [dict(type="conv", out=c, k=[3,3], p=[1,1,1,1], pool="max2") for c in (16,24,32)] + [
             dict(type="conv", out=5, k=[1, 1], relu=False, last=True)]


def pixels(image, side=32):
    return np.asarray(image.convert("L").resize((side, side), Image.Resampling.BILINEAR), dtype=np.float32) / 127.5 - 1


def fer_data(directory):
    import pyarrow.parquet as pq
    result = {}
    for split, source in (("train", "train"), ("val", "publicTest"), ("test", "privateTest")):
        path, = Path(directory).glob(source + "-*.parquet")
        rows = pq.read_table(path).to_pylist()
        images, labels = [], []
        for row in rows:
            image = row["image"]
            images.append(pixels(Image.open(io.BytesIO(image["bytes"])),48))
            labels.append(int(row["label"]))
        x = torch.tensor(np.stack(images)).unsqueeze(1)
        y = torch.tensor(labels)
        if y.min() < 0 or y.max() > 6:
            raise ValueError("FER label mapping must be 0..6")
        result[split] = (x, y)
    return result


def annotations(archive, split):
    with zipfile.ZipFile(archive) as z:
        path, = [n for n in z.namelist() if n.endswith(f"wider_face_{split}_bbx_gt.txt")]
        lines = iter(z.read(path).decode().splitlines())
    result = []
    for name in lines:
        count = int(next(lines))
        boxes = []
        for _ in range(max(1, count)):
            values = list(map(int, next(lines).split()))
            if count and values[2] >= 12 and values[3] >= 12 and values[7] == 0:
                boxes.append(values[:4])
        if boxes:
            result.append((name, boxes))
    return result


def iou(box, others):
    if not len(others):
        return np.zeros(0)
    box, others = np.asarray(box), np.asarray(others)
    lo, hi = np.maximum(box[:2], others[:, :2]), np.minimum(box[2:], others[:, 2:])
    inter = np.maximum(hi - lo, 0).prod(axis=1)
    return inter / np.maximum(1e-8, (box[2:] - box[:2]).prod() + (others[:, 2:] - others[:, :2]).prod(axis=1) - inter)


def wider_data(directory, train_count=2400, val_count=600, seed=17):
    """Controlled near-field crops, with explicit empty background negatives."""
    directory = Path(directory)
    train = annotations(directory / "wider_face_split.zip", "train")
    val = annotations(directory / "wider_face_split.zip", "val")
    rng = random.Random(seed)
    rng.shuffle(train)
    rng.shuffle(val)
    groups = dict(train=(train[:train_count], "train"), val=(val[:val_count // 2], "val"),
                  test=(val[val_count // 2:val_count], "val"))
    result, source_names = {}, {}
    for split, (records, archive_name) in groups.items():
        images, targets, ids = [], [], []
        with zipfile.ZipFile(directory / f"WIDER_{archive_name}.zip") as archive:
            for name, raw_boxes in records:
                with Image.open(io.BytesIO(archive.read(f"WIDER_{archive_name}/images/{name}"))) as image:
                    image = image.convert("RGB")
                    boxes = np.array(raw_boxes, dtype=float)
                    boxes[:, 2:] += boxes[:, :2]
                    # The largest valid face supplies a near-field training crop.
                    target = boxes[np.argmax((boxes[:, 2:] - boxes[:, :2]).prod(axis=1))]
                    size = max(target[2:] - target[:2]) / rng.uniform(0.25, 0.7)
                    size = max(32, min(size, *image.size))
                    cx, cy = (target[:2] + target[2:]) / 2
                    x = int(np.clip(cx - size * rng.uniform(0.3, 0.7), 0, max(0, image.width - size)))
                    y = int(np.clip(cy - size * rng.uniform(0.3, 0.7), 0, max(0, image.height - size)))
                    regions = [(x, y, x + int(size), y + int(size))]
                    # A background is labelled empty only if it overlaps no annotation.
                    for _ in range(6):
                        side = rng.randint(max(16, min(image.size) // 4), max(16, min(image.size)))
                        xx, yy = rng.randint(0, max(0, image.width - side)), rng.randint(0, max(0, image.height - side))
                        candidate = (xx, yy, xx + side, yy + side)
                        overlap_lo = np.maximum(np.array(candidate[:2]), boxes[:, :2])
                        overlap_hi = np.minimum(np.array(candidate[2:]), boxes[:, 2:])
                        if not (np.maximum(overlap_hi - overlap_lo, 0).prod(axis=1) > 0).any():
                            regions.append(candidate)
                            break
                    for region in regions:
                        left, top, right, bottom = region
                        normalized = (boxes - np.array([left, top, left, top])) / np.array([right-left, bottom-top]*2)
                        normalized = normalized.clip(0, 1)
                        labels = np.zeros((4, 4, 5), dtype=np.float32)
                        for b in sorted(normalized, key=lambda b: -np.prod(b[2:] - b[:2])):
                            width, height = b[2:] - b[:2]
                            if min(width, height) < 0.16:
                                continue
                            center = (b[:2] + b[2:]) / 2
                            col, row = np.minimum((center * 4).astype(int), 3)
                            if labels[row, col, 0] == 0:
                                labels[row, col] = [1, center[0]*4-col, center[1]*4-row, width, height]
                        images.append(pixels(image.crop(region)))
                        targets.append(labels)
                        ids.append(name)
        result[split] = (torch.tensor(np.stack(images)).unsqueeze(1), torch.tensor(np.stack(targets)))
        source_names[split] = sorted(set(ids))
    if set(source_names['val']) & set(source_names['test']):
        raise AssertionError("WIDER validation/test image leakage")
    return result, source_names


def face_loss(output, target):
    out = output.permute(0, 2, 3, 1)
    mask = target[..., 0] > 0
    classification = F.binary_cross_entropy_with_logits(out[..., 0], target[..., 0], pos_weight=out.new_tensor(8.0))
    box = F.smooth_l1_loss(out[..., 1:][mask], target[..., 1:][mask]) if mask.any() else out.sum() * 0
    return classification + 5 * box


def face_metrics(out, labels, threshold=0.6):
    """One-to-one box matching, IoU>=0.5, for the controlled evaluation crops."""
    from demos.vision import decode_faces
    tp = fp = total = 0
    for values, target in zip(out, labels):
        candidates = decode_faces(values.reshape(-1), dict(grid=[4, 4], output_scale=1.0), (32, 32), threshold=threshold)
        gt = []
        for row, col in zip(*np.nonzero(target[..., 0] > 0)):
            _, ox, oy, w, h = target[row, col]
            cx, cy = (col + ox) / 4, (row + oy) / 4
            gt.append([cx-w/2, cy-h/2, cx+w/2, cy+h/2])
        total += len(gt)
        used = set()
        for box in candidates:
            overlap = iou(np.array(box[:4]) / 32, gt)
            best = int(overlap.argmax()) if len(overlap) else -1
            if best >= 0 and overlap[best] >= 0.5 and best not in used:
                tp += 1
                used.add(best)
            else:
                fp += 1
    return dict(precision=tp/max(1,tp+fp), recall=tp/max(1,total), true_positive=tp, false_positive=fp, ground_truth=total)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("task", choices=("emotion", "face"))
    p.add_argument("--data", type=Path, default=ROOT / "build/demo_data")
    p.add_argument("--output", type=Path, default=ROOT / "software/host/demos/models")
    p.add_argument("--fp-epochs", type=int, default=35)
    p.add_argument("--qat-epochs", type=int, default=20)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--train-count", type=int, default=8000)
    p.add_argument("--val-count", type=int, default=1000)
    args = p.parse_args()
    torch.set_num_threads(6)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    sys.path.insert(0, str(ROOT / "software/host"))
    device = "cuda" if torch.cuda.is_available() else "cpu"
    out = args.output / args.task
    out.mkdir(parents=True, exist_ok=False)
    started = time.time()
    log_handle = (out / "train.log").open("w", encoding="utf-8")
    def log(text):
        print(text, flush=True)
        log_handle.write(text + "\n")
        log_handle.flush()
    log(f"device={device} seed={args.seed}")
    if args.task == "emotion":
        data, sources = fer_data(args.data / "fer"), {"splits": "FER2013 train/publicTest/privateTest; standard 7-class label order"}
        spec = EMOTION_SPEC
        metadata = dict(task="expression_classification", classes=CLASSES, preprocess="grayscale bilinear 48x48, pixel/127.5-1")
    else:
        data, sources = wider_data(args.data / "wider", args.train_count, args.val_count, args.seed)
        spec = FACE_SPEC
        metadata = dict(task="face_detection", grid=[4, 4], classes=["face"], threshold=0.6,
                        preprocess="grayscale bilinear 32x32, pixel/127.5-1",
                        box_format="objectness_logit,cell_x_offset,cell_y_offset,width_fraction,height_fraction",
                        scope="WIDER-derived near-field crops; no full WIDER AP or participant validation")
    data = {k: (x.to(device), y.to(device)) for k, (x,y) in data.items()}
    log(str({k: len(v[1]) for k,v in data.items()}))
    shape = (48,48,1) if args.task == 'emotion' else (32,32,1)
    net = Net(spec, shape).to(device)
    annotate_shapes(net)
    probe = copy.deepcopy(net).to_qat()
    with torch.no_grad():
        probe(data['train'][0][:64])
    capacity = compile_model(export(probe, args.task, metadata))
    log(f"probe capacity {capacity.manifest['model_bytes']} B, activations {capacity.manifest['activation_peak']} B")
    if capacity.manifest['model_bytes'] > 8192:
        raise ValueError("model exceeds SRAM model partition")
    del probe
    def metric(model, split):
        x, y = data[split]
        if args.task == "emotion":
            acc, _ = int_accuracy(model, x, y, device)
            return acc, dict(accuracy=acc, samples=len(y))
        codes = quantize_input(x, model['metadata']['input_scale'])
        result = npu_fastref.run(model, codes, device).float().cpu().numpy() * model['metadata']['output_scale']
        m = face_metrics(result, y.cpu().numpy())
        score = 2*m['precision']*m['recall']/max(1e-9,m['precision']+m['recall'])
        return score, dict(m, f1=score, samples=len(y))
    for phase, epochs in (("float", args.fp_epochs), ("qat", args.qat_epochs)):
        if phase == "qat":
            net.eval().to_qat()
            with torch.no_grad():
                net(data['train'][0][:512])
        optimizer = torch.optim.AdamW(net.parameters(), lr=0.002 if phase == 'float' else 0.0003,
                                      weight_decay=0.0002 if phase == 'float' else 0)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, max(1,epochs))
        best, state = -1, None
        for epoch in range(epochs):
            net.train()
            x,y = data['train']
            order = torch.randperm(len(y), device=device)
            for at in range(0,len(y),128):
                ids = order[at:at+128]
                xb,yb = x[ids],y[ids]
                if args.task == 'emotion' and random.random() < .5:
                    xb = xb.flip(3)
                optimizer.zero_grad(set_to_none=True)
                logits = net(xb)
                loss = F.cross_entropy(logits, yb, label_smoothing=.05) if args.task == 'emotion' else face_loss(logits,yb)
                loss.backward()
                optimizer.step()
            scheduler.step()
            net.eval()
            with torch.no_grad():
                if phase == 'qat':
                    score, details = metric(export(net,args.task,metadata), 'val')
                else:
                    vx,vy=data['val']
                    outputs = torch.cat([net(vx[i:i+512]) for i in range(0,len(vx),512)])
                    if args.task=='emotion':
                        score=(outputs.argmax(1)==vy).float().mean().item()
                    else:
                        m=face_metrics(outputs.permute(0,2,3,1).cpu().numpy(),vy.cpu().numpy())
                        score=2*m['precision']*m['recall']/max(1e-9,m['precision']+m['recall'])
                if score > best:
                    best,state=score,copy.deepcopy(net.state_dict())
            log(f"{phase} {epoch+1}/{epochs} loss={loss.item():.5f} val={score:.5f} best={best:.5f}")
        net.load_state_dict(state)
    torch.save(net.state_dict(), ROOT/'build'/f'{args.task}_demo_qat.pt')
    model=export(net,args.task,metadata)
    model['metadata']['accuracy_validated'] = False
    _,evaluation=metric(model,'test')
    model['metadata']['evaluation']=evaluation
    for cols in (8,):
        compile_model(model,cols=cols).save(out/f'c{cols}')
    # A few held-out inputs, with every INT32 output checked against fastref.
    x,y=data['test']
    frames=quantize_input(x[:8],model['metadata']['input_scale'])
    batch = npu_fastref.run(model,frames,device).cpu()
    for i,frame in enumerate(frames):
        expected=reference(model,frame.flatten().tolist())[model['nodes'][-1]['output']]
        if batch[i].flatten().tolist()!=expected:
            raise AssertionError(f'fastref differs on test frame {i}')
    capacity=compile_model(model)
    hardware=dict(c8={k:capacity.manifest[k] for k in ('model_bytes','activation_peak','op_count')},bit_exact_frames=len(frames))
    package=compile_model(model)
    rows=[]
    for i,frame in enumerate(frames):
        values=frame.flatten().tolist()
        scores=reference(model,values)[model['nodes'][-1]['output']]
        row=dict(index=i,input=values,output=scores,input_hex=encode_tensor(values,'i4').hex(),output_hex=encode_tensor(scores,'i32').hex())
        if args.task=='emotion':
            row.update(label=int(y[i]),predicted=max(range(len(scores)),key=scores.__getitem__))
        rows.append(row)
    (out/'model.json').write_text(json.dumps(model)+'\n',encoding='utf-8')
    (out/'golden.json').write_text(json.dumps(dict(model=args.task,input_shape=list(shape),input_scale=model['metadata']['input_scale'],frames=rows))+'\n',encoding='utf-8')
    report=dict(task=args.task,seed=args.seed,device=device,evaluation=evaluation,hardware=hardware,
                split_counts={k:len(v[1]) for k,v in data.items()},source_images=sources,
                seconds=time.time()-started,args={k:str(v) for k,v in vars(args).items()},
                fpga_validated=False,participant_validated=False,
                dataset_sources=json.loads((args.data/'all_sources.json').read_text()) if (args.data/'all_sources.json').exists() else {},
                training_source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                       for p in (Path(__file__),ROOT/'software/zoo/qat.py')})
    (out/'training_result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    torch.save(net.state_dict(), ROOT/'build'/f'{args.task}_demo_qat.pt')
    log(json.dumps(evaluation))
    log_handle.close()
    hashes={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
    (out/'SHA256SUMS.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':
    main()
