`timescale 1ns/1ps
// One shared 32-bit synchronous physical port for the entire immutable model.
// Byte addresses: descriptors/parameters align to 4; packed weight rows to 2.
// Halfword reads are zero-extended; a response is held until consumed.
module npu_model_store #(parameter BYTES=8192)(
 input wire clk,rst,input wire write_valid,output wire write_ready,
 input wire [15:0] write_addr,input wire [31:0] write_data,
 input wire read_request,output wire read_ready,input wire [15:0] read_addr,
 output wire read_valid,input wire read_response_ready,output wire [31:0] read_data
);
 reg pending,high_half;
 wire [31:0] memory_q;
 assign read_data=high_half ? {16'd0,memory_q[31:16]} : memory_q;
 wire available=(!pending || read_response_ready) && !rst;
 assign write_ready=available;
 assign read_ready=available && !write_valid;
 wire wf=write_valid && write_ready,rf=read_request && read_ready;
 wire enable=wf || rf;
 wire [10:0] address=wf ? write_addr[12:2] : read_addr[12:2];
 assign read_valid=pending && !rst;
`ifdef NPU_USE_RA1SHD_MODEL
 RA1SHD_2048x32M8 i_sram(.CLK(clk),.CEN(~enable),.OEN(1'b0),
  .WEN({4{!wf}}),.A(address),.D(write_data),.Q(memory_q));
`else
 reg [31:0] mem[0:BYTES/4-1];reg [31:0] q;
 always @(posedge clk) if(enable) begin
  if(wf) mem[address]<=write_data; else q<=mem[address];
 end
 assign memory_q=q;
`endif
 always @(posedge clk) begin
  if(rst) begin pending<=0;high_half<=0;end
  else if(available) begin pending<=rf;if(rf)high_half<=read_addr[1];end
 end
 initial if(BYTES<64 || BYTES>8192 || (BYTES&(BYTES-1))!=0)$fatal(1,"invalid model-store size");
endmodule
