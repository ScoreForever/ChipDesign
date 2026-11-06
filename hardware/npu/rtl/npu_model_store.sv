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

// Unified address space: activations span SRAM0 and the low part of SRAM1;
// the immutable model occupies the high end of SRAM1. All accesses share one coherent cache/write queue. Round-robin
// read arbitration prevents weight fetch and activation gather starving each
// other; a response owner is retained until the consumer accepts it.
module npu_shared_store #(parameter BYTES=16384)(
 input wire clk,rst,
 input wire [15:0] model_bytes,
 input wire model_write,output wire model_write_ready,
 input wire [15:0] model_write_addr,input wire [31:0] model_write_data,
 input wire model_read,output wire model_read_ready,input wire [15:0] model_read_addr,
 output wire model_rsp_valid,output wire [31:0] model_rsp_data,
 input wire read_valid,output wire read_ready,input wire [15:0] read_addr,
 output wire rsp_valid,input wire rsp_ready,output wire [31:0] rsp_data,
 input wire write_valid,output wire write_ready,input wire [15:0] write_addr,
 input wire [31:0] write_data,input wire [7:0] write_mask,
 input wire flush,output wire idle
);
 wire rr,rv,wr;wire [31:0] rd;
 reg owner,halfword,prefer_model;
 wire select_model=model_read && (!read_valid || prefer_model);
 assign model_read_ready=rr && select_model;
 assign read_ready=rr && !select_model;
 assign model_rsp_valid=rv && owner;
 assign rsp_valid=rv && !owner;
 assign model_rsp_data=halfword ? {16'd0,rd[15:0]} : rd;
 assign rsp_data=rd;
 assign model_write_ready=wr;
 assign write_ready=wr && !model_write;
 always @(posedge clk)begin
  if(rst)begin owner<=0;halfword<=0;prefer_model<=1;end
  else if(rr && (model_read || read_valid))begin
   owner<=select_model;halfword<=model_read_addr[1];prefer_model<=!select_model;
  end
 end
 npu_activation_store #(.BYTES(BYTES)) i_store(
  .clk(clk),.rst(rst),
  .read_valid(model_read || read_valid),.read_ready(rr),
  .read_addr(select_model ? ((16'd16384-model_bytes+model_read_addr)<<1) : read_addr),
  .rsp_valid(rv),.rsp_ready(owner || rsp_ready),.rsp_data(rd),
  .write_valid(model_write || write_valid),.write_ready(wr),
  .write_addr(model_write ? ((16'd16384-model_bytes+model_write_addr)<<1) : write_addr),
  .write_data(model_write ? model_write_data : write_data),
  .write_mask(model_write ? 8'hff : write_mask),
  .flush(flush || model_write),.idle(idle));
 initial if(BYTES!=16384)$fatal(1,"shared SRAM requires two 8KiB banks");
endmodule
