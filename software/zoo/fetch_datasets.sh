#!/usr/bin/env bash
# Download the public datasets used by the INT4 model zoo into <dir>.
#   software/zoo/fetch_datasets.sh datasets
# Needs curl, unzip and a Python with torchvision, wfdb and huggingface_hub
# (pass it as PYTHON=...). In mainland China, HF_ENDPOINT=https://hf-mirror.com.
set -euo pipefail
DIR="${1:?usage: fetch_datasets.sh <dir>}"
PYTHON="${PYTHON:-python3}"
mkdir -p "$DIR" && cd "$DIR"

# Speech Commands v2 (KWS-L, KWS-M)
mkdir -p speech_commands_v2 && (cd speech_commands_v2 && [ -f done ] || {
  curl -sSL -o sc.tgz https://storage.googleapis.com/download.tensorflow.org/data/speech_commands_v0.02.tar.gz
  tar xzf sc.tgz && rm sc.tgz && touch done; })

# UCI HAR, raw 9-axis signals (HAR-1D)
mkdir -p uci_har && (cd uci_har && [ -f done ] || {
  curl -sSL -o har.zip "https://archive.ics.uci.edu/static/public/240/human+activity+recognition+using+smartphones.zip"
  unzip -qo har.zip && unzip -qo "UCI HAR Dataset.zip" && touch done; })

# MIT-BIH arrhythmia (ECG-1D, PhysioNet), MNIST and Fashion-MNIST (torchvision)
"$PYTHON" - <<'PY'
import os, wfdb, torchvision
os.makedirs('mitdb', exist_ok=True)
if not os.path.exists('mitdb/done'):
    wfdb.dl_database('mitdb', 'mitdb'); open('mitdb/done', 'w').close()
for cls in (torchvision.datasets.MNIST, torchvision.datasets.FashionMNIST):
    for train in (True, False):
        cls('tv', train=train, download=True)
PY

# GTSRB, Hugging Face parquet copy (26,640 train / 12,630 test images)
"$PYTHON" - <<'PY'
import hashlib, pathlib
from huggingface_hub import hf_hub_download
expected = {
    'data/train-00000-of-00001.parquet': '22f75b0cab6937dab1c82de33f8354ae14d4b42d42b08c94879027b26cb97cb6',
    'data/test-00000-of-00001.parquet': '2f868c4e7ae887b3aee00c3c6348ac155287e5fb0497f1c7d7683694299c7e67',
}
for name, sha in expected.items():
    path = pathlib.Path('gtsrb') / name
    if not path.exists():
        hf_hub_download('tanganke/gtsrb', name, repo_type='dataset', local_dir='gtsrb')
    got = hashlib.sha256(path.read_bytes()).hexdigest()
    if got != sha:
        raise SystemExit(f'{path}: sha256 {got} differs from the copy the models were trained on ({sha})')
print('gtsrb ok')
PY
echo "datasets ready in $(pwd)"
