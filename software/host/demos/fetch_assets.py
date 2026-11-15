"""Explicitly download public demo assets. Nothing downloads on GUI startup."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import time
import urllib.request


def digest_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def fetch(url, path, expected=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (expected is None or digest_file(path) == expected):
        return path
    temporary = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {path.name}", flush=True)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=90) as source, temporary.open("wb") as target:
                length = source.headers.get("Content-Length")
                while block := source.read(1024 * 1024):
                    target.write(block)
            if length is not None and temporary.stat().st_size != int(length):
                raise OSError(f"Incomplete download: {path.name}")
            if expected and digest_file(temporary) != expected:
                raise ValueError(f"SHA-256 mismatch: {path.name}")
            temporary.replace(path)
            break
        except (OSError, http.client.HTTPException, ValueError):
            if attempt == 2:
                raise
            time.sleep(1)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def hf_files(repo, directory, revision, prefix="data/"):
    api = f"https://huggingface.co/api/datasets/{repo}"
    entries = json.load(urllib.request.urlopen(f"{api}/tree/{revision}/{prefix}", timeout=30))
    jobs = []
    for entry in entries:
        if entry["type"] == "file":
            url = f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{entry['path']}"
            jobs.append((url, Path(directory) / Path(entry["path"]).name, entry.get("lfs", {}).get("oid")))
    return revision, jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("asset", choices=("signals", "yunet", "fer", "wider", "all"))
    parser.add_argument("--output", type=Path, default=Path("build/demo_data"))
    args = parser.parse_args()
    wanted = {"signals", "yunet", "fer", "wider"} if args.asset == "all" else {args.asset}
    jobs, provenance = [], {}
    if "signals" in wanted:
        jobs.append(("https://archive.ics.uci.edu/static/public/240/human+activity+recognition+using+smartphones.zip",
                     args.output / "uci_har.zip", None))
        for suffix in ("hea", "dat", "atr"):
            jobs.append((f"https://physionet.org/files/mitdb/1.0.0/100.{suffix}", args.output / "mitdb" / f"100.{suffix}", None))
    if "yunet" in wanted:
        jobs.append(("https://huggingface.co/opencv/opencv_zoo/resolve/39a1451/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
                     args.output / "yunet.onnx", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"))
    if "fer" in wanted:
        revision, entries = hf_files("Aaryan333/fer2013_train_publicTest_privateTest", args.output / "fer", "da8d1643649c86bfc2cffba5b744b51fdf329212")
        provenance["fer"] = dict(repository="Aaryan333/fer2013_train_publicTest_privateTest", revision=revision)
        jobs.extend(entries)
    if "wider" in wanted:
        revision, entries = hf_files("CUHK-CSE/wider_face", args.output / "wider", "db171f1b7fedf4d3453e81297ff02f9915356d19")
        provenance["wider"] = dict(repository="CUHK-CSE/wider_face", revision=revision)
        jobs.extend(e for e in entries if "test.zip" not in str(e[1]))
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda job: fetch(*job), jobs))
    for path in paths:
        provenance[str(path.relative_to(args.output))] = dict(sha256=digest_file(path), bytes=path.stat().st_size)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / f"{args.asset}_sources.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(paths)} assets under {args.output}")


if __name__ == "__main__":
    main()
