"""Google Speech Commands v2 loader for the TinyCNN-8-Flat task.

Baseline class order is part of the deployment ABI:
  0=yes, 1=no, 2=up, 3=down

Official validation/testing lists keep speakers separated. Validation and test
are class-balanced. The legacy six-class experiment remains available through
`task='six_class'`; its background recordings are split by file.
"""

import hashlib
import os
import random
import sys

import numpy as np
import torch
from torch.utils.data import Dataset

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import preprocess  # noqa: E402

KEYWORDS = ['yes', 'no', 'up', 'down']
LABEL_TO_INDEX = {
    'yes': 0, 'no': 1, 'up': 2, 'down': 3, 'silence': 4, 'unknown': 5,
}
INDEX_TO_LABEL = {v: k for k, v in LABEL_TO_INDEX.items()}
TASK_LABELS = {
    'six_class': ['yes', 'no', 'up', 'down', 'silence', 'unknown'],
    'four_class': ['yes', 'no', 'up', 'down'],
}
DEFAULT_TASK = 'four_class'
NUM_CLASSES = len(TASK_LABELS[DEFAULT_TASK])


def get_task_labels(task):
    if task not in TASK_LABELS:
        raise ValueError(f'unsupported task: {task}')
    return list(TASK_LABELS[task])

# GSCD v2 only contains six long background recordings. File-level splitting
# is explicit and stable to prevent train/evaluation leakage.
BACKGROUND_SPLIT = {
    'train': [
        'doing_the_dishes.wav', 'dude_miaowing.wav',
        'exercise_bike.wav', 'running_tap.wav',
    ],
    'validation': ['pink_noise.wav'],
    'test': ['white_noise.wav'],
}


def _stable_seed(seed, *parts):
    payload = ':'.join([str(seed), *map(str, parts)]).encode('utf-8')
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], 'little')


def _list_words(data_dir):
    return sorted(
        d for d in os.listdir(data_dir)
        if os.path.isdir(os.path.join(data_dir, d)) and not d.startswith('_')
    )


def _read_list(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f'missing official GSCD split list: {path}')
    with open(path, 'r', encoding='utf-8') as handle:
        return {line.strip().replace('\\', '/') for line in handle if line.strip()}


def _belongs_to_mode(rel, mode, val_set, test_set):
    if mode == 'train':
        return rel not in val_set and rel not in test_set
    if mode == 'validation':
        return rel in val_set
    return rel in test_set


def _word_paths(data_dir, word, mode, val_set, test_set):
    word_dir = os.path.join(data_dir, word)
    if not os.path.isdir(word_dir):
        raise FileNotFoundError(f'missing GSCD word directory: {word_dir}')
    out = []
    for filename in sorted(os.listdir(word_dir)):
        if filename.lower().endswith('.wav'):
            rel = f'{word}/{filename}'
            if _belongs_to_mode(rel, mode, val_set, test_set):
                out.append(os.path.join(word_dir, filename))
    return out


def _background_files(noise_dir, mode):
    files = [os.path.join(noise_dir, name) for name in BACKGROUND_SPLIT[mode]]
    missing = [path for path in files if not os.path.isfile(path)]
    if missing:
        raise FileNotFoundError(f'missing background recordings: {missing}')
    return files


def _build_silence(noise_dir, count, seed, mode):
    """Create deterministic one-second background-noise slices."""
    rng = random.Random(_stable_seed(seed, mode, 'silence'))
    noise_files = _background_files(noise_dir, mode)
    lengths = {path: len(preprocess.read_wav(path)) for path in noise_files}
    out = []
    for index in range(count):
        path = noise_files[index % len(noise_files)]
        length = lengths[path]
        if length < preprocess.CLIP_SAMPLES:
            raise ValueError(f'background recording shorter than one second: {path}')
        offset = rng.randint(0, length - preprocess.CLIP_SAMPLES)
        out.append((path, offset, LABEL_TO_INDEX['silence']))
    return out


def build_manifest(data_dir, mode, seed=0, task=DEFAULT_TASK):
    """Build a deterministic manifest for train/validation/test.

    Training retains all target-keyword utterances and balances unknown/silence
    to their mean size. Validation/test downsample every class to the smallest
    target-keyword count, making overall accuracy and macro metrics comparable.
    """
    if mode not in ('train', 'validation', 'test'):
        raise ValueError(f'unsupported mode: {mode}')
    labels = get_task_labels(task)
    rng = random.Random(_stable_seed(seed, mode, 'manifest'))
    val_set = _read_list(os.path.join(data_dir, 'validation_list.txt'))
    test_set = _read_list(os.path.join(data_dir, 'testing_list.txt'))

    keyword_paths = {
        word: _word_paths(data_dir, word, mode, val_set, test_set)
        for word in KEYWORDS
    }
    counts = [len(keyword_paths[word]) for word in KEYWORDS]
    target_count = (round(sum(counts) / len(counts))
                    if mode == 'train' else min(counts))

    manifest = []
    for word in KEYWORDS:
        paths = keyword_paths[word]
        if mode != 'train':
            paths = rng.sample(paths, target_count)
        manifest.extend((path, LABEL_TO_INDEX[word]) for path in paths)

    if task == 'four_class':
        rng.shuffle(manifest)
        return manifest

    other_words = [word for word in _list_words(data_dir) if word not in KEYWORDS]
    unknown_pool = []
    for word in other_words:
        unknown_pool.extend(_word_paths(data_dir, word, mode, val_set, test_set))
    if len(unknown_pool) < target_count:
        raise ValueError(f'unknown pool too small: {len(unknown_pool)} < {target_count}')
    manifest.extend(
        (path, LABEL_TO_INDEX['unknown'])
        for path in rng.sample(unknown_pool, target_count)
    )

    noise_dir = os.path.join(data_dir, '_background_noise_')
    manifest.extend(_build_silence(noise_dir, target_count, seed, mode))
    rng.shuffle(manifest)
    return manifest


class SpeechCommandsDataset(Dataset):
    """Return `(1,20,16)` float features and the frozen integer label."""

    def __init__(self, data_dir, mode, seed=0, augment=None, cache_wav=True,
                 task=DEFAULT_TASK, augmentation=None):
        self.data_dir = data_dir
        self.mode = mode
        self.seed = seed
        self.task = task
        self.labels = get_task_labels(task)
        self.num_classes = len(self.labels)
        self.epoch = 0
        if augmentation is None:
            enabled = (mode == 'train') if augment is None else bool(augment)
            augmentation = 'strong' if enabled else 'none'
        if augmentation not in ('none', 'weak', 'strong'):
            raise ValueError(f'unsupported augmentation profile: {augmentation}')
        if mode != 'train' and augmentation != 'none':
            raise ValueError('augmentation is only allowed for the training split')
        self.augmentation = augmentation
        self.augment = augmentation != 'none'
        self.noise_cache = {} if cache_wav else None
        self.augment_rng = random.Random(_stable_seed(seed, mode, 'augment'))
        self.manifest = build_manifest(data_dir, mode, seed, task)
        noise_dir = os.path.join(data_dir, '_background_noise_')
        self.augmentation_noise_files = (
            _background_files(noise_dir, 'train') if self.augment else []
        )

    def set_epoch(self, epoch):
        """Resample train unknown/silence and reset augmentation deterministically."""
        self.epoch = int(epoch)
        if self.mode == 'train':
            epoch_seed = _stable_seed(self.seed, 'epoch', self.epoch)
            self.manifest = build_manifest(
                self.data_dir, self.mode, epoch_seed, self.task
            )
            self.augment_rng = random.Random(
                _stable_seed(self.seed, self.mode, 'augment', self.epoch)
            )

    def __len__(self):
        return len(self.manifest)

    def __getitem__(self, idx):
        item = self.manifest[idx]
        if len(item) == 3:
            path, offset, label = item
            audio = self._read_wav(path)[offset:offset + preprocess.CLIP_SAMPLES].copy()
        else:
            path, label = item
            audio = self._fit_clip(self._read_wav(path))
            if self.augment:
                audio = self._augment_audio(audio)
        feature = preprocess.extract_feature(audio)
        return torch.from_numpy(feature).unsqueeze(0), label

    def class_counts(self):
        counts = [0] * self.num_classes
        for item in self.manifest:
            counts[item[-1]] += 1
        return counts

    def _read_wav(self, path):
        if self.noise_cache is None:
            return preprocess.read_wav(path)
        if path not in self.noise_cache:
            self.noise_cache[path] = preprocess.read_wav(path)
        return self.noise_cache[path]

    @staticmethod
    def _fit_clip(audio):
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < preprocess.CLIP_SAMPLES:
            return np.pad(audio, (0, preprocess.CLIP_SAMPLES - len(audio)))
        return audio[:preprocess.CLIP_SAMPLES].copy()

    def _augment_audio(self, audio):
        rng = self.augment_rng
        if self.augmentation == 'strong':
            max_shift, gain_range = 1600, (0.8, 1.2)
            noise_probability, snr_range = 0.8, (10.0, 30.0)
        else:
            max_shift, gain_range = 800, (0.9, 1.1)
            noise_probability, snr_range = 0.4, (15.0, 30.0)
        shift = rng.randint(-max_shift, max_shift)  # zero-filled, no wrap
        shifted = np.zeros_like(audio)
        if shift > 0:
            shifted[shift:] = audio[:-shift]
        elif shift < 0:
            shifted[:shift] = audio[-shift:]
        else:
            shifted[:] = audio
        shifted *= rng.uniform(*gain_range)

        if self.augmentation_noise_files and rng.random() < noise_probability:
            noise_path = rng.choice(self.augmentation_noise_files)
            noise_all = self._read_wav(noise_path)
            offset = rng.randint(0, len(noise_all) - preprocess.CLIP_SAMPLES)
            noise = noise_all[offset:offset + preprocess.CLIP_SAMPLES]
            signal_rms = float(np.sqrt(np.mean(shifted * shifted) + 1e-12))
            noise_rms = float(np.sqrt(np.mean(noise * noise) + 1e-12))
            snr_db = rng.uniform(*snr_range)
            scale = signal_rms / (noise_rms * 10.0 ** (snr_db / 20.0))
            shifted = shifted + noise * scale
        return np.clip(shifted, -1.0, 1.0).astype(np.float32)


def collate(batch):
    features = torch.stack([item[0] for item in batch])
    labels = torch.tensor([item[1] for item in batch], dtype=torch.long)
    return features, labels


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--mode', default='train')
    parser.add_argument('--task', choices=tuple(TASK_LABELS), default=DEFAULT_TASK)
    args = parser.parse_args()
    dataset = SpeechCommandsDataset(
        args.data_dir, args.mode, augment=False, task=args.task
    )
    print(f'{args.mode}: {len(dataset)} samples class_counts={dataset.class_counts()}')
    if len(dataset):
        feature, label = dataset[0]
        print('feature shape:', tuple(feature.shape),
              'label:', label, dataset.labels[int(label)])
