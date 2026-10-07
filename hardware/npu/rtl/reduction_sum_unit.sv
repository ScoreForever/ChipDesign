`timescale 1ns/1ps
// Signed INT8 per-lane reduction into INT32 sums. The controller marks the
// first and last vectors of a reduction. Used by TinyCNN-8 global pooling.
module reduction_sum_unit #(
    parameter LANES = 8,
    parameter DATA_WIDTH = 8,
    parameter ACC_WIDTH = 32
) (
    input  wire                             clk,
    input  wire                             rst,
    input  wire                             in_valid,
    output wire                             in_ready,
    input  wire                             first,
    input  wire                             last,
    input  wire [LANES-1:0]                 lane_mask,
    input  wire [LANES*DATA_WIDTH-1:0]      in_data,
    output reg  [LANES*ACC_WIDTH-1:0]       out_sum,
    output reg                              out_valid,
    input  wire                             out_ready
);
    reg [LANES*ACC_WIDTH-1:0] accumulators;
    wire advance = !out_valid || out_ready;
    wire input_fire = in_valid && in_ready;

    // A completed, blocked output freezes the reduction state so the next
    // reduction cannot overwrite it.
    assign in_ready = advance && !rst;

    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin : reduction_lane
            wire signed [DATA_WIDTH-1:0] lane_input =
                $signed(in_data[lane*DATA_WIDTH +: DATA_WIDTH]);
            wire signed [ACC_WIDTH-1:0] lane_extended = lane_input;
            wire signed [ACC_WIDTH-1:0] old_accumulator =
                $signed(accumulators[lane*ACC_WIDTH +: ACC_WIDTH]);
            wire signed [ACC_WIDTH-1:0] next_sum = first ?
                (lane_mask[lane] ? lane_extended : {ACC_WIDTH{1'b0}}) :
                (old_accumulator +
                    (lane_mask[lane] ? lane_extended : {ACC_WIDTH{1'b0}}));

            always @(posedge clk) begin
                if (rst) begin
                    accumulators[lane*ACC_WIDTH +: ACC_WIDTH] <=
                        {ACC_WIDTH{1'b0}};
                    out_sum[lane*ACC_WIDTH +: ACC_WIDTH] <=
                        {ACC_WIDTH{1'b0}};
                end else if (input_fire) begin
                    accumulators[lane*ACC_WIDTH +: ACC_WIDTH] <= next_sum;
                    if (last)
                        out_sum[lane*ACC_WIDTH +: ACC_WIDTH] <= next_sum;
                end
            end
        end
    endgenerate

    always @(posedge clk) begin
        if (rst)
            out_valid <= 1'b0;
        else if (advance)
            out_valid <= input_fire && last;
    end
endmodule
