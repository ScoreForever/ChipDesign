`timescale 1ns/1ps
// Global spatial reduction followed by per-channel requantization to INT8.
// The multiplier/shift includes division by H*W and the FC input scale.
module global_avg_pool_engine #(
    parameter LANES=8,
    parameter DIM_WIDTH=8,
    parameter CHANNEL_WIDTH=8,
    parameter ADDR_WIDTH=16,
    parameter SHIFT_WIDTH=6
) (
    input wire clk,input wire rst,
    input wire start_valid,output wire start_ready,
    input wire [DIM_WIDTH-1:0] input_height,input_width,
    input wire [CHANNEL_WIDTH-1:0] channels,
    input wire [ADDR_WIDTH-1:0] input_base,output_base,
    input wire signed [31:0] output_offset,
    input wire signed [7:0] activation_min,activation_max,
    output wire busy,output reg done,
    output wire [ADDR_WIDTH-1:0] activation_read_addr,
    input wire [LANES*8-1:0] activation_read_data,
    output wire [ADDR_WIDTH-1:0] parameter_tile_addr,
    input wire [LANES*32-1:0] multiplier_read_data,
    input wire [LANES*SHIFT_WIDTH-1:0] shift_read_data,
    output wire output_write_valid,input wire output_write_ready,
    output wire [ADDR_WIDTH-1:0] output_write_addr,
    output wire [LANES*8-1:0] output_write_data,
    output wire [LANES-1:0] output_write_mask
);
    reg active;
    wire sum_start_ready,sum_done;
    wire sum_write_valid,sum_write_ready;
    wire [ADDR_WIDTH-1:0] sum_write_addr;
    wire [LANES*32-1:0] sum_write_data;
    wire [LANES-1:0] sum_write_mask;
    wire rq_in_ready,rq_out_valid;
    wire [LANES*8-1:0] rq_out_data;
    reg [ADDR_WIDTH-1:0] held_addr;
    reg [LANES-1:0] held_mask;
    reg final_requant_pending;

    assign start_ready=!active && sum_start_ready && !rst;
    assign busy=active;
    assign parameter_tile_addr=(sum_write_addr-output_base)/LANES;
    assign sum_write_ready=rq_in_ready;
    assign output_write_valid=rq_out_valid;
    assign output_write_addr=held_addr;
    assign output_write_data=rq_out_data;
    assign output_write_mask=held_mask;

    global_sum_pool_engine #(.LANES(LANES),.DIM_WIDTH(DIM_WIDTH),
        .CHANNEL_WIDTH(CHANNEL_WIDTH),.ADDR_WIDTH(ADDR_WIDTH)) sum_engine(
        .clk(clk),.rst(rst),.start_valid(start_valid && start_ready),
        .start_ready(sum_start_ready),.input_height(input_height),
        .input_width(input_width),.channels(channels),.input_base(input_base),
        .output_base(output_base),.busy(),.done(sum_done),
        .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .output_write_valid(sum_write_valid),.output_write_ready(sum_write_ready),
        .output_write_addr(sum_write_addr),.output_write_data(sum_write_data),
        .output_write_mask(sum_write_mask));

    requant_unit #(.LANES(LANES),.SHIFT_WIDTH(SHIFT_WIDTH)) requant(
        .clk(clk),.rst(rst),.in_valid(sum_write_valid),.in_ready(rq_in_ready),
        .lane_mask(sum_write_mask),.acc_data(sum_write_data),
        .bias_data({LANES*32{1'b0}}),.multiplier_data(multiplier_read_data),
        .shift_data(shift_read_data),.output_offset(output_offset),
        .activation_min(activation_min),.activation_max(activation_max),
        .out_data(rq_out_data),.out_valid(rq_out_valid),.out_ready(output_write_ready));

    always @(posedge clk) begin
        if(rst)begin
            active<=0;done<=0;held_addr<=0;held_mask<=0;final_requant_pending<=0;
        end else begin
            done<=0;
            if(start_valid && start_ready)begin
                active<=1;final_requant_pending<=0;
            end
            if(sum_write_valid && sum_write_ready)begin
                held_addr<=sum_write_addr;held_mask<=sum_write_mask;
                if(sum_done)final_requant_pending<=1;
            end
            if(sum_done)final_requant_pending<=1;
            if(rq_out_valid && output_write_ready &&
               (final_requant_pending || sum_done))begin
                active<=0;done<=1;final_requant_pending<=0;
            end
        end
    end
endmodule
