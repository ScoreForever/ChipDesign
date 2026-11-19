"""Package portable Lab 5 delivery evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksums(root, path, required=None):
    lines=[ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError(f'Empty checksum file: {path}')
    seen=set()
    for line in lines:
        parts=line.split(maxsplit=1)
        if len(parts)!=2:
            raise RuntimeError(f'Malformed checksum line in {path}: {line!r}')
        sha,name=parts
        clean=name.lstrip('*')
        if clean in seen:
            raise RuntimeError(f'Duplicate checksum entry: {clean}')
        seen.add(clean)
        target=(root/clean).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file() or digest(target)!=sha:
            raise RuntimeError('Checksum mismatch or unsafe filename')
    if required is not None:
        missing=sorted(set(required)-seen)
        if missing:
            raise RuntimeError(f'Missing required checksum entries in {path}: {missing}')


def package_delivery(pnr_dir, lab5_dir, delivery_dir, zero_pdk_manifest=None):
    pnr,lab,out=map(Path,(pnr_dir,lab5_dir,delivery_dir))
    out.mkdir(parents=True,exist_ok=True)
    stage=out/'staging'
    stage.mkdir(exist_ok=False)
    shutil.copytree(pnr/'lab4_exports',stage/'lab4_exports')
    for name in ('postDummy.gds','my_soc_top.cdl','final_inputs.sha256'):
        preferred = lab/'delivery'/name
        if preferred.is_file():
            src_file = preferred
        else:
            matches=sorted(lab.rglob(name))
            if not matches:
                raise RuntimeError(f'Missing required Lab 5 file: {name}')
            if len(matches)>1:
                raise RuntimeError(f'Expected unique {name}, found {len(matches)}')
            src_file = matches[0]
        shutil.copy2(src_file,stage/name)
        shutil.copy2(src_file,out/name)
    verify_checksums(stage,stage/'final_inputs.sha256',required=('postDummy.gds','my_soc_top.cdl'))
    for label,root in [('pnr',pnr),('lab5',lab)]:
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in ('.rpt','.tsv','.csv'):
                dst=stage/'reports'/label/path.relative_to(root)
                dst.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,dst)
    archive=out/'design_and_reports.tar.gz'
    with tarfile.open(archive,'w:gz') as tf:
        for path in sorted(stage.rglob('*')):
            if path.is_file():tf.add(path,arcname=str(path.relative_to(stage)))
    subprocess.run(['gzip','-t',str(archive)],check=True)
    names=['design_and_reports.tar.gz','final_inputs.sha256','postDummy.gds','my_soc_top.cdl']
    (out/'SHA256SUMS').write_text(''.join(f'{digest(out/n)}  {n}\n' for n in names))
    verify_checksums(out,out/'SHA256SUMS',required=names)
    verify_checksums(out,out/'final_inputs.sha256',required=('postDummy.gds','my_soc_top.cdl'))
    manifest=dict(files=[dict(filename=n,bytes=(out/n).stat().st_size,sha256=digest(out/n)) for n in names],verification=dict(gzip=True,sha256sums=True,final_inputs=True))
    (out/'delivery_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    if zero_pdk_manifest:
        Path(zero_pdk_manifest).write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    for name in ('pnr-dir','lab5-dir','delivery-dir'):ap.add_argument('--'+name,required=True,type=Path)
    ap.add_argument('--zero-pdk-manifest',type=Path)
    a=ap.parse_args();package_delivery(a.pnr_dir,a.lab5_dir,a.delivery_dir,a.zero_pdk_manifest)

if __name__=='__main__':main()
