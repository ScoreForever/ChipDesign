`timescale 1ns/1ps
// The same command trace exercises native ready/valid and physical JTAG pins.
module tb_npu_program;
 reg clk=0,rst_n=0; always #5 clk=~clk;
 wire irq;
 reg [7:0] op; reg [31:0] addr,data,mask,expected,value;
 reg [4:0] expected_error;
 integer fd,rc,commands=0,waits=0,bursts=0;
`ifdef JTAG_TEST
`define PROGRAM_CORE dut.i_control.i_npu
`else
`define PROGRAM_CORE dut.i_npu
`endif
 always @(posedge clk)if(rst_n && !`PROGRAM_CORE.rst && `PROGRAM_CORE.state==`PROGRAM_CORE.EXECUTE &&
  `PROGRAM_CORE.mac_op && !`PROGRAM_CORE.kernel_prepare && `PROGRAM_CORE.issue_more &&
  `PROGRAM_CORE.gather_state==0 && `PROGRAM_CORE.issue_p!==0)
  $fatal(1,"initial activation gather must be position zero");
 always @(posedge clk)if(rst_n && !`PROGRAM_CORE.rst &&
  `PROGRAM_CORE.issue_fire && `PROGRAM_CORE.has_next &&
  `PROGRAM_CORE.peek_p!==(`PROGRAM_CORE.issue_p+5'd1))
  $fatal(1,"shadow request cursor lost alignment");
`undef PROGRAM_CORE

 string trace_path;
 reg [63:0] events[0:524287];integer event_count=0,event_index=0;
 string event_path;
`ifdef JTAG_TEST
 reg tck=1,tms=1,tdi=0;wire tdo;
 my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));
 wire event_valid=dut.i_control.aw && dut.i_control.i_npu.w_ready;
 wire [15:0] event_addr=dut.i_control.wa;
 wire [31:0] event_data=dut.i_control.wd;
 wire [7:0] event_mask=dut.i_control.wm;
 reg dummy;integer ticks=0;
 // TCK period in ns (+TCK_NS, default 16 for fast regression; 1000 = 1 MHz).
 real tck_ns=16;
 task tick(input reg m,input reg d,output reg q);
 begin tms=m;tdi=d;#(tck_ns*3/16);tck=0;#(tck_ns*9/16);q=tdo;tck=1;#(tck_ns*4/16);ticks=ticks+1;end endtask
 reg [4:0] cur_ir=5'h12;
 task ir_select(input [4:0] code);
 integer k;
 begin
  if(cur_ir!==code)begin
   tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
   for(k=0;k<5;k=k+1)tick(k==4,code[k],dummy);
   tick(1,0,dummy);tick(0,0,dummy);cur_ir=code;
  end
 end endtask
 task scan32(input [31:0] request,output reg [31:0] response);
 integer n;reg q;
 begin
  response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(n=0;n<32;n=n+1)begin tick(n==31,request[n],q);response[n]=q;end
  tick(1,0,dummy);tick(0,0,dummy);
 end endtask
 // Burst reads return the previous word; compare it one scan later.
 reg have_prev=0;reg [31:0] prev_expected,prev_mask,burst_reply;
 task scan(input [71:0] request,output reg [71:0] response);
 integer n;reg q;
 begin
  response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(n=0;n<72;n=n+1)begin tick(n==71,request[n],q);response[n]=q;end
  tick(1,0,dummy);tick(0,0,dummy);
 end endtask
 task exchange;
 reg [71:0] reply;integer n;
 begin
  ir_select(5'h12);
  scan({op,addr,data},reply);
  if(reply[69])$fatal(1,"JTAG burst fault before cmd=%0d",commands);
  if(have_prev && (reply[31:0]&prev_mask)!==(prev_expected&prev_mask))
   $fatal(1,"burst read tail got=%h expected=%h",reply[31:0],prev_expected);
  have_prev=0;n=0;reply=0;
  while(!reply[71] || reply[70])begin
   scan(0,reply);n=n+1;if(n>100)$fatal(1,"JTAG command timeout cmd=%0d op=%h addr=%h tap=%h ir=%h reply=%h pending=%h cv=%h cr=%h controller=%h",commands,op,addr,dut.i_host.tap,dut.i_host.ir,reply,dut.i_host.pending,dut.cv,dut.cr,dut.i_control.i_controller.state);
  end
  if(reply[69] || reply[68:64]!==expected_error || reply[63:32]!==addr)
   $fatal(1,"JTAG cmd=%0d op=%h addr=%h response=%h expected_error=%0d",commands,op,addr,reply,expected_error);
  value=reply[31:0];
 end endtask
`else
 reg cv=0,rr=0;wire cr,rv;wire [4:0] err;wire [31:0] rd;
`ifdef COLS4
 localparam C=4;
`else
 localparam C=8;
`endif
 npu_control_subsystem #(.COLS(C)) dut(.clk_i(clk),.rst_ni(rst_n),.cmd_valid_i(cv),.cmd_ready_o(cr),
  .cmd_op_i(op),.cmd_addr_i(addr),.cmd_data_i(data),.rsp_valid_o(rv),.rsp_ready_i(rr),
  .rsp_error_o(err),.rsp_data_o(rd),.irq_o(irq));
 wire event_valid=dut.aw && dut.i_npu.w_ready;wire [15:0] event_addr=dut.wa;
 wire [31:0] event_data=dut.wd;wire [7:0] event_mask=dut.wm;
 task exchange;
 reg [31:0] saved;reg [4:0] saved_error;
 begin
  @(negedge clk);cv=1;while(!cr)@(negedge clk);
  @(negedge clk);cv=0;while(!rv)@(negedge clk);
  saved=rd;saved_error=err;
  // Response backpressure must retain the response and never reexecute it.
  repeat(commands%4)begin @(negedge clk);if(!rv || rd!==saved || err!==saved_error)$fatal(1,"response changed under backpressure");end
  value=rd;
  if(err!==expected_error)$fatal(1,"cmd=%0d op=%h addr=%h error=%0d expected=%0d",commands,op,addr,err,expected_error);
  rr=1;@(negedge clk);rr=0;
 end endtask
`endif
 wire [31:0] event_byte_mask;
 genvar em;generate for(em=0;em<8;em=em+1)begin:mask_bits
 assign event_byte_mask[em*4+:4]={4{event_mask[em]}};end endgenerate
 always @(posedge clk)if(event_valid && event_count!=0)begin
  if(event_index>=event_count || event_addr!==events[event_index][63:48] ||
     event_mask!==events[event_index][39:32] ||
     (event_data&event_byte_mask)!==(events[event_index][31:0]&event_byte_mask))
   $fatal(1,"write event %0d addr=%h mask=%h data=%h expected=%h",event_index,event_addr,event_mask,event_data,events[event_index]);
  event_index=event_index+1;
 end
 initial begin
  if($value$plusargs("EVENTS=%s",event_path))begin
   if(!$value$plusargs("EVENT_COUNT=%d",event_count))$fatal(1,"event count required");
   $readmemh(event_path,events,0,event_count-1);
  end
  if(!$value$plusargs("TRACE=%s",trace_path))$fatal(1,"TRACE required");
`ifdef JTAG_TEST
  if($value$plusargs("TCK_NS=%f",tck_ns))$display("TCK period %0.1f ns",tck_ns);
`endif
  fd=$fopen(trace_path,"r");if(!fd)$fatal(1,"trace missing");
  // An explicit assertion edge initializes the stopped TCK domain too.
  #1;rst_n=1;#1;rst_n=0;
  repeat(5)@(negedge clk);rst_n=1;repeat(5)@(negedge clk);
`ifdef JTAG_TEST
  repeat(6)tick(1,0,dummy);tick(0,0,dummy);
  tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(rc=0;rc<5;rc=rc+1)tick(rc==4,(5'h12>>rc)&1,dummy);
  tick(1,0,dummy);tick(0,0,dummy);
`endif
  while(!$feof(fd))begin
   rc=$fscanf(fd,"%h %h %h %h %h %h\n",op,addr,data,expected_error,mask,expected);
   if(rc!=6)$fatal(1,"malformed trace");
   if(op=='hf0)begin waits=0;while(!irq)begin @(negedge clk);waits=waits+1;if(waits>2000000)$fatal(1,"job timeout");end end
   else if(op=='hf1)repeat(data)@(negedge clk);
   // Phase marker for the end-to-end timing breakdown.
`ifdef JTAG_TEST
   else if(op=='hf2)$display("PHASE %0d time_ns=%0d ticks=%0d",data,$time,ticks);
`else
   else if(op=='hf2)$display("PHASE %0d time_ns=%0d ticks=0",data,$time);
`endif
`ifdef JTAG_TEST
   else if(op=='h80)begin ir_select(5'h13);scan32(data,burst_reply);bursts=bursts+1;end
   else if(op=='h81)begin
    // Four Run-Test/Idle clocks let the read cross back through the TCK
    // synchronizer before the next Capture-DR (host: runtest 4).
    // The fast default TCK (16 ns) is barely slower than the 10 ns system
    // clock, so the system-side latency also counts: idle 8 clocks there.
    ir_select(5'h14);
    if(dut.i_host.pending)$fatal(1,"burst read captured before the previous read completed");
    scan32(0,burst_reply);repeat(tck_ns<100 ? 8 : 4)tick(0,0,dummy);bursts=bursts+1;
    if(have_prev && (burst_reply&prev_mask)!==(prev_expected&prev_mask))
     $fatal(1,"burst read got=%h expected=%h",burst_reply,prev_expected);
    have_prev=1;prev_expected=expected;prev_mask=mask;
   end
`else
   else if(op=='h80 || op=='h81)begin
    op=op=='h80 ? 2 : 1;exchange;bursts=bursts+1;
    if((value&mask)!==(expected&mask))$fatal(1,"burst cmd=%0d addr=%h got=%h expected=%h",commands,addr,value,expected);
   end
`endif
   else begin
    exchange;
    if(op==1 && (addr=='h38 || addr=='h40))$display("METRIC reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h100 && addr<'h160)$display("PERF reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h200 && addr<'h1200)$display("OPTRACE reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h190 && addr<='h19c)$display("TRACEHDR reg=%h value=%0d",addr,value);
    if((value&mask)!==(expected&mask))$fatal(1,"cmd=%0d op=%h addr=%h got=%h expected=%h mask=%h",commands,op,addr,value,expected,mask);
    commands=commands+1;
   end
  end
  $fclose(fd);
  if(event_index!=event_count)$fatal(1,"missing write events %0d/%0d",event_index,event_count);
`ifdef JTAG_TEST
  // Flush a trailing burst read and its sticky fault through IR=12h.
  if(have_prev)begin op=1;addr=4;data=0;expected_error=0;exchange;end
  $display("JTAG ticks=%0d tck_ns=%0.1f bursts=%0d sim_ns=%0t",ticks,tck_ns,bursts,$time);
`endif
  $display("PASS program commands=%0d",commands);$finish;
 end
 initial begin #1000000000;$fatal(1,"global timeout");end
endmodule
