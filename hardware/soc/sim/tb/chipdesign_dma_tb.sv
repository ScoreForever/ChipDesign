`timescale 1ns/1ps

// chipdesign_dma_tb — drives the integrated ChipDesign SoC with the NPU DMA
// test program and reports PASS/FAIL/TIMEOUT.
//
// Magic region layout (agreed with software):
//   0x80001FE0 : magic_status (0xC0DEC0DE = PASS,
//                              0xDEADBEEF = FAIL,
//                              0x12345678 = RUNNING)
// SRAM is 32-bit / word; the magic region starts at word index 0x7F8.

module chipdesign_dma_tb;

  localparam int unsigned MAGIC_WORD     = 32'h0000_07F8;
  localparam logic [31:0] STATUS_RUNNING = 32'h1234_5678;
  localparam logic [31:0] STATUS_PASS    = 32'hC0DE_C0DE;
  localparam logic [31:0] STATUS_FAIL    = 32'hDEAD_BEEF;
  localparam int unsigned TIMEOUT_CYCLES = 1_000_000;

  logic clk_i;
  logic rst_ni;
  logic tck_i;
  logic tms_i;
  logic td_i;
  logic td_o;

  my_soc_top #(
      .INIT_FILE("hardware/soc/sim/sw/chipdesign_dma_test.hex")
  ) dut (
      .clk_i (clk_i),
      .rst_ni(rst_ni),
      .tck_i (tck_i),
      .tms_i (tms_i),
      .td_i  (td_i),
      .td_o  (td_o)
  );

  // 100 MHz clock (10 ns period)
  always #5 clk_i = ~clk_i;

  // Hierarchical read of SRAM memory array.
  wire [31:0] magic_status = dut.i_mainmem.i_mainmem.memory[MAGIC_WORD + 0];

  int unsigned cycle_count;

  initial begin
    $dumpfile("hardware/soc/sim/out/chipdesign_dma_tb.vcd");
    $dumpvars(0, chipdesign_dma_tb);

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
        $display("[%0t] PASS: NPU DMA test OK after %0d cycles",
                 $time, cycle_count);
        $finish;
      end
      if (magic_status == STATUS_FAIL) begin
        $display("[%0t] FAIL: NPU DMA test failed after %0d cycles",
                 $time, cycle_count);
        $finish;
      end
    end

    $display("[%0t] TIMEOUT after %0d cycles, magic_status=0x%08h",
             $time, TIMEOUT_CYCLES, magic_status);
    $finish;
  end

endmodule
