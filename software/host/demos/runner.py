"""Headless demo checks using the very same resident inference sessions as the UI."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from npu_program import decode_tensor
from demos.inference import InferenceSession
from demos.signals import Sample, ecg_record, har_dataset, phone_csv
from demos.vision import MODEL_ROOT


NAMES = dict(mnist="mnist_l", kws="kws_l", ecg="ecg", har="har", face="face", emotion="emotion")


def make_session(args, name):
    root = Path(args.vision_models) if args.vision_models else MODEL_ROOT
    return InferenceSession(args, NAMES[name], root/name if name in ("face","emotion") else None)


def input_samples(args, session):
    import numpy as np
    path=args.input
    suffix=path.suffix.lower()
    if suffix==".json":
        blob=json.loads(path.read_text(encoding="utf-8"))
        frames=blob["frames"] if "frames" in blob else [blob]
        return [Sample(str(f.get("index",i)),f["input"],np.array([]),f.get("label"),f.get("output")) for i,f in enumerate(frames[:args.limit])]
    if suffix==".bin":
        values=decode_tensor(path.read_bytes(),session.package.manifest['input_dtype'],session.package.manifest['input_elements'])
        return [Sample(path.name,values,np.array([]))]
    if args.demo=="ecg" and suffix==".hea":
        return ecg_record(path,session.scale,args.limit)
    if args.demo=="har":
        return phone_csv(path,session.scale,args.imu_rotation)[:args.limit] if suffix==".csv" else har_dataset(path,session.scale,args.limit)
    if args.demo=="kws" and suffix==".wav":
        from demos.kws_audio import read_pcm,one_second,mfcc,quantize
        stats=json.loads(Path(__file__).with_name("kws_normalization.json").read_text())
        audio=one_second(*read_pcm(str(path)))
        values=quantize(mfcc(audio),stats['mean'],stats['std'],session.scale).ravel().tolist()
        return [Sample(path.name,values,audio)]
    if args.demo=="mnist":
        from PIL import Image,ImageOps
        from demos.mnist_input import prepare
        with Image.open(path) as source:
            image=source.convert('L')
        corners=[image.getpixel(p) for p in ((0,0),(image.width-1,0),(0,image.height-1),(image.width-1,image.height-1))]
        if sum(corners)>4*127:
            image=ImageOps.invert(image)
        prepared=prepare(image,session.scale)
        if prepared is None:
            raise ValueError("图像中没有笔迹")
        return [Sample(path.name,prepared[1],np.asarray(prepared[0]))]
    raise ValueError(f"{args.demo} 不支持此输入类型：{suffix}")


def vision_input(args):
    from PIL import Image,ImageOps
    from demos.vision import YuNet,prepare_image,letterbox,decode_faces,unmap_boxes,face_crop
    with Image.open(args.input) as source:
        image=ImageOps.exif_transpose(source).convert('RGB')
    prediction=None
    session=None
    started=time.perf_counter()
    try:
        if args.demo=='face' and args.face_mode=='npu':
            session=make_session(args,'face')
            session.connect()
            frame,transform=letterbox(image,32)
            prediction=session.infer(prepare_image(frame,session.scale))
            boxes=unmap_boxes(decode_faces(prediction.scores,session.metadata,(32,32)),transform,image.size)
        else:
            boxes=YuNet(args.yunet).detect(image)
            if args.demo=='emotion' and boxes:
                session=make_session(args,'emotion')
                session.connect()
                box=max(boxes,key=lambda b:(b[2]-b[0])*(b[3]-b[1]))
                side=session.package.manifest['tensors']['input']['shape'][0]
                prediction=session.infer(prepare_image(face_crop(image,box),session.scale,size=side))
                boxes=[box]
        return dict(task=args.demo,mode=args.face_mode if args.demo=='face' else 'emotion',
                    backend='host' if args.demo=='face' and args.face_mode=='host' else args.backend,
                    boxes=boxes,total_ms=(time.perf_counter()-started)*1000,
                    prediction=prediction.to_dict() if prediction else None,
                    label=session.metadata['classes'][prediction.index] if prediction and args.demo=='emotion' else None)
    finally:
        if session:
            session.close()


def run(args, demos):
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Keep evidence from an earlier run intact.
    with args.output.open('x',encoding='utf-8') as handle:
        report=dict(started_utc=datetime.now(timezone.utc).isoformat(),backend=args.backend,passed=False,
                    kind='golden_exact' if args.self_test else 'input_inference',runs=[],errors=[],fpga_validated=False)
        try:
            if args.input and args.demo in ('face','emotion') and args.input.suffix.lower() not in ('.json','.bin'):
                if args.demo=='face' and args.face_mode=='host':
                    report['backend']='host'
                report['runs'].append(vision_input(args))
            else:
                names=[args.demo] if args.demo else list(demos)
                for name in names:
                    session=None
                    try:
                        session=make_session(args,name)
                        session.connect()
                        if args.self_test:
                            golden=json.loads((session.directory/'golden.json').read_text(encoding='utf-8'))
                            samples=[Sample(str(f['index']),f['input'],None,f.get('label'),f['output']) for f in golden['frames'][:args.limit]]
                        else:
                            samples=input_samples(args,session)
                        if not samples:
                            raise ValueError('没有输入窗口')
                        for sample in samples:
                            prediction=session.infer(sample.values)
                            exact=list(prediction.scores)==sample.expected_scores if sample.expected_scores is not None else None
                            report['runs'].append(dict(demo=name,sample=sample.name,expected=sample.expected,
                                                       exact=exact,preprocess_ms=sample.preprocess_ms,
                                                       model_sha256=hashlib.sha256(session.package.image).hexdigest(),
                                                       input_sha256=hashlib.sha256(bytes(v&255 for v in sample.values)).hexdigest(),
                                                       **prediction.to_dict()))
                            if exact is False:
                                report['errors'].append(f'{name} {sample.name}: golden output differs')
                        print(f"{name}: {len(samples)} inputs checked",flush=True)
                    except Exception as exc:
                        report['errors'].append(f'{name}: {type(exc).__name__}: {exc}')
                    finally:
                        if session:
                            session.close()
            report['passed']=bool(report['runs']) and not report['errors']
            report['fpga_validated']=report['passed'] and args.backend=='fpga' and args.self_test
        except Exception as exc:
            report['errors'].append(f'{type(exc).__name__}: {exc}')
        finally:
            json.dump(report,handle,ensure_ascii=False,indent=2)
            handle.write('\n')
    print(f"{'PASS' if report['passed'] else 'FAIL'}: {args.output}",flush=True)
    for error in report['errors']:
        print(error,flush=True)
    return report['passed']
