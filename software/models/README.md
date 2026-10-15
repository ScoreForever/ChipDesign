# TinyCNN-8-Flat trained deployment package

`tinycnn8_flatten_strong.npz` is the unmodified output of
`software/tools/export_model.py`, with labels `0=yes, 1=no, 2=up, 3=down`.
It follows the frozen `20×16×1` input, NHWC Flatten, 160-input FC and
256×8 weight image (241 words used). See the
[training plan](../../docs/MODEL_TRAINING_PLAN.md) and
[integer ABI](../../docs/ARCHITECTURE.md).

## Dataset and provenance

This run used Google Speech Commands v2 (`speech_commands_v0.02`), read directly
from `D:\datasets\speech_commands_v0.02`. Audio is not included in this package.
The official tar download was too slow, so the prepared dataset was reconstructed
into the official directory layout from HuggingFace's S3 v0.02 mirror:

- [Training archive](https://s3.amazonaws.com/datasets.huggingface.co/SpeechCommands/v0.02/v0.02_train.tar.gz)
- [Validation archive](https://s3.amazonaws.com/datasets.huggingface.co/SpeechCommands/v0.02/v0.02_validation.tar.gz)
- [Test archive](https://s3.amazonaws.com/datasets.huggingface.co/SpeechCommands/v0.02/v0.02_test.tar.gz)

Train/validation match the official splits. **Test is the separately published
official v0.02 test set**, rather than the files named by the full corpus's
original `testing_list.txt`, which are absent from these mirror archives.
The local `testing_list.txt` was generated from that separate test set.
The existing loader balances validation/test classes and retains all four-class
training utterances. Seed 0 produced:

| Split | yes | no | up | down | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Train | 3,228 | 3,130 | 2,948 | 3,134 | 12,440 |
| Validation | 350 | 350 | 350 | 350 | 1,400 |
| Test | 405 | 405 | 405 | 405 | 1,620 |

The historical documentation records 12,318/1,596/1,620 samples. Because the
prepared dataset and splits differ, this run's accuracy cannot be compared
directly with the historical 89.94% result.

## Training and quantization

Measured on Windows CPU on 2026-10-14 with Python 3.12.10, NumPy 2.5.4 and
PyTorch 2.14.1+cpu. Training used seed 0, strong augmentation, Adam lr `1e-3`,
weight decay `1e-4`, batch 256, 50 epochs of cosine decay and zero data-loader
workers. The best checkpoint was selected by validation macro-F1: epoch 41,
validation accuracy 90.50%, macro-F1 90.55% (training log precision).
PTQ calibrated on 2,048 unaugmented training examples selected with seed 0.

A separate one-epoch probe took **81.3 seconds** for training plus validation,
giving a conservative 50-epoch estimate of 4,065 seconds (67 min 45 s).
The subsequent full run benefited from warmed filesystem caches: its 50 logged
epoch durations total **230.7 seconds** (mean 4.614 seconds), including training
and validation but excluding initialization, checkpoint saves and final test
evaluation. The probe checkpoint was not used for the full run.

## Test results

Both evaluations use the same balanced 1,620-example test manifest. INT8
classification uses `fc_cmp_mult/shift` to compare raw logits on a common scale.

| Metric | FP32 | INT8 | PTQ drop (FP32 minus INT8, percentage points) |
| --- | ---: | ---: | ---: |
| Accuracy | 89.6914% | 90.3704% | -0.6790 |
| Macro-F1 | 89.7727% | 90.4215% | -0.6488 |

Negative drops indicate improvement. INT8 exceeds the strong-augmentation 80%
accuracy target; neither metric declines beyond the 2-percentage-point limit.

| Class recall | FP32 | INT8 |
| --- | ---: | ---: |
| yes | 95.3086% | 96.0494% |
| no | 86.6667% | 87.4074% |
| up | 91.6049% | 93.0864% |
| down | 85.1852% | 84.9383% |

Confusion matrices use rows=true, columns=predicted; both axes are ordered
`yes, no, up, down`.

```text
FP32                    INT8
386  10   0   9         389   8   1   7
  4 351   1  49           3 354   1  47
  5   8 371  21           4   9 377  15
  9  42   9 345           9  43   9 344
```

## Package integrity

Package size: **12,026 bytes**. SHA-256:

```text
1732c470ec65f2d2c0ebc96dd93326dabb058277b71eef1bb3717d2ddee2f83a
```

The checksum identifies this exported file. Retraining with a different library
version or CPU backend can change floating-point results; regenerating the NPZ
can change archive metadata even if the stored arrays match.

## Reproduce

Run from the repository root with the prepared dataset above. The environment
variable preserves the original scripts' checkpoint loading under current
PyTorch: locally generated checkpoints contain NumPy validation metadata as
well as weights. No source changes are required.

```powershell
$py = 'D:\tools\python312\python.exe'
$dataset = 'D:\datasets\speech_commands_v0.02'
$env:TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD = '1'
$env:Path = 'D:\iverilog\bin;' + $env:Path
New-Item -ItemType Directory -Force build, software/models | Out-Null
# Keep the RTL runner's temporary compilation output inside build/.
$env:TEMP = (Resolve-Path build).Path
$env:TMP = $env:TEMP

# Independent timing probe; do not reuse it as the full training run.
& $py software/train/train.py --data-dir $dataset --epochs 1 `
  --batch-size 256 --num-workers 0 --seed 0 --task four_class `
  --head flatten --augmentation strong --lr 1e-3 --weight-decay 1e-4 `
  --device cpu --output build/epoch_probe

& $py software/train/train.py --data-dir $dataset --epochs 50 `
  --batch-size 256 --num-workers 0 --seed 0 --task four_class `
  --head flatten --augmentation strong --lr 1e-3 --weight-decay 1e-4 `
  --device cpu --output software/train/runs/tinycnn8_4class_flatten_strong

& $py software/tools/export_model.py `
  --checkpoint software/train/runs/tinycnn8_4class_flatten_strong/best.pt `
  --data-dir $dataset --calib-size 2048 --device cpu `
  --output software/models/tinycnn8_flatten_strong.npz
& $py software/tools/evaluate_quantized.py `
  --package software/models/tinycnn8_flatten_strong.npz --data-dir $dataset
& $py software/tools/export_rtl_vectors.py `
  --package software/models/tinycnn8_flatten_strong.npz --data-dir $dataset `
  --output-dir data/rtl_vectors_flatten --count 100
powershell -NoProfile -ExecutionPolicy Bypass `
  -File hardware/npu/scripts/run_tinycnn8_trained_test.ps1 `
  -VectorDir data/rtl_vectors_flatten

& $py -m pytest -q software/tests
# These files implement main() scripts rather than pytest test functions.
# pytest 9.1.1 collects no tests (exit 5); execute both checks directly:
& $py software/tests/test_dataset_contract.py --data-dir $dataset
& $py software/tests/test_golden_vs_rtl_tb.py
Get-FileHash software/models/tinycnn8_flatten_strong.npz -Algorithm SHA256
```

The trained-model RTL regression passed all 100 real-speech samples: all four
raw INT32 logits per sample matched bit for bit (400 comparisons). Its final
output was ALL TRAINED FLATTEN RTL VECTORS PASSED samples=100.

Both directly executed software contract scripts passed. Run logs and
checkpoints are local intermediate artifacts under `build/` and
`software/train/runs/`; generated RTL vectors are under `data/`.
