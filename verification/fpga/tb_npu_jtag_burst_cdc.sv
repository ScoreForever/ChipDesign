`timescale 1ns/1ps
// Real serial TAP, manual destination backpressure; no hierarchical forcing.
module tb_npu_jtag_burst_cdc;
  reg clk=0,rst_n=0,tck=0,tms=1,tdi=0,ready=0,rsp_valid=0;
  always #10 clk=~clk;
  wire tdo,valid,rsp_ready;
  wire [7:0] op;
  wire [31:0] addr,data;
  reg [31:0] rsp_data=0;
  reg [4:0] rsp_error=0;
  integer half_period=37,accepted=0,n,i,before_count,seed=419;
  reg [31:0] base,value,next_addr;
  reg [71:0] rx,held;
  npu_jtag_transport dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),
    .td_i(tdi),.td_o(tdo),.cmd_valid_o(valid),.cmd_ready_i(ready),.cmd_op_o(op),
    .cmd_addr_o(addr),.cmd_data_o(data),.rsp_valid_i(rsp_valid),.rsp_ready_o(rsp_ready),
    .rsp_error_i(rsp_error),.rsp_data_i(rsp_data));
  always @(posedge clk) if(rst_n && valid && ready) accepted=accepted+1;
  function [31:0] golden(input [31:0] address);
    golden=address ^ 32'ha5c3d71f;
  endfunction
  task tick(input reg m,input reg d);
    begin tms=m;tdi=d;#(half_period);tck=1;#(half_period);tck=0;#1;end
  endtask
  task idle(input integer count);
    integer j;begin for(j=0;j<count;j=j+1) tick(0,0);end
  endtask
  task tap_reset;
    begin repeat(6) tick(1,0);tick(0,0);end
  endtask
  task ir(input reg [4:0] instruction);
    integer j;
    begin
      tick(1,0);tick(1,0);tick(0,0);tick(0,0);
      for(j=0;j<5;j=j+1) tick(j==4,instruction[j]);
      tick(1,0);tick(0,0);
    end
  endtask
  task scan(input integer width,input reg [71:0] tx,output reg [71:0] result);
    integer j;
    begin
      result=0;tick(1,0);tick(0,0);tick(0,0);
      for(j=0;j<width;j=j+1) begin
        result[j]=tdo;tick(j==width-1,tx[j]);
        // Stop TCK inside Shift-DR as well as between completed transactions.
        if(j==11) #173;
      end
      tick(1,0);tick(0,0);
    end
  endtask
  task check(input reg [7:0] expected_op,input reg [31:0] expected_addr,input reg [31:0] expected_data);
    begin
      wait(valid);#1;
      if({op,addr,data} !== {expected_op,expected_addr,expected_data})
        $fatal(1,"burst command mismatch: op=%h addr=%h data=%h expected=%h/%h/%h",op,addr,data,expected_op,expected_addr,expected_data);
      held=dut.req_payload;
      repeat(7) begin @(negedge clk);
        if(!valid || {op,addr,data} !== {expected_op,expected_addr,expected_data} || dut.req_payload !== held)
          $fatal(1,"backpressure changed payload");
      end
    end
  endtask
  task reply(input reg [31:0] payload,input reg [4:0] error_code);
    begin
      @(negedge clk);ready=1;wait(rsp_ready);@(negedge clk);ready=0;
      repeat(3) @(negedge clk);
      rsp_data=payload;rsp_error=error_code;rsp_valid=1;
      @(negedge clk);rsp_valid=0;
    end
  endtask
  task reset_all;
    begin
      rst_n=0;ready=0;rsp_valid=0;#31;
      if(valid!==0 || dut.pending!==0 || rsp_ready!==0) $fatal(1,"stopped-clock reset failed");
      rst_n=1;#113;
      if(dut.tck_rst_n!==0) $fatal(1,"reset released without TCK");
      tap_reset();ir(5'h12);
    end
  endtask
  task anchor(input reg [7:0] operation,input reg [31:0] address,input reg [31:0] payload);
    begin
      ir(5'h12);scan(72,{operation,address,payload},rx);check(operation,address,payload);
      reply(golden(address),0);idle(8);
    end
  endtask
  initial begin
    #1;rst_n=1;#1;rst_n=0;
    for(n=0;n<32;n=n+1) begin
      half_period=3+($random(seed)&127);
      reset_all();
      case(n%4)
        0:base=32'h0000fffc;
        1:base=32'h0001fffc;
        2:base=32'hfffffffc;
        3:base={$random(seed)}&32'hfffffffc;
      endcase
      before_count=accepted;
      anchor(2,base,32'h12345678);ir(5'h13);
      for(i=1;i<=4;i=i+1) begin
        next_addr=base+4*i;value=$random(seed);
        scan(32,{40'd0,value},rx);check(2,next_addr,value);
        if(i==2) begin
          held=dut.req_payload;
          scan(32,72'hbadbadbad,rx);
          if(!dut.overrun || dut.req_payload!==held) $fatal(1,"pending burst overwritten");
        end
        reply(golden(next_addr),0);
        #503; // no TCK while response crosses and remains held
        if(!dut.pending) $fatal(1,"response consumed without TCK");
        idle(8);
      end
      ir(5'h12);scan(72,0,rx);
      if(rx[71:64]!==8'ha0 || rx[63:32]!==next_addr) $fatal(1,"sticky overrun/address lost");
      tap_reset(); // clear sticky scan status, preserve the completed mailbox
      anchor(1,base,0);ir(5'h14);
      for(i=1;i<=4;i=i+1) begin
        scan(32,0,rx);
        if(rx[31:0]!==golden(base+4*(i-1))) $fatal(1,"read pipeline torn/stale");
        next_addr=base+4*i;check(1,next_addr,0);reply(golden(next_addr),0);idle(4);
      end
      ir(5'h12);scan(72,0,rx);
      if(rx!=={8'h80,next_addr,golden(next_addr)}) $fatal(1,"final read/address missing");
      if(accepted!=before_count+10) $fatal(1,"duplicate/lost burst request");
      // TMS reset while a burst is held under core backpressure.
      ir(5'h13);scan(32,72'h1234,rx);check(2,next_addr+4,32'h1234);
      held=dut.req_payload;tap_reset();
      if(!dut.pending || dut.req_payload!==held) $fatal(1,"TMS canceled burst");
      reply(32'hcafef00d,0);idle(8);ir(5'h12);scan(72,0,rx);
      if(rx[71:64]!==8'h80 || rx[31:0]!==32'hcafef00d) $fatal(1,"TMS restart lost response");
      // Error from a burst response must reach the sticky status.
      ir(5'h14);scan(32,0,rx);check(1,next_addr+8,0);
      // Select status before the reply. One idle clock then scan places CDR
      // at the first completed capture, while the error flag is being latched.
      ir(5'h12);reply(0,5'd6);idle(1);scan(72,0,rx);
      if(rx[71:64]!==8'ha6) $fatal(1,"burst error not surfaced");
      scan(72,0,rx);
      if(rx[71:64]!==8'ha6) $fatal(1,"burst error was not sticky");
      // Global reset with TCK stopped while a burst command is pending.
      tap_reset();ir(5'h13);scan(32,72'h7777,rx);check(2,next_addr+12,32'h7777);
      reset_all();idle(8);
      if(valid || dut.pending || dut.have_reply || dut.overrun) $fatal(1,"stale state after reset");
      anchor(1,32'h20000,0);ir(5'h14);scan(32,0,rx);check(1,32'h20004,0);
      reply(32'hf00dcafe,0);idle(8);ir(5'h12);scan(72,0,rx);
      if(rx!=={8'h80,32'h20004,32'hf00dcafe}) $fatal(1,"fresh burst after reset failed");
    end
    $display("PASS NPU_JTAG_BURST_CDC: 32 rate/phase sweeps, word-address carry/wrap, read pipeline, backpressure, overrun/error, Shift-DR/TCK pause, TMS/global reset, restart");
    $finish;
  end
  initial begin #100000000;$fatal(1,"burst CDC timeout");end
endmodule
