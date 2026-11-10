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
 wire rr,rv,wr,store_idle;wire [31:0] rd;
`ifdef CHIPDESIGN_FPGA_MEM
 // Configuration settles before accesses. Hold new requests for the one
 // cycle needed to update the registered model base when its size changes.
 reg [15:0] model_bytes_q,model_base_q;
 reg base_valid;
 wire base_ready=base_valid && model_bytes_q==model_bytes;
 wire [15:0] model_base=model_base_q;
 always @(posedge clk)begin
  if(rst)base_valid<=0;
  else begin
   base_valid<=1;
   model_bytes_q<=model_bytes;
   model_base_q<=(16'd16384-model_bytes)<<1;
  end
 end
`else
 wire base_ready=1'b1;
 wire [15:0] model_base=(16'd16384-model_bytes)<<1;
`endif
 reg owner,halfword,prefer_model;
`ifdef CHIPDESIGN_FPGA_MEM
 // Reserve the activation turn after a model read. If no activation arrives,
 // release it on the next edge, before the registered model response returns.
 // The grant then depends only on registered arbitration state, keeping the
 // gather's mask/valid calculation out of the shared address selection.
 wire select_model=model_read && prefer_model;
 wire request_valid=(select_model || read_valid) && base_ready;
`else
 wire select_model=model_read && (!read_valid || prefer_model);
 wire request_valid=(model_read || read_valid) && base_ready;
`endif
 wire [15:0] request_addr=select_model ? model_base+(model_read_addr<<1) : read_addr;
 wire store_valid,store_model,store_halfword,request_ready;
 wire [15:0] store_addr;
 assign rsp_valid=rv && !owner;
 wire [31:0] model_word=halfword ? {16'd0,rd[15:0]} : rd;
`ifdef CHIPDESIGN_FPGA_MEM
 // Isolate the BRAM output from the next model request and store arbitration.
 // The model response interface consumes each word without backpressure.
 reg model_valid_q;
 reg [31:0] model_word_q;
 assign request_ready=rr && base_ready;
 assign store_valid=request_valid;
 assign store_addr=request_addr;
 assign store_model=select_model;
 assign store_halfword=model_read_addr[1];
 assign idle=store_idle && !model_valid_q && base_ready;
 always @(posedge clk) begin
  if(rst) model_valid_q<=0;
  else begin
   model_valid_q<=rv && owner;
   if(rv && owner) model_word_q<=model_word;
  end
 end
 assign model_rsp_valid=model_valid_q;
 assign model_rsp_data=model_word_q;
`else
 assign model_rsp_valid=rv && owner;
 assign model_rsp_data=model_word;
 assign request_ready=rr;
 assign store_valid=request_valid;
 assign store_addr=request_addr;
 assign store_model=select_model;
 assign store_halfword=model_read_addr[1];
 assign idle=store_idle;
`endif
 assign model_read_ready=request_ready && select_model;
 assign read_ready=request_ready && !select_model;
 assign rsp_data=rd;
 assign model_write_ready=wr && base_ready;
 assign write_ready=wr && !model_write && base_ready;
 always @(posedge clk)begin
  if(rst)begin owner<=0;halfword<=0;prefer_model<=1;end
  else begin
   if(request_ready && request_valid)prefer_model<=!select_model;
`ifdef CHIPDESIGN_FPGA_MEM
   else if(!prefer_model && !read_valid)prefer_model<=1;
`endif
   if(rr && store_valid)begin owner<=store_model;halfword<=store_halfword;end
  end
 end
 npu_activation_store #(.BYTES(BYTES)) i_store(
  .clk(clk),.rst(rst),
  .read_valid(store_valid),.read_ready(rr),.read_addr(store_addr),
  .rsp_valid(rv),.rsp_ready(owner || rsp_ready),.rsp_data(rd),
  .write_valid((model_write || write_valid) && base_ready),.write_ready(wr),
  .write_addr(model_write ? model_base+(model_write_addr<<1) : write_addr),
  .write_data(model_write ? model_write_data : write_data),
  .write_mask(model_write ? 8'hff : write_mask),
  .flush(flush || model_write),.idle(store_idle));
 initial if(BYTES!=16384)$fatal(1,"shared SRAM requires two 8KiB banks");
endmodule
