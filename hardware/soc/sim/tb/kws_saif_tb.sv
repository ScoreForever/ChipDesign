`timescale 1ns/1ps
// Passive capture of the first input-load through result-read/clear window.
// Model setup is excluded. Pipelined firmware includes the overlapping next
// input DMA in this window; use --pipeline 0 for isolated-frame activity.
module kws_saif_tb;
  kws_workload_tb workload();
  bit recording = 0, complete = 0, cleared = 0, awaiting_status = 0;
  longint unsigned cycle = 0, first_cycle;
  initial begin
    $dumpfile("workload.vcd");
    $dumpvars(0, workload.dut);
    $dumpoff;
  end
  always @(posedge workload.clk_i) begin
    cycle = cycle + 1;
    if (workload.dut.ndmreset_n && !complete) begin
      // CPU loading starts at the legacy input window. For DMA, start at
      // the destination configuration for packed input, before CTRL/start
      // and the first SRAM read, so all input-transfer bus activity is kept.
      // Weight/parameter DMA destinations do not match this input address.
      if (!recording && workload.dut.npu_req && workload.dut.npu_we &&
          (workload.dut.npu_addr == 32'h70001000 ||
           (workload.dut.npu_addr == 32'h70000404 &&
            workload.dut.npu_wdata == 32'h70001800))) begin
        recording = 1;
        first_cycle = cycle;
        $dumpon;
        $display("SAIF_BEGIN time_ns=%0.0f cycle=%0d", $realtime, cycle);
      end
      if (recording && workload.dut.npu_req && workload.dut.npu_we &&
          workload.dut.npu_addr == 32'h70000000 &&
          workload.dut.npu_wdata == 32'h00000004)
        cleared = 1;
      // End on the response to idle() after all logits and CPU scaling.
      if (cleared && workload.dut.data_req && workload.dut.data_gnt &&
          !workload.dut.data_we && workload.dut.data_addr == 32'h70000004)
        awaiting_status = 1;
      if (awaiting_status && workload.dut.data_valid) begin
        $dumpoff;
        $dumpflush;
        complete = 1;
        recording = 0;
        $display("SAIF_END time_ns=%0.0f cycle=%0d cycles=%0d", $realtime, cycle, cycle-first_cycle);
      end
    end
  end
  final begin
    if (!complete) $error("FAIL: single-inference activity window did not complete");
  end
endmodule
