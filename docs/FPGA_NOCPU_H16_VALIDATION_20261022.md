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

## Limits and next validation

This is a physical smoke test: it proves the controller command path, model
loading, and one complete inference against the integer golden model. A zero
frame does not establish speech-task accuracy, data-dependent corner-case
coverage, or long-run stability. The 100-sample bit-exact result described in
the model documentation is RTL simulation evidence, not an FPGA measurement.

Next, run a diverse set of real preprocessed test frames on the board and
compare all four raw logits per frame against `software/golden_model.py`. Then
run the full test set for classification metrics and repeat load/infer/ACK
cycles to assess stability. Preserve the test vectors, metric summary, and
all artifact hashes with that run.
