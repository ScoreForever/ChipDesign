`timescale 1ns/1ps
module tb_npu_memories;
 reg clk=0,rst=1;always #5 clk=~clk;
 reg wv=0,rv=0,rr=0;reg [15:0] wa=0,ra=0;reg [31:0] wd=0;
 wire wr,rready,rvalid;wire [31:0] rd;
 npu_model_store dut(.clk(clk),.rst(rst),.write_valid(wv),.write_ready(wr),.write_addr(wa),.write_data(wd),
 .read_request(rv),.read_ready(rready),.read_addr(ra),.read_valid(rvalid),.read_response_ready(rr),.read_data(rd));
 reg av=0,arr=0,aw=0;reg [15:0] aa=0,awa=0;reg [31:0] awd=0;reg [3:0] mask=0;
 wire ar,arv;wire [31:0] ard;
 npu_activation_store act(.clk(clk),.rst(rst),.read_valid(av),.read_ready(ar),.read_addr(aa),
 .rsp_valid(arv),.rsp_ready(arr),.rsp_data(ard),.write_valid(aw),.write_addr(awa),.write_data(awd),.write_mask(mask));
 integer i,j;reg [31:0] saved,expected;
 initial begin
  repeat(3)@(negedge clk);rst=0;
  for(i=0;i<8;i=i+1) begin
   @(negedge clk);wv=1;wa=i*4;wd='h12340000+i;
   while(!wr)@(negedge clk);
  end
  @(negedge clk);wv=0;rv=1;ra=12;
  @(negedge clk);rv=0;while(!rvalid)@(negedge clk);saved=rd;
  if(saved!==32'h12340003)$fatal(1,"model read");
  repeat(5)begin @(negedge clk);if(!rvalid || rd!==saved || wr || rready)$fatal(1,"model response backpressure");end
  rr=1;@(negedge clk);rr=0;
  rv=1;ra=4;@(negedge clk);rv=0;while(!rvalid)@(negedge clk);
  rst=1;@(negedge clk);if(rvalid)$fatal(1,"pending response survived reset");rst=0;
  rv=1;ra=4;rr=1;@(negedge clk);rv=0;while(!rvalid)@(negedge clk);
  if(rd!==32'h12340001)$fatal(1,"reset corrupted model contents");
  for(i=0;i<32;i=i+1)begin @(negedge clk);aw=1;awa=i;awd=i+10;mask=1;end
  @(negedge clk);aw=0;
  for(i=0;i<28;i=i+1)begin
   av=1;aa=i;arr=0;@(negedge clk);av=0;
   while(!arv)@(negedge clk);
   for(j=0;j<4;j=j+1)expected[j*8+:8]=i+j+10;
   if(ard!==expected)$fatal(1,"unaligned activation read %0d got %h expected %h",i,ard,expected);
   saved=ard;repeat(2)begin @(negedge clk);if(!arv || ard!==saved || ar)$fatal(1,"activation response backpressure");end
   arr=1;@(negedge clk);arr=0;
  end
  $display("PASS model/activation synchronous memory, backpressure, reset, unaligned reads");$finish;
 end
 initial begin #100000;$fatal(1,"memory test timeout");end
endmodule
