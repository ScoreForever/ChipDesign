`timescale 1ns/1ps

// Gate-level/SDF regression for the Innovus post-route netlist.
//
// Expected invocation supplies:
//   +firmware=<absolute path to chipdesign_npu_test.hex>
// Run from a directory containing an uncompressed my_soc_top_tt_io.sdf.
//
// The hard SRAM macro is retained in the synthesized netlist, so firmware is
// loaded directly into the vendor model's storage array before reset release.
module chipdesign_soc_postlayout_tb;

  localparam int unsigned MAGIC_WORD     = 32'h0000_07F8;
  localparam logic [31:0] STATUS_PASS    = 32'hC0DE_C0DE;
  localparam logic [31:0] STATUS_FAIL    = 32'hDEAD_BEEF;
  localparam int unsigned TIMEOUT_CYCLES = 1_000_000;

  logic clk_i;
  logic rst_ni;
  logic tck_i;
  logic tms_i;
  logic td_i;
  wire  td_o;

  string firmware_file;
  int unsigned cycle_count;
  logic seen_npu_irq;

  my_soc_top dut (
      .clk_i (clk_i),
      .rst_ni(rst_ni),
      .tck_i (tck_i),
      .tms_i (tms_i),
      .td_i  (td_i),
      .td_o  (td_o)
  );

  // Matches the 20 ns implementation constraint (50 MHz).
  always #10 clk_i = ~clk_i;

  wire [31:0] magic_status = dut.i_mainmem_i_mainmem.mem[MAGIC_WORD];

  // Genus preserves this top-level internal net name.  Checking it keeps the
  // post-layout regression equivalent to the RTL integrated-SoC regression.
  always @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni)
      seen_npu_irq <= 1'b0;
    else if (dut.npu_irq === 1'b1)
      seen_npu_irq <= 1'b1;
  end

  initial begin
    clk_i         = 1'b0;
    rst_ni        = 1'b0;
    tck_i         = 1'b0;
    tms_i         = 1'b0;
    td_i          = 1'b0;
    cycle_count   = 0;
    seen_npu_irq  = 1'b0;

    if (!$value$plusargs("firmware=%s", firmware_file))
      $fatal(1, "POSTLAYOUT_FAIL: +firmware=<hex> is required");
    // Xcelium requires SDF annotation before any delay/event control executes.
    $sdf_annotate("my_soc_top_tt_io.sdf", dut, , "postlayout_sdf.log", "MAXIMUM");
    // Allow vendor-model initialization to complete before overriding memory.
    #1;
    $readmemh(firmware_file, dut.i_mainmem_i_mainmem.mem);

    repeat (8) @(posedge clk_i);
    rst_ni = 1'b1;
    $display("[%0t] post-layout reset released; MAXIMUM SDF annotated", $time);

    while (cycle_count < TIMEOUT_CYCLES) begin
      @(posedge clk_i);
      cycle_count = cycle_count + 1;

      if (magic_status === STATUS_PASS) begin
        if (!seen_npu_irq)
          $fatal(1, "[%0t] POSTLAYOUT_FAIL: completed without NPU IRQ", $time);
        $display("[%0t] POSTLAYOUT_PASS: integrated SoC OK after %0d cycles",
                 $time, cycle_count);
        $finish;
      end

      if (magic_status === STATUS_FAIL)
        $fatal(1, "[%0t] POSTLAYOUT_FAIL: firmware error after %0d cycles",
               $time, cycle_count);
    end

    $fatal(1, "[%0t] POSTLAYOUT_TIMEOUT after %0d cycles; status=%08h irq=%0d",
           $time, TIMEOUT_CYCLES, magic_status, seen_npu_irq);
  end

endmodule
