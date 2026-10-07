`timescale 1ns/1ps
// Per-channel INT32 -> INT8 requantization: out = clamp((acc * scale) >>> shift + zp, -128, 127)
//
// Parameters:
//   LANES       - number of parallel channels (default 8, matches Matrix/Vector Unit)
//   ACC_WIDTH   - accumulator bit width (default 32)
//   SCALE_WIDTH - per-channel scale bit width (default 16, signed)
//   DATA_WIDTH  - output bit width (default 8)
//
// The unit is combinational: valid_o follows valid_i one-to-one.  The caller
// (npu_mmio_wrapper) latches the output on the rising edge of valid_i.

module requant_unit #(
    parameter int LANES       = 8,
    parameter int ACC_WIDTH   = 32,
    parameter int SCALE_WIDTH = 16,
    parameter int DATA_WIDTH  = 8
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        valid_i,
    input  logic [LANES*ACC_WIDTH-1:0]   acc_i,
    input  logic [LANES*SCALE_WIDTH-1:0] scale_i,
    input  logic [4:0]                   shift_i,
    input  logic [DATA_WIDTH-1:0]        zero_point_i,
    output logic [LANES*DATA_WIDTH-1:0]  out_o,
    output logic                         valid_o
);

    localparam int PROD_WIDTH = ACC_WIDTH + SCALE_WIDTH;

    generate
        for (genvar i = 0; i < LANES; i = i + 1) begin : gen_lane
            logic signed [ACC_WIDTH-1:0]   acc;
            logic signed [SCALE_WIDTH-1:0] scale;
            logic signed [PROD_WIDTH-1:0]  prod;
            logic signed [PROD_WIDTH-1:0]  shifted;
            logic signed [PROD_WIDTH-1:0]  with_zp;
            logic        [DATA_WIDTH-1:0]  lane_out;

            assign acc   = signed'(acc_i[i*ACC_WIDTH +: ACC_WIDTH]);
            assign scale = signed'(scale_i[i*SCALE_WIDTH +: SCALE_WIDTH]);
            assign prod  = acc * scale;
            assign shifted = prod >>> shift_i;
            assign with_zp = shifted + signed'({ {(PROD_WIDTH-DATA_WIDTH){zero_point_i[DATA_WIDTH-1]}},
                                                  zero_point_i });

            always_comb begin
                if (with_zp > PROD_WIDTH'(127))
                    lane_out = {1'b0, {DATA_WIDTH-1{1'b1}}}; // 127
                else if (with_zp < -PROD_WIDTH'(128))
                    lane_out = {1'b1, {DATA_WIDTH-1{1'b0}}}; // -128
                else
                    lane_out = with_zp[DATA_WIDTH-1:0];
            end

            assign out_o[i*DATA_WIDTH +: DATA_WIDTH] = lane_out;
        end
    endgenerate

    assign valid_o = valid_i;

endmodule
