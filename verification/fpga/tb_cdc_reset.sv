`timescale 1ns/1ps
module tb_cdc_reset;
  logic sys=0, tck=0, run_tck=1, reset_n=0;
  always #10 sys=~sys;
  always #37 if(run_tck) tck=~tck;
  wire trst_n;
  rstgen reset_sync(.clk_i(tck),.rst_ni(reset_n),.test_mode_i(1'b0),.rst_no(trst_n),.init_no());
  logic [31:0] tx=0, rx;
  logic valid=0,ready,rx_valid,consume=1;
  cdc_2phase #(.T(logic [31:0])) mailbox(
    .src_rst_ni(trst_n),.src_clk_i(tck),.src_data_i(tx),.src_valid_i(valid),.src_ready_o(ready),
    .dst_rst_ni(reset_n),.dst_clk_i(sys),.dst_data_o(rx),.dst_valid_o(rx_valid),.dst_ready_i(consume));
  task release_reset;
    reset_n=1;
    repeat(3) begin @(posedge tck); #1; if(trst_n!==0) $fatal(1,"early reset release"); end
    @(posedge tck); #1; if(trst_n!==1) $fatal(1,"reset not released");
  endtask
  task transfer(input logic [31:0] data);
    @(negedge tck); wait(ready); tx=data; valid=1;
    @(posedge tck); @(negedge tck); valid=0;
    wait(rx_valid); if(rx!==data) $fatal(1,"mailbox mismatch");
    wait(!rx_valid); wait(ready);
  endtask
  initial begin
    #1; reset_n=1; #1; reset_n=0; #11;
    release_reset(); transfer(32'h12345678);
    // Hold a destination transaction pending, stop TCK, then reset both sides.
    consume=0;
    @(negedge tck); tx=32'hdeadbeef; valid=1;
    @(posedge tck); @(negedge tck); valid=0;
    wait(rx_valid); run_tck=0;
    #17; reset_n=0; #1;
    if(trst_n!==0) $fatal(1,"reset did not assert without TCK");
    #81; reset_n=1; #250;
    if(trst_n!==0 || rx_valid!==0) $fatal(1,"stopped-clock reset failure");
    reset_n=0; #13; run_tck=1; release_reset(); consume=1;
    transfer(32'hcafef00d);
    $display("PASS CDC reset: stopped TCK, pending transaction, restart, fresh transfer");
    $finish;
  end
  initial begin #100000; $fatal(1,"CDC reset timeout"); end
endmodule
