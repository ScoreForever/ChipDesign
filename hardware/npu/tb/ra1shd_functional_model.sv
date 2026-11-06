`timescale 1ns/1ps
// Portable synchronous 2048x32 1RW, active-low byte enables. This is an
// interface model for regression, not a foundry timing or signoff model.
module RA1SHD_2048x32M8(
 input wire CLK,CEN,OEN,input wire [3:0] WEN,input wire [10:0] A,
 input wire [31:0] D,output wire [31:0] Q
);
 reg [31:0] mem[0:2047],q;
 integer b;
 always @(posedge CLK)if(!CEN)begin
  if(WEN==4'hf)q<=mem[A];
  else for(b=0;b<4;b=b+1)if(!WEN[b])mem[A][b*8+:8]<=D[b*8+:8];
 end
 assign Q=OEN ? 32'bz : q;
endmodule
