"""Rebuild complete public test splits and reevaluate the frozen ABI 4 zoo."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'software/host'),str(ROOT/'software'),str(ROOT/'software/zoo')]
import torch
import npu_fastref
from zoo import data as datasets
from zoo.hwopt import frames_for
from train import quantize_input
from npu_program import compile_model, encode_tensor


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--models',default='mnist_l,fashion_cnn,har,ecg,gtsrb_l,kws_l,kws_m')
    p.add_argument('--batch-size',type=int,default=256)
    args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(8)
    torch.backends.cuda.matmul.allow_tf32=False
    torch.backends.cudnn.allow_tf32=False
    device='cuda' if torch.cuda.is_available() else 'cpu'
    baseline={r['model']:r for r in json.loads((ROOT/'docs/validation/int4_v2/zoo_eval.json').read_text())}
    for name in args.models.split(','):
        out=args.output/name;out.mkdir(exist_ok=False)
        run=ROOT/'software/zoo/pretrained'/name
        model=json.loads((run/'model.json').read_text());golden=json.loads((run/'golden.json').read_text())
        report=dict(model=name,passed=False,started_utc=datetime.now(timezone.utc).isoformat(),
            model_sha256=sha(run/'model.json'),golden_sha256=sha(run/'golden.json'),
            device=device,batch_size=args.batch_size,
            versions={n:importlib.metadata.version(n) for n in ('torch','torchvision','torchaudio','numpy','pillow','wfdb','soundfile')},
            loader_sha256=sha(ROOT/'software/zoo/data.py'),fastref_sha256=sha(ROOT/'software/host/npu_fastref.py'))
        try:
            print('REBUILD',name,flush=True);started=time.monotonic()
            raw=datasets.LOADERS[name](args.data)
            x,y=raw['test'];y=y.cpu().long()
            assert len(y)==baseline[name]['test_samples'],(name,len(y),baseline[name]['test_samples'])
            frames=frames_for(model,quantize_input(x.cpu(),model['metadata']['input_scale'])).to(torch.int8).contiguous()
            report['test_samples']=len(y)
            for row in golden['frames']:
                values=frames[row['index']].flatten().tolist()
                assert values==row['input'],(name,row['index'],'rebuilt input mismatch')
                assert int(y[row['index']])==row['label'],(name,row['index'],'label mismatch')
                assert encode_tensor(values,golden['input_dtype']).hex()==row['input_hex']
            report['golden_input_and_label_matches']=len(golden['frames'])
            with torch.inference_mode():
                test=npu_fastref.run(model,frames[[r['index'] for r in golden['frames']]].to(device),device)
                outputs=test.reshape(len(golden['frames']),-1).cpu().tolist()
            for output,row in zip(outputs,golden['frames']):assert output==row['output'],(name,row['index'],'fastref differs from published scalar/RTL golden')
            report['golden_output_matches']=len(outputs)
            report['frames_sha256']=hashlib.sha256(frames.numpy().tobytes()).hexdigest()
            report['labels_sha256']=hashlib.sha256(y.to(torch.int16).numpy().tobytes()).hexdigest()
            torch.save(dict(frames=frames,labels=y.to(torch.int16)),out/'test_frames.pt')
            preds=[];outputs_sha=hashlib.sha256()
            if device=='cuda':torch.cuda.reset_peak_memory_stats()
            evaluated=time.monotonic();last=evaluated
            with torch.inference_mode():
                for i in range(0,len(y),args.batch_size):
                    result=npu_fastref.run(model,frames[i:i+args.batch_size].to(device),device)
                    logits=result.reshape(result.shape[0],-1).cpu()
                    outputs_sha.update(logits.numpy().tobytes())
                    preds.append(logits.argmax(1))
                    if time.monotonic()-last>15:
                        print('EVAL',name,min(i+args.batch_size,len(y)),'/',len(y),flush=True);last=time.monotonic()
            pred=torch.cat(preds);classes=int(y.max())+1
            cm=torch.bincount(y*classes+pred,minlength=classes*classes).reshape(classes,classes)
            recall=[int(cm[c,c])/max(1,int(cm[c].sum())) for c in range(classes)]
            accuracy=(pred==y).float().mean().item()
            report.update(correct=int((pred==y).sum()),int4_test=accuracy,
                int4_test_balanced=math.fsum(recall)/classes,recall=recall,confusion=cm.tolist(),
                class_counts=cm.sum(1).tolist(),outputs_sha256=outputs_sha.hexdigest(),
                evaluation_seconds=time.monotonic()-evaluated,total_seconds=time.monotonic()-started,
                gpu_peak_bytes=torch.cuda.max_memory_allocated() if device=='cuda' else None,
                published_accuracy=baseline[name]['int4_test'],accuracy_delta=accuracy-baseline[name]['int4_test'],
                published_balanced=baseline[name]['int4_test_balanced'])
            report['published_metrics_equal']=all(report[k]==baseline[name][k] for k in ('test_samples','int4_test','int4_test_balanced','recall'))
            for cols in (8,4):assert compile_model(model,cols=cols).image==(run/f'c{cols}/model.bin').read_bytes()
            torch.save(dict(predictions=pred.to(torch.int16),labels=y.to(torch.int16)),out/'predictions.pt')
            report['passed']=True
            print('PASS',name,'correct',report['correct'],'/',len(y),'accuracy',accuracy,
                  'published_equal',report['published_metrics_equal'],flush=True)
        except Exception as exc:
            report['error']=f'{type(exc).__name__}: {exc}'
            raise
        finally:
            report['finished_utc']=datetime.now(timezone.utc).isoformat()
            (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
