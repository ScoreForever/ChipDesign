`timescale 1ns/1ps
module tb_npu_trace;
 reg clk=0;always #5 clk=~clk;
 reg clear=1,active=0,request=0;reg [127:0] sample=0;
 reg [3:0] events=0;reg [31:0] descriptor=0,control=1,offset=0;
 reg [15:0] address=0;wire valid;wire [31:0] data;wire [4:0] error;
 npu_trace_capture dut(.clk(clk),.clear(clear),.active(active),.sample(sample),.events(events),
  .descriptor(descriptor),.control(control),.offset(offset),.read_request(request),.read_addr(address),
  .read_valid(valid),.read_data(data),.read_error(error));
 task read_check(input [15:0] addr,input [31:0] expected,input [4:0] err);
 begin
  @(negedge clk);request=1;address=addr;
  @(negedge clk);if(!valid || data!==expected || error!==err)
   $fatal(1,"trace read addr=%h data=%h expected=%h err=%0d expected_err=%0d",addr,data,expected,error,err);
  request=0;@(negedge clk);if(valid)$fatal(1,"trace response did not clear");
 end endtask
 task reset_capture;
 begin @(negedge clk);clear=1;active=0;repeat(2)@(negedge clk);clear=0;end endtask
 task emit(input integer stamp,input integer pc,input [3:0] ev,input [31:0] sig);
 begin
  @(negedge clk);active=1;sample={4'h1,pc[3:0],4'h8,4'h1,16'h0123,32'h04560100,sig,stamp[31:0]};
  events=ev;descriptor=32'h00000301;
 end endtask
 task finish_capture;
 begin @(negedge clk);active=0;events=0;repeat(4)@(negedge clk);end endtask
 integer i;
 initial begin
  reset_capture();offset=2;
  for(i=0;i<7;i=i+1)emit(i,0,i==0 || i==1 ? 1 : i==2 ? 4 : i==6 ? 2 : 0,i==4 ? 'h400 : 'h80);
  for(i=7;i<11;i=i+1)emit(i,1,i==7 ? 1 : i==8 ? 4 : i==10 ? 2 : 0,'h100);
  finish_capture();
  read_check('h194,5,0);read_check('h198,2,0);read_check('h19c,6,0);read_check('h190,5,0);
  read_check('h200,0,0);read_check('h204,7,0);read_check('h208,'h301,0);read_check('h20c,11,0);
  read_check('h218,1,0);read_check('h21c,6,0);
  read_check('h220,7,0);read_check('h224,11,0);read_check('h230,4,0);
  for(i=0;i<5;i=i+1)begin
   read_check('h1000+i*16,i+2,0);
   read_check('h1008+i*16,'h04560100,0);read_check('h100c+i*16,'h10810123,0);
  end
  read_check('h1050,0,2);read_check('h1001,0,3);
  control='h31;offset=99;read_check('h1ac,1,0);read_check('h1b0,2,0);
  reset_capture();control=3;offset=2;
  for(i=0;i<7;i=i+1)emit(i,0,i==0 ? 1 : i==1 ? 4 : i==6 ? 2 : 0,i==4 ? 'h400 : 0);
  finish_capture();read_check('h194,3,0);read_check('h198,4,0);read_check('h1000,4,0);
  reset_capture();control='h11;offset=0;
  emit(0,0,1,0);emit(1,0,2,0);
  for(i=2;i<1102;i=i+1)emit(i,1,i==2 ? 1 : i==3 ? 4 : i==1101 ? 2 : 0,'h80);
  finish_capture();read_check('h194,1024,0);read_check('h190,15,0);
  read_check('h1000,2,0);read_check('h4ff0,1025,0);read_check('h5000,0,2);
  reset_capture();control=0;emit(0,0,1,0);emit(1,0,4,0);emit(2,0,8,0);finish_capture();
  read_check('h194,0,0);read_check('h20c,13,0);read_check('h204,3,0);
  reset_capture();control=1;emit(0,0,1,0);emit(1,0,4,0);
  reset_capture();finish_capture();read_check('h194,0,0);read_check('h1000,0,2);read_check('h20c,0,0);
  $display("PASS trace payload, per-operator boundaries, offset, trigger, overflow, failure, disable, reset and unread RAM protection");$finish;
 end
 initial begin #1000000;$fatal(1,"trace unit timeout");end
endmodule
