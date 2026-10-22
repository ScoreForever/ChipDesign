# noCPU H16 FPGA Validation (2026-10-22)

## Scope

Physical validation of the CPU-less TinyCNN-8-Flat design on the EGo1 board
(Xilinx `xc7a35tcsg324-1`). The FPGA image was built from the noCPU worktree
and programmed through Vivado Hardware Manager.

## Build and board evidence

- Build result: PASS through `write_bitstream`.
- Clock: 100 MHz input, 50 MHz fabric clock; clock-lock output enabled.
- Timing: WNS `+1.590 ns`, TNS `0.000 ns`, WHS `+0.044 ns`.
- Configuration/BRAM DRC: PASS.
- Bitstream SHA-256:
  `8346244761A8CDA194B68434FAD3CE9D80B9AE876CE4A0C81A8AA88B12616FDF`
- Board observation after programming: K3 clock-lock LED on, M1 heartbeat LED
  blinking. K1 TCK activity was not visibly observed.
- Local board wiring uses H16 (J5 pin 21) for TCK. E15 (J5 pin 17) did not
  respond during boundary-scan checks on this board.

## JTAG controller test

OpenOCD found the custom fabric TAP and read IDCODE `0x00000001`. The original
host script sent the complete 72-bit command as one `drscan` field. This OpenOCD
build returned all-zero data for that field. Splitting the packet into 32-bit
data, 32-bit address, and 8-bit opcode fields fixed the scan and response
parsing in `software/host/nocpu_jtag.py`.

Observed controller response to `READ VERSION`:

- Data: `0x00010000` (supported controller ABI).
- Address: `0x00000000` (matches request).
- Response status: `0x80` (valid, error code zero).
- Controller status after read: idle, error zero.

## Physical model load and inference

The deployed package is `software/models/tinycnn8_flatten_strong.npz`.

- Package SHA-256:
  `1732c470ec65f2d2c0ebc96dd93326dabb058277b71eef1bb3717d2ddee2f83a`
- Input: a 320-byte all-zero INT8 feature frame.
- Integer golden logits: `[-1608, -1427, -126, 1047]`.
- FPGA logits: `[-1608, -1427, -126, 1047]` (all four INT32 values match).
- Predicted class: `down` (class 3), matching the golden classifier.
- FPGA cycles: 3028 core cycles, 3029 task cycles.
- Post-inference controller status: model ready, idle, done cleared after ACK,
  no fault, error zero; job 1 completed.

Host invocation (from the WSL environment with OpenOCD running):

```sh
python3 software/host/nocpu_jtag.py status
python3 software/host/nocpu_jtag.py infer \
  --package software/models/tinycnn8_flatten_strong.npz \
  --input build/nocpu_impl_h16_20261022/zero_frame.bin
```

## 100-frame physical real-speech comparison

After the zero-frame smoke test, the production package was left loaded and
100 real utterances were run through the FPGA at 1 MHz JTAG. The samples were
selected deterministically from the Speech Commands v0.02 test archive, 25
per class (`yes`, `no`, `up`, `down`), then processed by the repository's
`export_rtl_vectors.py` and integer golden model.

- Result: **PASS, 100/100 samples and 400/400 raw INT32 logits bit-exact**.
- Board status before and after: model ready, idle, no fault, error zero.
- Job IDs: 2 through 101, contiguous; all completed and ACKed.
- Core/task cycles: 3028/3029 for every sample.
- End-to-end JTAG batch time: 61.04 seconds.
- Balanced-subset classification accuracy: 97.0%; macro-F1: 97.00%.
- Confusion matrix (rows true, columns predicted; order `yes`, `no`, `up`, `down`):

```text
25  0  0  0
 0 24  0  1
 0  1 24  0
 1  0  0 24
```

The exact INT32 comparison is the hardware correctness gate. The 97% accuracy
is only a metric for this balanced 100-frame subset, not a replacement for the
model's full test-set evaluation.

Evidence bundle:
`docs/validation/nocpu_h16_100real_20261022/` contains the selected INT8
inputs, golden logits, labels, sample/WAV hashes, source hashes and the
per-sample FPGA comparison report. The source archive URL and SHA-256 are in
`fpga_golden_100.json`; the model package and bitstream hashes are recorded
above and in that report.

## Remaining validation

This physical run covers one model load and 100 inference/ACK cycles. Next,
run the full test set on the board for end-to-end metrics, then repeat
reset/load/infer cycles and a longer soak run to assess recovery and stability.
