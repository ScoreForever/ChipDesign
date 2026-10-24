`timescale 1ns/1ps
// The same command trace exercises native ready/valid and physical JTAG pins.
module tb_npu_program;
 reg clk=0,rst_n=0; always #5 clk=~clk;
 wire irq;
 reg [7:0] op; reg [31:0] addr,data,mask,expected,value;
 reg [4:0] expected_error;
 integer fd,rc,commands=0,waits=0;
 string trace_path;
 reg [63:0] events[0:65535];integer event_count=0,event_index=0;
 string event_path;
`ifdef JTAG_TEST
 reg tck=1,tms=1,tdi=0;wire tdo;
 my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));
 wire event_valid=dut.i_control.aw;
 wire [15:0] event_addr=dut.i_control.wa;
 wire [31:0] event_data=dut.i_control.wd;
 wire [3:0] event_mask=dut.i_control.wm;
 reg dummy;integer ticks=0;
 task tick(input reg m,input reg d,output reg q);
 begin tms=m;tdi=d;#3;tck=0;#9;q=tdo;tck=1;#4;ticks=ticks+1;end endtask
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
  scan({op,addr,data},reply);n=0;reply=0;
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
 wire event_valid=dut.aw;wire [15:0] event_addr=dut.wa;
 wire [31:0] event_data=dut.wd;wire [3:0] event_mask=dut.wm;
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
 wire [31:0] event_byte_mask={{8{event_mask[3]}},{8{event_mask[2]}},{8{event_mask[1]}},{8{event_mask[0]}}};
 always @(posedge clk)if(event_valid && event_count!=0)begin
  if(event_index>=event_count || event_addr!==events[event_index][63:48] ||
     event_mask!==events[event_index][35:32] ||
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
   else begin
    exchange;
    if(op==1 && (addr=='h38 || addr=='h40))$display("METRIC reg=%h value=%0d",addr,value);
    if((value&mask)!==(expected&mask))$fatal(1,"cmd=%0d op=%h addr=%h got=%h expected=%h mask=%h",commands,op,addr,value,expected,mask);
    commands=commands+1;
   end
  end
  $fclose(fd);
  if(event_index!=event_count)$fatal(1,"missing write events %0d/%0d",event_index,event_count);
  $display("PASS program commands=%0d",commands);$finish;
 end
 initial begin #1000000000;$fatal(1,"global timeout");end
endmodule
