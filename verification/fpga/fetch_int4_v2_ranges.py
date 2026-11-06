"""Public-source dataset fetch with validated parallel HTTP ranges and hashes."""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
from pathlib import Path
import re
import shutil
import tarfile
import time
import urllib.request
import zipfile


def opener():return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(url,start,end):
    return opener().open(urllib.request.Request(url,headers={
        'Range':f'bytes={start}-{end}','Accept-Encoding':'identity',
        'User-Agent':'ChipDesign-public-dataset-validation/1.0'}),timeout=45)


def ranged(url,target,workers=12):
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():return target
    with request(url,0,0) as response:
        match=re.fullmatch(r'bytes 0-0/(\d+)',response.headers.get('Content-Range',''))
        if response.status!=206 or not match:raise RuntimeError('source does not support exact ranges: '+url)
        total=int(match[1])
    chunk=1024*1024
    folder=target.with_name(target.name+'.chunks');folder.mkdir(exist_ok=True)
    count=(total+chunk-1)//chunk
    print('RANGES',target.name,total,'bytes',count,'chunks',workers,'workers',flush=True)
    def one(index):
        start=index*chunk;end=min(total,start+chunk)-1
        final=folder/f'{index:05d}.bin';partial=folder/f'{index:05d}.part'
        if final.exists() and final.stat().st_size==end-start+1:return
        for attempt in range(12):
            try:
                offset=partial.stat().st_size if partial.exists() else 0
                if offset==end-start+1:partial.replace(final);return
                with request(url,start+offset,end) as response:
                    expected=f'bytes {start+offset}-{end}/{total}'
                    if response.status!=206 or response.headers.get('Content-Range')!=expected:
                        raise RuntimeError('mismatched Content-Range: '+str(response.headers.get('Content-Range')))
                    with partial.open('ab') as f:
                        while True:
                            block=response.read(65536)
                            if not block:break
                            f.write(block)
                if partial.stat().st_size!=end-start+1:raise IOError('truncated chunk')
                partial.replace(final);return
            except Exception as exc:
                if attempt==11:raise
                print('RETRY',target.name,index,attempt+1,type(exc).__name__,str(exc)[:100],flush=True)
                time.sleep(min(2**attempt,15))
    done=0;last=time.monotonic()
    with ThreadPoolExecutor(workers) as pool:
        for future in as_completed([pool.submit(one,i) for i in range(count)]):
            future.result();done+=1
            if time.monotonic()-last>15 or done==count:
                print('PROGRESS',target.name,done,'/',count,flush=True);last=time.monotonic()
    assembled=target.with_name(target.name+'.assembled')
    with assembled.open('wb') as f:
        for i in range(count):
            with (folder/f'{i:05d}.bin').open('rb') as src:shutil.copyfileobj(src,f)
    if assembled.stat().st_size!=total:raise IOError('assembled length mismatch')
    assembled.replace(target)
    # Keep chunks for resumability/audit until the complete evaluation is done.
    return target


def unzip(path,target):
    target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')
        for m in z.infolist():
            if not (target/m.filename).resolve().is_relative_to(target.resolve()):raise ValueError('unsafe ZIP member')
        z.extractall(target)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--data',type=Path,required=True)
    args=p.parse_args();root=args.data.resolve();archives=root/'archives'
    urls={};report={}
    def speech():
        url='https://storage.googleapis.com/download.tensorflow.org/data/speech_commands_v0.02.tar.gz'
        path=ranged(url,archives/'speech_commands_v0.02.tar.gz',64);urls['speech']=url
        target=root/'speech_commands_v2';target.mkdir(exist_ok=True)
        print('EXTRACT speech',flush=True)
        with tarfile.open(path,'r:gz') as tar:
            for m in tar:
                if m.issym() or m.islnk() or not (target/m.name).resolve().is_relative_to(target):raise ValueError('unsafe TAR member')
                tar.extract(m,target)
        (target/'done').touch()
    def har():
        url='https://d396qusza40orc.cloudfront.net/getdata%2Fprojectfiles%2FUCI%20HAR%20Dataset.zip'
        path=ranged(url,archives/'har_coursera.zip');urls['har']=url
        target=root/'uci_har';unzip(path,target)
        assert (target/'UCI HAR Dataset/test/y_test.txt').exists()
        (target/'done').touch()
    def ecg():
        url='https://physionet.org/static/published-projects/mitdb/mit-bih-arrhythmia-database-1.0.0.zip'
        path=ranged(url,archives/'mitdb-1.0.0.zip');urls['ecg']=url
        target=root/'mitdb_archive';unzip(path,target)
        header,=target.rglob('100.hea');dest=root/'mitdb';dest.mkdir(exist_ok=True)
        for file in header.parent.iterdir():
            if file.is_file():shutil.copyfile(file,dest/file.name)
        assert len(list(dest.glob('*.hea')))==48
        (dest/'done').touch()
    def images():
        import gzip
        import torchvision
        cls=torchvision.datasets.FashionMNIST;dest=root/'tv/FashionMNIST/raw';dest.mkdir(parents=True,exist_ok=True)
        for name,md5 in cls.resources:
            path=ranged('https://fashion-mnist.s3.eu-central-1.amazonaws.com/'+name,archives/name,8)
            assert hashlib.md5(path.read_bytes()).hexdigest()==md5,name
            shutil.copyfile(path,dest/name)
            with gzip.open(path,'rb') as src,(dest/name.removesuffix('.gz')).open('wb') as f:shutil.copyfileobj(src,f)
        urls['images']='https://fashion-mnist.s3.eu-central-1.amazonaws.com/'
    def gtsrb():
        hashes={'train-00000-of-00001.parquet':'22f75b0cab6937dab1c82de33f8354ae14d4b42d42b08c94879027b26cb97cb6',
                'test-00000-of-00001.parquet':'2f868c4e7ae887b3aee00c3c6348ac155287e5fb0497f1c7d7683694299c7e67'}
        for name,sha in hashes.items():
            # Use a new destination so an interrupted earlier fetch cannot count as complete.
            path=ranged('https://hf-mirror.com/datasets/tanganke/gtsrb/resolve/main/data/'+name,root/'gtsrb_ranged/data'/name,16)
            assert hashlib.sha256(path.read_bytes()).hexdigest()==sha,name
        urls['gtsrb']='https://hf-mirror.com/datasets/tanganke/gtsrb/resolve/main/data/'
        dest=root/'gtsrb/data';dest.mkdir(parents=True,exist_ok=True)
        for name in hashes:shutil.copyfile(root/'gtsrb_ranged/data'/name,dest/name)
    tasks=dict(speech=speech,har=har,ecg=ecg,images=images,gtsrb=gtsrb)
    with ThreadPoolExecutor(5) as pool:
        pending={pool.submit(fn):name for name,fn in tasks.items()}
        for future in as_completed(pending):
            name=pending[future]
            try:future.result();report[name]='READY';print('READY',name,flush=True)
            except Exception as exc:report[name]=f'{type(exc).__name__}: {exc}';print('FAILED',name,report[name],flush=True)
            (root/'range_download_status.json').write_text(json.dumps(dict(status=report,urls=urls),indent=2)+'\n')
    if any(v!='READY' for v in report.values()):raise RuntimeError('dataset downloads incomplete')


if __name__=='__main__':main()
