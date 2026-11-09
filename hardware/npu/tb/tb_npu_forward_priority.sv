`timescale 1ns/1ps
// Check youngest-write-wins at the combinational forwarding seam, including
// every queue head/count/match bitmap. End-to-end memory tests cover handshakes.
module tb_npu_forward_priority;
 reg [15:0] address=0;
 npu_activation_store dut(.clk(1'b0),.rst(1'b0),
  .read_valid(1'b0),.read_addr(address),.rsp_ready(1'b0),
  .write_valid(1'b0),.write_addr(16'd0),.write_data(32'd0),.write_mask(8'd0),.flush(1'b0));
 integer head_i,count_i,pattern,slot,age,n,at,checks=0;
 reg [31:0] seed=32'h98763210,expect0,expect1;
 reg [7:0] mask0,mask1;
 reg [10:0] word0,word1;
 initial begin
  for(head_i=0;head_i<8;head_i=head_i+1)
   for(count_i=0;count_i<=8;count_i=count_i+1)
    for(pattern=0;pattern<256;pattern=pattern+1)begin
     word0=pattern[7] ? 11'd2047 : 11'd17;word1=word0+11'd1;
     address={2'd0,word0,pattern[2:0]};
     dut.head=head_i;dut.count=count_i;
     for(slot=0;slot<8;slot=slot+1)begin
      seed={seed[30:0],seed[31]^seed[21]^seed[1]^seed[0]};
      dut.q_addr[slot]=pattern[slot] ? word0 : word1;
      dut.q_data[slot]=seed;dut.q_mask[slot]=seed[19:12];
     end
     expect0=0;expect1=0;mask0=0;mask1=0;
     for(age=0;age<count_i;age=age+1)begin
      at=(head_i+age)%8;
      for(n=0;n<8;n=n+1)if(dut.q_mask[at][n])begin
       if(dut.q_addr[at]==word0)begin
        expect0[n*4+:4]=dut.q_data[at][n*4+:4];mask0[n]=1;
       end
       if(dut.q_addr[at]==word1)begin
        expect1[n*4+:4]=dut.q_data[at][n*4+:4];mask1[n]=1;
       end
      end
     end
     #1;
     if(dut.forward_mask0!==mask0 || dut.forward_mask1!==mask1 ||
        dut.forward_data0!==expect0 || dut.forward_data1!==expect1)
      $fatal(1,"forward priority head=%0d count=%0d bitmap=%0h",head_i,count_i,pattern);
     checks=checks+1;
    end
  $display("PASS forwarding priority: %0d head/count/match cases, word wrap and all nibble lanes",checks);
  $finish;
 end
endmodule
