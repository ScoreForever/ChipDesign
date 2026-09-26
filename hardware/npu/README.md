# Weight-stationary Matrix Unit

`matrix_unit` computes one output vector per accepted transaction:

`P_out[c] = P_in[c] + sum(r=0..ARRAY_ROWS-1) A[r] * W[r][c]`.

The default configuration is 4 rows by 8 columns with signed 8-bit
activations and weights and signed 32-bit partial sums. `ws_pe.sv` holds one
weight and performs a registered multiply/add. `ws_systolic_array.sv` connects
the PEs with activations moving right and partial sums moving down.
`matrix_unit.sv` controls weight loading, skew, deskew, and streaming.

## Interface

| Port | Direction | Meaning |
| --- | --- | --- |
| `clk`, `rst` | in | Positive-edge clock and synchronous active-high reset |
| `weight_start_valid`, `weight_start_ready` | in, out | Handshake to begin a new tile; ready only while idle |
| `weight_valid`, `weight_ready`, `weight_data` | in, out, in | Supply one packed weight row per handshake |
| `weights_loaded`, `idle` | out | Tile available for compute; no load or compute in flight |
| `in_valid`, `in_ready` | in, out | Compute input handshake |
| `in_act_data`, `in_psum_data` | in | Packed activation and input partial-sum vectors |
| `out_valid`, `out_ready`, `out_psum_data` | out, in, out | Packed output partial-sum handshake |

Lane 0 is in the least significant bits: `A[r]` is
`in_act_data[r*ACT_WIDTH +: ACT_WIDTH]`; `P_in[c]` and `P_out[c]` use
`c*ACC_WIDTH +: ACC_WIDTH`; weight row element `W[r][c]` uses
`weight_data[c*WGT_WIDTH +: WGT_WIDTH]` while row `r` is loaded.

Pulse or hold `weight_start_valid` until it handshakes with
`weight_start_ready`. Then send exactly `ARRAY_ROWS` weight rows, ordered
from row 0 upward, on `weight_valid && weight_ready`. `weights_loaded` goes
high after the final row. A new tile starts only after `idle` is high; no
compute/load overlap or double buffering is provided.

Input row `r` is delayed `r` enabled cycles, and input partial-sum column
`c` is delayed `c` enabled cycles. The registered array then brings the
matching values together at PE `(r,c)`. Output column `c` is delayed
`ARRAY_COLS-1-c` enabled cycles to align the output vector. From an input
acceptance edge to the edge that creates its output, the pipeline takes
`ARRAY_ROWS+ARRAY_COLS-2` enabled cycles. An output handshake occurs at a
subsequent edge. The array can accept one input each cycle after filling.
When `out_valid && !out_ready`, a common clock enable freezes every data and
valid register, and `in_ready` is low. Input bubbles advance through the
pipeline but do not assert output valid.

For a 4x8 example, the controller can load 4 rows of 8 weights. If
`A=[1,2,3,4]`, `P_in[0]=10`, and column 0 of the weights is `[5,6,7,8]`,
then `P_out[0]=10+1*5+2*6+3*7+4*8=80`. The other seven columns are calculated
in the same transaction. Bias can be supplied in `P_in`; K/N tails are
zero-padded by the controller.

## Test

Run from the repository root on Windows with Icarus Verilog on `PATH`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File hardware/npu/scripts/run_matrix_unit_test.ps1
```

The script compiles with `iverilog -g2012 -Wall`, runs with `vvp`, and returns
nonzero on any failure. It runs the PE test and 4x4, 4x8, and 8x8 matrix
regressions. Define `DUMP_VCD` when compiling the matrix testbench manually
to emit `matrix_unit.vcd` for debugging.
