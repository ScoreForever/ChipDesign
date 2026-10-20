`timescale 1ns/1ps
// Native backend adapter. Future programmable engines can replace this module
// without changing the command transport or its ready/valid contract.
module npu_control_subsystem #(
    parameter [31:0] DEFAULT_TIMEOUT = 100000,
    parameter ENABLE_EXTENSION = 0,
    parameter WEIGHT_SRAM = 1,
    parameter SPATIAL_TILE = 16,
    parameter ARRAY_ACC_WIDTH = 18,
    parameter PSUM_WIDTH = 23
) (
    input wire clk_i, rst_ni,
    input wire cmd_valid_i, output wire cmd_ready_o,
    input wire [7:0] cmd_op_i, input wire [31:0] cmd_addr_i, cmd_data_i,
    output wire rsp_valid_o, input wire rsp_ready_i,
    output wire [4:0] rsp_error_o, output wire [31:0] rsp_data_o,
    output wire irq_o, output wire ext_reset_o,
    output wire ext_valid_o, input wire ext_ready_i,
    output wire ext_write_o, output wire [31:0] ext_addr_o, ext_data_o,
    input wire ext_rsp_valid_i, output wire ext_rsp_ready_o,
    input wire [31:0] ext_rsp_data_i, input wire [4:0] ext_rsp_error_i
);
    wire core_reset, core_start, core_ready, core_busy, core_done;
    wire [3:0] classes;
    wire [255:0] logits, bias_data, multiplier_data;
    wire [47:0] shift_data;
    wire activation_we, weight_we, weight_ready, parameter_we, debug_en, debug_valid;
    wire [15:0] activation_addr, weight_addr, debug_addr;
    wire [7:0] activation_data;
    wire [63:0] weight_data;
    wire [1:0] parameter_layer;
    wire [31:0] debug_data, perf_total, perf_rows, perf_issues, perf_retires, perf_peak;
    wire [191:0] perf_layers;
    wire perf_valid, perf_overflow;
    assign ext_reset_o = !rst_ni || core_reset;

    npu_command_controller #(.DEFAULT_TIMEOUT(DEFAULT_TIMEOUT),.ENABLE_EXTENSION(ENABLE_EXTENSION)) i_controller (
        .clk_i(clk_i),.rst_ni(rst_ni),
        .cmd_valid_i(cmd_valid_i),.cmd_ready_o(cmd_ready_o),.cmd_op_i(cmd_op_i),
        .cmd_addr_i(cmd_addr_i),.cmd_data_i(cmd_data_i),
        .rsp_valid_o(rsp_valid_o),.rsp_ready_i(rsp_ready_i),.rsp_error_o(rsp_error_o),.rsp_data_o(rsp_data_o),
        .irq_o(irq_o),.core_reset_o(core_reset),.core_start_o(core_start),
        .core_ready_i(core_ready),.core_busy_i(core_busy),.core_done_i(core_done),.core_logits_i(logits),
        .class_count_o(classes),.activation_we_o(activation_we),.activation_addr_o(activation_addr),
        .activation_data_o(activation_data),.weight_we_o(weight_we),.weight_ready_i(weight_ready),.weight_addr_o(weight_addr),
        .weight_data_o(weight_data),.parameter_we_o(parameter_we),.parameter_layer_o(parameter_layer),
        .bias_o(bias_data),.multiplier_o(multiplier_data),.shift_o(shift_data),
        .debug_en_o(debug_en),.debug_addr_o(debug_addr),.debug_data_i(debug_data),.debug_valid_i(debug_valid),
        .perf_total_i(perf_total),.perf_weight_rows_i(perf_rows),.perf_issues_i(perf_issues),
        .perf_retires_i(perf_retires),.perf_peak_i(perf_peak),.perf_layers_i(perf_layers),
        .perf_valid_i(perf_valid),.perf_overflow_i(perf_overflow),
        .ext_valid_o(ext_valid_o),.ext_ready_i(ext_ready_i),.ext_write_o(ext_write_o),
        .ext_addr_o(ext_addr_o),.ext_data_o(ext_data_o),.ext_rsp_valid_i(ext_rsp_valid_i),
        .ext_rsp_ready_o(ext_rsp_ready_o),.ext_rsp_data_i(ext_rsp_data_i),.ext_rsp_error_i(ext_rsp_error_i)
    );
    tinycnn8_npu_fused #(.DEBUG_READBACK(1),.WEIGHT_SRAM(WEIGHT_SRAM),.SPATIAL_TILE(SPATIAL_TILE),
        .ARRAY_ACC_WIDTH(ARRAY_ACC_WIDTH),.PSUM_WIDTH(PSUM_WIDTH)) i_npu (
        .clk(clk_i),.rst(!rst_ni || core_reset),.start_valid(core_start),.start_ready(core_ready),
        .class_count(classes),.busy(core_busy),.done(core_done),.logits(logits),
        .host_activation_we(activation_we),.host_activation_addr(activation_addr),.host_activation_data(activation_data),
        .host_weight_we(weight_we),.host_weight_ready(weight_ready),.host_weight_addr(weight_addr),.host_weight_data(weight_data),
        .host_parameter_we(parameter_we),.host_parameter_layer(parameter_layer),.host_parameter_tile(8'd0),
        .host_bias_data(bias_data),.host_multiplier_data(multiplier_data),.host_shift_data(shift_data),
        .debug_read_en(debug_en),.debug_read_addr(debug_addr),.debug_read_data(debug_data),.debug_read_valid(debug_valid),
        .perf_total_cycles(perf_total),.perf_weight_rows(perf_rows),.perf_matrix_issues(perf_issues),
        .perf_matrix_retires(perf_retires),.perf_peak_inflight(perf_peak),.perf_layer_cycles(perf_layers),
        .perf_valid(perf_valid),.perf_overflow(perf_overflow)
    );
endmodule
