`timescale 1ns/1ps

// Gate-level/SDF regression for the smaller_cpu (PicoRV32) post-route netlist.
// Firmware is loaded directly into the RA1SHD vendor model's storage array
// before reset release. Hierarchical macro path i_mainmem/i_mainmem (this
// branch's Genus netlist keeps the my_mainmem wrapper).
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

  // 50 MHz (20 ns period).
  always #10 clk_i = ~clk_i;

  wire [31:0] magic_status = dut.i_mainmem.i_mainmem.mem[MAGIC_WORD];

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
    $sdf_annotate("my_soc_top_tt_io.sdf", dut, , "postlayout_sdf.log", "MAXIMUM");
    #1;
    $readmemh(firmware_file, dut.i_mainmem.i_mainmem.mem);

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
