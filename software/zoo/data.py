"""Dataset preparation for the INT4 model zoo.

Every loader returns float tensors already normalized for the network,
shaped [N, C, H, W], plus integer labels, for train / validation / test.
The test split is never used for model selection. Prepared tensors are
cached as .pt files under the data root.
"""
from __future__ import annotations
from pathlib import Path
import random
import numpy as np
import torch


def cached(root, name, build):
    path = Path(root) / f'{name}.pt'
    if path.exists():
        return torch.load(path)
    result = build()
    torch.save(result, path)
    return result


def split_validation(x, y, fraction=0.1, seed=0):
    g = torch.Generator().manual_seed(seed)
    order = torch.randperm(len(y), generator=g)
    n = int(len(y) * fraction)
    return (x[order[n:]], y[order[n:]]), (x[order[:n]], y[order[:n]])


def torchvision_images(root, cls):
    import torchvision
    tv = Path(root) / 'tv'
    train = getattr(torchvision.datasets, cls)(tv, train=True, download=False)
    test = getattr(torchvision.datasets, cls)(tv, train=False, download=False)
    norm = lambda d: ((d.data.float() / 255 - 0.5) / 0.5).unsqueeze(1)
    (xt, yt), (xv, yv) = split_validation(norm(train), train.targets.long())
    return dict(train=(xt, yt), val=(xv, yv), test=(norm(test), test.targets.long()),
                classes=list(train.classes), preprocess='(pixel/255-0.5)/0.5')


def mnist(root):
    return cached(root, 'mnist', lambda: torchvision_images(root, 'MNIST'))


def fashion(root):
    return cached(root, 'fashion', lambda: torchvision_images(root, 'FashionMNIST'))


def uci_har(root):
    """Raw 9-axis inertial windows (128 samples at 50 Hz), subject-disjoint
    official split; layout [N, 9, 1, 128] (H=1, W=time, C=axis)."""
    def build():
        base = Path(root) / 'uci_har' / 'UCI HAR Dataset'
        names = ['body_acc_x', 'body_acc_y', 'body_acc_z', 'body_gyro_x', 'body_gyro_y', 'body_gyro_z',
                 'total_acc_x', 'total_acc_y', 'total_acc_z']

        def load(split):
            x = np.stack([np.loadtxt(base / split / 'Inertial Signals' / f'{n}_{split}.txt') for n in names], 1)
            y = np.loadtxt(base / split / f'y_{split}.txt').astype(np.int64) - 1
            subjects = np.loadtxt(base / split / f'subject_{split}.txt').astype(np.int64)
            return torch.tensor(x, dtype=torch.float32).unsqueeze(2), torch.tensor(y), subjects
        xtr, ytr, subj = load('train')
        xte, yte, _ = load('test')
        mean = xtr.mean((0, 2, 3), keepdim=True)
        std = xtr.std((0, 2, 3), keepdim=True)
        xtr, xte = (xtr - mean) / std, (xte - mean) / std
        # Validation holds out whole training subjects.
        held = np.isin(subj, sorted(set(subj))[-3:])
        val = torch.tensor(held)
        return dict(train=(xtr[~val], ytr[~val]), val=(xtr[val], ytr[val]), test=(xte, yte),
                    classes=['walking', 'upstairs', 'downstairs', 'sitting', 'standing', 'laying'],
                    preprocess='per-axis z-score with training mean/std', mean=mean.flatten().tolist(),
                    std=std.flatten().tolist())
    return cached(root, 'uci_har', build)


AAMI = {'N': 0, 'L': 0, 'R': 0, 'e': 0, 'j': 0, 'A': 1, 'a': 1, 'J': 1, 'S': 1,
        'V': 2, 'E': 2, 'F': 3, '/': 4, 'f': 4, 'Q': 4}


def mitbih(root):
    """MIT-BIH arrhythmia beats (MLII), AAMI classes N/S/V/F/Q. Window of 256
    samples (96 before the R peak) decimated to 128, z-scored per beat.
    Intra-patient stratified split 70/10/20 by beat (the common benchmark
    protocol; not the harder inter-patient DS1/DS2 protocol)."""
    def build():
        import wfdb
        base = Path(root) / 'mitdb'
        records = sorted(p.stem for p in base.glob('*.hea'))
        xs, ys = [], []
        for rec in records:
            sig = wfdb.rdrecord(str(base / rec)).p_signal
            lead = sig[:, 0]
            ann = wfdb.rdann(str(base / rec), 'atr')
            for s, sym in zip(ann.sample, ann.symbol):
                if sym not in AAMI or s < 96 or s + 160 > len(lead):
                    continue
                beat = lead[s - 96:s + 160].reshape(128, 2).mean(1)
                beat = (beat - beat.mean()) / (beat.std() + 1e-6)
                xs.append(beat)
                ys.append(AAMI[sym])
        x = torch.tensor(np.array(xs), dtype=torch.float32).view(-1, 1, 1, 128)
        y = torch.tensor(ys)
        g = torch.Generator().manual_seed(0)
        parts = {k: [] for k in ('train', 'val', 'test')}
        for c in range(5):
            idx = torch.nonzero(y == c).flatten()
            idx = idx[torch.randperm(len(idx), generator=g)]
            a, b = int(0.7 * len(idx)), int(0.8 * len(idx))
            parts['train'].append(idx[:a])
            parts['val'].append(idx[a:b])
            parts['test'].append(idx[b:])
        out = {k: (x[torch.cat(v)], y[torch.cat(v)]) for k, v in parts.items()}
        out.update(classes=['N', 'S', 'V', 'F', 'Q'], preprocess='per-beat z-score, 2x decimation')
        return out
    return cached(root, 'mitbih', build)


KEYWORDS = ['yes', 'no', 'up', 'down', 'left', 'right', 'on', 'off', 'stop', 'go']


def speech_commands(root, n_mfcc=10):
    """Google Speech Commands v2, the 12-class task: ten keywords, unknown
    (other words) and silence (background noise). 49x10 MFCC (40 ms window,
    20 ms hop), the DS-CNN front end. Official validation/testing lists."""
    def build():
        import torchaudio
        base = Path(root) / 'speech_commands_v2'
        val = set((base / 'validation_list.txt').read_text().split())
        test = set((base / 'testing_list.txt').read_text().split())
        words = sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith('_'))
        rng = random.Random(0)
        files = {'train': [], 'val': [], 'test': []}
        for wdir in words:
            label = KEYWORDS.index(wdir) if wdir in KEYWORDS else 10
            for f in sorted((base / wdir).glob('*.wav')):
                rel = f'{wdir}/{f.name}'
                split = 'val' if rel in val else 'test' if rel in test else 'train'
                files[split].append((f, label))
        # Unknown words are subsampled to one keyword's share; silence comes
        # from 1 s background-noise crops at random gains.
        import soundfile

        def load(path):
            return torch.tensor(soundfile.read(str(path), dtype='float32')[0])
        noise = [load(p) for p in sorted((base / '_background_noise_').glob('*.wav'))]
        mfcc = torchaudio.transforms.MFCC(16000, n_mfcc, melkwargs=dict(n_fft=640, win_length=640, hop_length=320,
                                                                          n_mels=40, center=False))
        out = {}
        for split, items in files.items():
            known = [it for it in items if it[1] < 10]
            unknown = [it for it in items if it[1] == 10]
            rng.shuffle(unknown)
            share = len(known) // 10
            chosen = known + unknown[:share]
            waves, labels = [], []
            for f, label in chosen:
                w = load(f)
                w = torch.nn.functional.pad(w, (0, max(0, 16000 - len(w))))[:16000]
                waves.append(w)
                labels.append(label)
            for _ in range(share):
                n = rng.choice(noise)
                at = rng.randrange(0, len(n) - 16000)
                waves.append(n[at:at + 16000] * rng.uniform(0, 1))
                labels.append(11)
            feats = []
            for i in range(0, len(waves), 4096):
                feats.append(mfcc(torch.stack(waves[i:i + 4096])))
            x = torch.cat(feats).transpose(1, 2).unsqueeze(1)  # [N,1,49,10]
            out[split] = (x, torch.tensor(labels))
        mean, std = out['train'][0].mean(), out['train'][0].std()
        for k in ('train', 'val', 'test'):
            out[k] = ((out[k][0] - mean) / std, out[k][1])
        out.update(classes=KEYWORDS + ['unknown', 'silence'], preprocess='49x10 MFCC, global z-score',
                   mean=float(mean), std=float(std))
        return out
    return cached(root, f'speech_commands_{n_mfcc}', build)


def gtsrb(root):
    """German Traffic Sign Recognition Benchmark, 43 classes, RGB resized
    to 32x32, official test set. Read from the Hugging Face parquet copy."""
    def build():
        import io
        import pandas as pd
        from PIL import Image
        base = Path(root) / 'gtsrb'

        def load(pattern):
            frames = [pd.read_parquet(p) for p in sorted(base.glob(pattern))]
            df = pd.concat(frames)
            imgs = [np.asarray(Image.open(io.BytesIO(b['bytes'])).convert('RGB').resize((32, 32), Image.BILINEAR))
                    for b in df['image']]
            x = torch.tensor(np.stack(imgs), dtype=torch.float32).permute(0, 3, 1, 2) / 255
            # Per-image contrast normalization (lighting varies widely in GTSRB),
            # scaled so roughly +-2 sigma spans the INT4 input range.
            x = (x - x.mean((1, 2, 3), keepdim=True)) / (x.std((1, 2, 3), keepdim=True) + 1e-3) / 2
            return x, torch.tensor(df['label'].values, dtype=torch.long)
        xtr, ytr = load('**/train-*.parquet')
        xte, yte = load('**/test-*.parquet')
        (xt, yt), (xv, yv) = split_validation(xtr, ytr)
        return dict(train=(xt, yt), val=(xv, yv), test=(xte, yte), classes=list(range(43)),
                    preprocess='RGB 32x32 bilinear, per-image z-score / 2')
    return cached(root, 'gtsrb', build)


LOADERS = dict(mnist=mnist, fashion=fashion, har=uci_har, ecg=mitbih, kws=speech_commands, gtsrb=gtsrb,
               fashion_l=fashion, kws_l=speech_commands, mnist_l=mnist, kws_m=speech_commands, gtsrb_l=gtsrb, fashion_xl=fashion, fashion_teacher=fashion, fashion_cnn=fashion, fashion_cnn2=fashion)
