`timescale 1ns/1ps
module my_soc_top #(
 parameter ACT_BYTES=8192,MODEL_BYTES=8192,
`ifdef NPU_ARRAY_COLS
 parameter COLS=`NPU_ARRAY_COLS,
`else
 parameter COLS=8,
`endif
 parameter MAX_OPS=16,MAX_K=1024
)(input wire clk_i,rst_ni,tck_i,tms_i,td_i,output wire td_o,irq_o);
 wire cv,cr,rv,rr; wire [7:0] op; wire [31:0] addr,data,rd; wire [4:0] err;
 npu_jtag_transport i_host (
  .clk_i(clk_i),.rst_ni(rst_ni),.tck_i(tck_i),.tms_i(tms_i),.td_i(td_i),.td_o(td_o),
  .cmd_valid_o(cv),.cmd_ready_i(cr),.cmd_op_o(op),.cmd_addr_o(addr),.cmd_data_o(data),
  .rsp_valid_i(rv),.rsp_ready_o(rr),.rsp_error_i(err),.rsp_data_i(rd));
 npu_control_subsystem #(.ACT_BYTES(ACT_BYTES),.MODEL_BYTES(MODEL_BYTES),.COLS(COLS),.MAX_OPS(MAX_OPS),.MAX_K(MAX_K)) i_control (
  .clk_i(clk_i),.rst_ni(rst_ni),.cmd_valid_i(cv),.cmd_ready_o(cr),.cmd_op_i(op),.cmd_addr_i(addr),.cmd_data_i(data),
  .rsp_valid_o(rv),.rsp_ready_i(rr),.rsp_error_o(err),.rsp_data_o(rd),.irq_o(irq_o));
endmodule
