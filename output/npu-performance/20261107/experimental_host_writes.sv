`timescale 1ns/1ps
// Host acknowledgements may precede SRAM acceptance. Reconfiguration must not
// relocate accepted writes, reads must wait, and RESET cancels pending writes.
module tb_npu_host_writes;
 reg clk=0,rst_n=0;always #5 clk=~clk;
 reg cv=0;wire cr,rv;reg [7:0] op=0;reg [31:0] addr=0,data=0;
 wire [4:0] error;wire [31:0] response;
 npu_control_subsystem dut(.clk_i(clk),.rst_ni(rst_n),
  .cmd_valid_i(cv),.cmd_ready_o(cr),.cmd_op_i(op),.cmd_addr_i(addr),.cmd_data_i(data),
  .rsp_valid_o(rv),.rsp_ready_i(1'b1),.rsp_error_o(error),.rsp_data_o(response),.irq_o());
 task command(input [7:0] opcode,input [31:0] address,value,output [31:0] result);
 integer waits;
 begin
  @(negedge clk);cv=1;op=opcode;addr=address;data=value;waits=0;
  @(posedge clk);while(!cr)begin @(posedge clk);waits=waits+1;if(waits>1000)$fatal(1,"command ready timeout");end
  @(negedge clk);cv=0;
  while(!rv)begin @(negedge clk);waits=waits+1;if(waits>1000)$fatal(1,"command response timeout");end
  if(error!=0)$fatal(1,"host command op=%d addr=%h error=%d",opcode,address,error);
  result=response;
  @(negedge clk);
 end endtask
 reg [31:0] value,read_value;
 task configure(input [15:0] size);
 begin
  command(3,0,0,value);
  command(2,'h10,size,value);command(2,'h14,1,value);
  command(2,'h18,0,value);command(2,'h1c,4,value);
  command(2,'h20,4,value);command(2,'h24,4,value);command(2,'h64,0,value);
 end endtask
 reg read_done;
 initial begin
  repeat(5)@(negedge clk);rst_n=1;
  configure(8192);
  force dut.i_activation.i_store.write_ready=0;
  command(2,'h10000,32'ha55ac33c,value);
  if(!dut.host_write_pending)$fatal(1,"host write stimulus did not remain pending");
  command(3,0,0,value);command(2,'h10,4096,value);
  read_done=0;
  fork
   begin command(1,'h22000,0,read_value);read_done=1;end
   begin
    repeat(30)@(negedge clk);
    if(read_done)$fatal(1,"read overtook buffered write");
    release dut.i_activation.i_store.write_ready;
   end
  join
  if(read_value!==32'ha55ac33c)$fatal(1,"buffered model write/read ordering got %h",read_value);
  configure(4096);command(2,'h10000,32'h11112222,value);
  while(!dut.act_idle)@(negedge clk);
  command(9,0,0,value);configure(4096);
  force dut.i_activation.i_store.write_ready=0;
  command(2,'h10000,32'hdeadc0de,value);command(9,0,0,value);
  repeat(3)@(negedge clk);
  if(dut.host_write_pending)$fatal(1,"RESET retained buffered write");
  release dut.i_activation.i_store.write_ready;
  command(1,'h23000,0,read_value);
  if(read_value!==32'h11112222)$fatal(1,"RESET failed to preserve old SRAM word %h",read_value);
  $display("PASS host write backpressure, physical address snapshot, read barrier and reset cancellation");
  $finish;
 end
 initial begin #100000;$fatal(1,"host write queue timeout");end
endmodule
