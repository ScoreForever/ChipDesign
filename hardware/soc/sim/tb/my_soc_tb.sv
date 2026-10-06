`timescale 1ns/1ps

// my_soc_tb — drives the SoC, polls a magic region near the top of SRAM
// and reports PASS / FAIL / TIMEOUT based on what the program writes there.
//
// Magic region layout (the C program must agree with this; see lab3_test1.c):
//   0x80001FE0 : magic_status (0xC0DEC0DE = PASS, 0xDEADBEEF = FAIL,
//                              0x12345678 = RUNNING)
//   0x80001FE4 : fail_i        (row index of first mismatch, valid on FAIL)
//   0x80001FE8 : fail_j        (col index of first mismatch, valid on FAIL)
//   0x80001FEC : hw value      (NPU output at (fail_i, fail_j))
//   0x80001FF0 : ref value     (software reference at (fail_i, fail_j))
//   0x80001FF4 : case_id       (optional, set by multi-case tests; single-case tests may leave 0)
// SRAM is 32-bit / word; the magic region starts at SRAM word index 0x7F8 (= 0x1FE0/4).

module my_soc_tb;

  localparam int unsigned MAGIC_WORD     = 32'h0000_07F8;
  localparam logic [31:0] STATUS_RUNNING = 32'h1234_5678;
  localparam logic [31:0] STATUS_PASS    = 32'hC0DE_C0DE;
  localparam logic [31:0] STATUS_FAIL    = 32'hDEAD_BEEF;

  // A single 4×4 GEMM end-to-end (CPU boot + 32 MMIO writes + 4-cycle compute
  // + status poll + 16 MMIO reads + software ref compare) lands around 7-10K
  // cycles in -novopt mode. 1M leaves plenty of headroom without hiding hangs.
  localparam int unsigned TIMEOUT_CYCLES = 1_000_000;

  logic clk_i;
  logic rst_ni;
  logic tck_i;
  logic tms_i;
  logic td_i;
  logic td_o;

  my_soc_top dut (
      .clk_i (clk_i),
      .rst_ni(rst_ni),
      .tck_i (tck_i),
      .tms_i (tms_i),
      .td_i  (td_i),
      .td_o  (td_o)
  );

  // 100 MHz clock (10 ns period)
  always #5 clk_i = ~clk_i;

  // Hierarchical handles into SRAM. dut.i_mainmem -> my_mainmem,
  // its inner i_mainmem -> sram_ff, whose storage array is `memory`.
  wire [31:0] magic_status = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 0];
  wire [31:0] magic_fail_i = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 1];
  wire [31:0] magic_fail_j = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 2];
  wire [31:0] magic_hw     = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 3];
  wire [31:0] magic_ref    = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 4];
  wire [31:0] magic_case   = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 5];

  int unsigned cycle_count;

  initial begin
    $dumpfile("soc/sim/out/my_soc_tb.vcd");
    $dumpvars(0, my_soc_tb);

    clk_i       = 1'b0;
    rst_ni      = 1'b0;
    tck_i       = 1'b0;
    tms_i       = 1'b0;
    td_i        = 1'b0;
    cycle_count = 0;

    repeat (8) @(posedge clk_i);
    rst_ni = 1'b1;
    $display("[%0t] reset released, polling magic_status @ 0x80001FE0", $time);

    while (cycle_count < TIMEOUT_CYCLES) begin
      @(posedge clk_i);
      cycle_count = cycle_count + 1;

      if (magic_status == STATUS_PASS) begin
        $display("[%0t] PASS: matrix-multiply self-check OK after %0d cycles",
                 $time, cycle_count);
        $finish;
      end
      if (magic_status == STATUS_FAIL) begin
        $display("[%0t] FAIL case=%0d @ (i=%0d, j=%0d) hw=%0d ref=%0d after %0d cycles",
                 $time, magic_case, magic_fail_i, magic_fail_j,
                 magic_hw, magic_ref, cycle_count);
        $finish;
      end
    end

    $display("[%0t] TIMEOUT after %0d cycles, magic_status=0x%08h",
             $time, TIMEOUT_CYCLES, magic_status);
    $finish;
  end

endmodule
