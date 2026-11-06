`timescale 1ns/1ps
// Exercise the actual FPGA TAP mailbox, including its serial frontend.
module tb_npu_jtag_cdc;
  reg clk=0, rst_n=0, tck=0, tms=1, tdi=0;
  always #10 clk=~clk;
  wire tdo, valid, rsp_ready;
  wire [7:0] op;
  wire [31:0] addr, data;
  reg ready=0, rsp_valid=0;
  reg [31:0] rsp_data=0;
  reg [4:0] rsp_error=0;
  integer half_period=37, accepted=0, seed=137, n, before_count;
  reg [71:0] packet, response;
  reg [71:0] held_request;
  reg [36:0] held_response;
  reg request_held=0, response_held=0;
  npu_jtag_transport dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),
    .tms_i(tms),.td_i(tdi),.td_o(tdo),.cmd_valid_o(valid),.cmd_ready_i(ready),
    .cmd_op_o(op),.cmd_addr_o(addr),.cmd_data_o(data),.rsp_valid_i(rsp_valid),
    .rsp_ready_o(rsp_ready),.rsp_error_i(rsp_error),.rsp_data_i(rsp_data));
  always @(posedge clk) if(rst_n && valid && ready) accepted=accepted+1;
  // Observe source stability throughout each mailbox ownership interval.
  always @(negedge tck) begin
    if(!rst_n) request_held=0;
    else begin
      if(dut.pending && request_held && dut.req_payload !== held_request)
        $fatal(1,"request payload changed before acknowledgement");
      request_held=dut.pending; held_request=dut.req_payload;
    end
  end
  always @(negedge clk) begin
    if(!rst_n) response_held=0;
    else begin
      if(response_held && !dut.waiting_reply &&
        {dut.rsp_mailbox_error,dut.rsp_mailbox_data} !== held_response)
        $fatal(1,"response changed without another transaction");
      response_held=!dut.waiting_reply;
      held_response={dut.rsp_mailbox_error,dut.rsp_mailbox_data};
    end
  end
  task tick(input reg m, input reg d);
    begin
      tms=m; tdi=d; #(half_period); tck=1;
      #(half_period); tck=0; #1;
    end
  endtask
  task select_ir;
    integer i;
    begin
      repeat(6) tick(1,0); tick(0,0);
      tick(1,0); tick(1,0); tick(0,0); tick(0,0);
      for(i=0;i<5;i=i+1) tick(i==4,(5'h12>>i)&1);
      tick(1,0); tick(0,0);
    end
  endtask
  task scan(input reg [71:0] tx, output reg [71:0] rx);
    integer i;
    begin
      tick(1,0); tick(0,0); tick(0,0);
      for(i=0;i<72;i=i+1) begin rx[i]=tdo; tick(i==71,tx[i]); end
      tick(1,0); tick(0,0);
    end
  endtask
  task check_command(input reg [71:0] expected);
    begin
      wait(valid); #1;
      if({op,addr,data} !== expected) $fatal(1,"torn/incorrect request");
      repeat(7) begin @(negedge clk);
        if(!valid || {op,addr,data} !== expected) $fatal(1,"backpressure data changed");
      end
    end
  endtask
  task reply(input reg [31:0] value, input reg [4:0] error_code);
    begin
      @(negedge clk); ready=1;
      wait(rsp_ready); @(negedge clk); ready=0;
      repeat(3) @(negedge clk);
      rsp_data=value; rsp_error=error_code; rsp_valid=1;
      @(negedge clk); rsp_valid=0;
    end
  endtask
  task reset_all;
    begin
      rst_n=0; ready=0; rsp_valid=0; #31;
      if(dut.pending !== 0 || valid !== 0 || dut.waiting_reply !== 0)
        $fatal(1,"asynchronous reset failed with stopped TCK");
      rst_n=1; #83;
      if(dut.tck_rst_n !== 0) $fatal(1,"TCK reset released without clock");
      select_ir();
    end
  endtask
  initial begin
    // Ensure an initial reset event even on simulators with X initialization.
    #1; rst_n=1; #1; reset_all();
    for(n=0;n<100;n=n+1) begin
      // Sweep TCK faster/slower than 50 MHz, with unrelated edge phases.
      half_period=3+($random(seed)&127);
      packet={$random(seed),$random(seed),8'h21};
      packet[71:64]=8'h21;
      before_count=accepted;
      scan(packet,response); check_command(packet);
      // An extra request while pending must not overwrite the mailbox.
      scan({8'h22,64'hbadbadbadbadbadb},response);
      if(!dut.overrun || dut.req_payload !== packet) $fatal(1,"overrun corrupted mailbox");
      reply(packet[31:0]^32'h55aa1234,n%32);
      // No TCK edges while response arrives; mailbox must retain the reply.
      #503;
      if(!dut.pending) $fatal(1,"pending cleared without TCK");
      // TMS reset during pending must not cancel or duplicate the command.
      select_ir(); scan(0,response);
      if(response !== {1'b1,1'b0,1'b0,5'(n%32),packet[63:32],packet[31:0]^32'h55aa1234})
        $fatal(1,"incorrect response %h",response);
      if(accepted != before_count+1) $fatal(1,"duplicate/lost command");
    end
    // Reset a command held under destination backpressure with TCK stopped.
    packet={8'h31,64'h123456789abcdef0}; scan(packet,response); check_command(packet);
    reset_all();
    if(valid || dut.pending || dut.have_reply) $fatal(1,"stale request after reset");
    // Reset after command acceptance, before a reply, again without TCK.
    scan(packet,response); check_command(packet);
    @(negedge clk); ready=1; wait(rsp_ready); @(negedge clk); ready=0;
    reset_all();
    if(rsp_ready || dut.have_reply) $fatal(1,"stale response after reset");
    // Reset a published response that the stopped source has not observed.
    scan(packet,response); check_command(packet); reply(32'hdeadbeef,5'h1f);
    #101;
    if(!dut.pending) $fatal(1,"response consumed without TCK");
    reset_all();
    if(dut.have_reply || dut.pending) $fatal(1,"published response survived reset");
    // Fresh transaction after both cancellation cases.
    scan(packet,response); check_command(packet); reply(32'hcafef00d,0);
    repeat(4) tick(0,0); scan(0,response);
    if(response !== {8'h80,packet[63:32],32'hcafef00d}) $fatal(1,"restart failed");
    $display("PASS NPU_JTAG_CDC: 100 phase/rate sweeps, backpressure, overrun, stopped TCK, TMS reset, asynchronous reset, restart");
    $finish;
  end
  initial begin #100000000; $fatal(1,"timeout"); end
endmodule
