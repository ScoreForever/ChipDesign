`timescale 1ns/1ps
// CPU-less production system. Dedicated JTAG host -> controller -> native NPU.
// The former CPU/AXI system is retained as my_soc_cpu_reference for comparison.
module my_soc_top (
    input wire clk_i, rst_ni,
    input wire tck_i, tms_i, td_i,
    output wire td_o,
    output wire irq_o
);
    wire cmd_valid, cmd_ready, rsp_valid, rsp_ready;
    wire [7:0] cmd_op;
    wire [31:0] cmd_addr, cmd_data, rsp_data;
    wire [4:0] rsp_error;
    npu_jtag_transport i_host (
        .clk_i(clk_i),.rst_ni(rst_ni),.tck_i(tck_i),.tms_i(tms_i),.td_i(td_i),.td_o(td_o),
        .cmd_valid_o(cmd_valid),.cmd_ready_i(cmd_ready),.cmd_op_o(cmd_op),
        .cmd_addr_o(cmd_addr),.cmd_data_o(cmd_data),
        .rsp_valid_i(rsp_valid),.rsp_ready_o(rsp_ready),.rsp_error_i(rsp_error),.rsp_data_i(rsp_data)
    );
    npu_control_subsystem i_control (
        .clk_i(clk_i),.rst_ni(rst_ni),.cmd_valid_i(cmd_valid),.cmd_ready_o(cmd_ready),
        .cmd_op_i(cmd_op),.cmd_addr_i(cmd_addr),.cmd_data_i(cmd_data),
        .rsp_valid_o(rsp_valid),.rsp_ready_i(rsp_ready),.rsp_error_o(rsp_error),.rsp_data_o(rsp_data),
        .irq_o(irq_o),.ext_valid_o(),.ext_ready_i(1'b0),.ext_write_o(),.ext_addr_o(),.ext_data_o(),
        .ext_rsp_valid_i(1'b0),.ext_rsp_ready_o(),.ext_rsp_data_i(32'd0),.ext_rsp_error_i(5'd0)
    );
endmodule
