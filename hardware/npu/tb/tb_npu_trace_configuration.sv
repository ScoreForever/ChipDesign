`timescale 1ns/1ps
// Configuration matrix test: waveform payload is optional, summaries are not.
module tb_npu_trace_configuration;
 reg clk=0;always #5 clk=~clk;
 reg clear=1,active=0,request=0;
 reg [127:0] sample=0;reg [3:0] events=0;
 reg [31:0] descriptor=32'h301,control=1,offset=0;
 reg [15:0] address=0;
 wire valid;wire [31:0] data;wire [4:0] error;
 npu_trace_capture dut(.clk(clk),.clear(clear),.active(active),.sample(sample),.events(events),
  .descriptor(descriptor),.control(control),.offset(offset),.read_request(request),.read_addr(address),
  .read_valid(valid),.read_data(data),.read_error(error));
 task read_check(input [15:0] addr,input [31:0] value,input [4:0] err);
 begin
  @(negedge clk);request=1;address=addr;
  @(negedge clk);
  if(!valid || data!==value || error!==err)
   $fatal(1,"trace config read %h got %h/%d expected %h/%d",addr,data,error,value,err);
  request=0;@(negedge clk);
  if(valid)$fatal(1,"trace config response did not clear");
 end endtask
 task emit(input [31:0] stamp,input [3:0] ev,input [31:0] signals);
 begin
  @(negedge clk);active=1;events=ev;sample={32'd0,32'd0,signals,stamp};
 end endtask
 integer expected_wave;
 initial begin
  if(!$value$plusargs("WAVE=%d",expected_wave))$fatal(1,"WAVE expectation required");
  if(dut.WAVE_ENABLE!==expected_wave)$fatal(1,"wave default/override mismatch");
  repeat(2)@(negedge clk);clear=0;
  emit(10,1,'h80);emit(11,4,'h100);emit(12,2,'h600);
  @(negedge clk);active=0;events=0;repeat(4)@(negedge clk);
  read_check('h180,'h10080,0);read_check('h184,expected_wave ? 1024 : 0,0);
  read_check('h190,expected_wave ? 5 : 4,0);read_check('h194,expected_wave ? 3 : 0,0);
  read_check('h198,expected_wave ? 10 : 0,0);read_check('h19c,expected_wave ? 12 : 0,0);
  read_check('h188,1,0);read_check('h1ac,expected_wave ? 1 : 0,0);read_check('h1b0,0,0);
  // Summaries must be identical for every waveform configuration.
  read_check('h200,10,0);read_check('h204,13,0);read_check('h208,'h301,0);read_check('h20c,11,0);
  read_check('h210,1,0);read_check('h214,1,0);read_check('h218,1,0);read_check('h21c,1,0);
  read_check('h1000,expected_wave ? 10 : 0,expected_wave ? 0 : 2);
  read_check('h1001,0,3);read_check('h1030,0,2);
  @(negedge clk);clear=1;@(negedge clk);clear=0;
  read_check('h20c,0,0);read_check('h194,0,0);read_check('h1000,0,2);
  $display("PASS waveform configuration and operator summaries WAVE=%0d",expected_wave);
  $finish;
 end
 initial begin #100000;$fatal(1,"trace configuration timeout");end
endmodule
