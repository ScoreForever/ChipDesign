`timescale 1ns/1ps

// chipdesign_npu_irq_tb — drives the integrated ChipDesign SoC with the
// NPU interrupt test program and reports PASS / FAIL / TIMEOUT based on the
// magic region in SRAM.
//
// Magic region layout (agreed with software):
//   0x80001FE0 : magic_status (0xC0DEC0DE = PASS,
//                              0xDEADBEEF = FAIL,
//                              0x12345678 = RUNNING)
// SRAM is 32-bit / word; the magic region starts at word index 0x7F8.

module chipdesign_npu_irq_tb;

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
      .INIT_FILE("hardware/soc/sim/sw/chipdesign_npu_irq_test.hex")
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

  // Debug probes for interrupt bring-up.
  logic npu_irq_d;
  always_ff @(posedge clk_i) npu_irq_d <= dut.i_npu_subsystem.i_npu_core.irq_o;
  always_ff @(posedge clk_i) begin
    if (dut.i_npu_subsystem.i_npu_core.irq_o && !npu_irq_d)
      $display("[%0t] DEBUG: npu_irq rising (irq_o=1)", $time);
  end

  // Sanity check that the NPU IRQ is wired to CPU irq_i[16].
  logic irq_wired_d;
  always_ff @(posedge clk_i) irq_wired_d <= dut.i_npu_subsystem.i_npu_core.irq_o && !dut.i_cpu.irq_i[16];
  always_ff @(posedge clk_i) begin
    if (dut.i_npu_subsystem.i_npu_core.irq_o && !dut.i_cpu.irq_i[16] && !irq_wired_d)
      $display("[%0t] WARNING: npu irq_o=1 but CPU irq_i[16]=0", $time);
  end

  int unsigned cycle_count;

  initial begin
    $dumpfile("hardware/soc/sim/out/chipdesign_npu_irq_tb.vcd");
    $dumpvars(0, chipdesign_npu_irq_tb);

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
        $display("[%0t] PASS: NPU interrupt test OK after %0d cycles",
                 $time, cycle_count);
        $finish;
      end
      if (magic_status == STATUS_FAIL) begin
        $display("[%0t] FAIL: NPU interrupt test failed after %0d cycles",
                 $time, cycle_count);
        $finish;
      end
    end

    $display("[%0t] TIMEOUT after %0d cycles, magic_status=0x%08h",
             $time, TIMEOUT_CYCLES, magic_status);
    $display("  DEBUG: pc_if=0x%08h irq_i[16]=%0b core_sleep=%0b",
             dut.i_cpu.core_i.pc_if, dut.i_cpu.irq_i[16],
             dut.i_cpu.core_sleep_o);
    $finish;
  end

endmodule
