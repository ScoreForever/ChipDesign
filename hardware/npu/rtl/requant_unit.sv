`timescale 1ns/1ps
// Per-lane INT32 bias and TFLite-style double-rounding requantization.
// Lane 0 occupies the least-significant packed bits.
module requant_unit #(
    parameter LANES = 8,
    parameter SHIFT_WIDTH = 6
) (
    input  wire                              clk,
    input  wire                              rst,
    input  wire                              in_valid,
    output wire                              in_ready,
    input  wire [LANES-1:0]                  lane_mask,
    input  wire [LANES*32-1:0]               acc_data,
    input  wire [LANES*32-1:0]               bias_data,
    input  wire [LANES*32-1:0]               multiplier_data,
    input  wire [LANES*SHIFT_WIDTH-1:0]       shift_data,
    input  wire signed [31:0]                 output_offset,
    input  wire signed [7:0]                  activation_min,
    input  wire signed [7:0]                  activation_max,
    output reg  [LANES*8-1:0]                out_data,
    output reg                               out_valid,
    input  wire                              out_ready
);
    localparam signed [31:0] INT32_MIN_VALUE = 32'sh80000000;
    localparam signed [31:0] INT32_MAX_VALUE = 32'sh7fffffff;

    wire advance = !out_valid || out_ready;
    wire input_fire = in_valid && in_ready;
    wire [LANES*8-1:0] result;

    assign in_ready = advance && !rst;

    // gemmlowp/TFLite SaturatingRoundingDoublingHighMul. Division is used
    // here to make truncation toward zero explicit for negative products.
    function automatic signed [31:0] saturating_rounding_high_mul;
        input signed [31:0] a;
        input signed [31:0] b;
        reg signed [63:0] product;
        reg signed [63:0] nudge;
        reg signed [63:0] rounded_product;
        reg signed [63:0] magnitude;
        begin
            if (a == INT32_MIN_VALUE && b == INT32_MIN_VALUE) begin
                saturating_rounding_high_mul = INT32_MAX_VALUE;
            end else begin
                product = a * b;
                nudge = (product >= 0) ? 64'sd1073741824 : -64'sd1073741823;
                rounded_product = product + nudge;
                // Signed division by 2^31 with truncation toward zero. Write
                // it as shifts so synthesis cannot infer a general divider.
                if (rounded_product >= 0)
                    magnitude = rounded_product >>> 31;
                else
                    magnitude = -((-rounded_product) >>> 31);
                saturating_rounding_high_mul = magnitude[31:0];
            end
        end
    endfunction

    // gemmlowp/TFLite RoundingDivideByPOT: nearest with ties away from zero.
    function automatic signed [31:0] rounding_divide_by_pot;
        input signed [31:0] value;
        input integer exponent;
        reg [31:0] mask;
        reg [31:0] remainder;
        reg [31:0] threshold;
        reg signed [31:0] base;
        begin
            if (exponent <= 0) begin
                rounding_divide_by_pot = value;
            end else if (exponent >= 32) begin
                // shift=-32 is reserved by the programming contract.  Return
                // the underflow value deterministically if malformed direct
                // users bypass the MMIO validation instead of relying on an
                // undefined 32-bit mask shift.
                rounding_divide_by_pot = 32'sd0;
            end else begin
                mask = (32'h00000001 << exponent) - 1;
                remainder = $unsigned(value) & mask;
                threshold = (mask >> 1) + (value < 0);
                base = value >>> exponent;
                rounding_divide_by_pot = base + (remainder > threshold);
            end
        end
    endfunction

    // Positive shifts are applied before the Q0.31 multiply. Exported model
    // parameters must keep this left shift within INT32; saturation gives a
    // deterministic result if malformed parameters violate that contract.
    function automatic signed [31:0] saturating_left_shift;
        input signed [31:0] value;
        input integer amount;
        reg signed [63:0] wide;
        begin
            wide = value;
            wide = wide <<< amount;
            if (wide > 64'sh000000007fffffff)
                saturating_left_shift = INT32_MAX_VALUE;
            else if (wide < -64'sd2147483648)
                saturating_left_shift = INT32_MIN_VALUE;
            else
                saturating_left_shift = wide[31:0];
        end
    endfunction

    function automatic signed [31:0] multiply_by_quantized_multiplier;
        input signed [31:0] value;
        input signed [31:0] multiplier;
        input signed [SHIFT_WIDTH-1:0] shift;
        integer left_shift;
        integer right_shift;
        reg signed [31:0] shifted_value;
        reg signed [31:0] high_product;
        begin
            left_shift = (shift > 0) ? shift : 0;
            right_shift = (shift < 0) ? -shift : 0;
            shifted_value = saturating_left_shift(value, left_shift);
            high_product = saturating_rounding_high_mul(shifted_value, multiplier);
            multiply_by_quantized_multiplier =
                rounding_divide_by_pot(high_product, right_shift);
        end
    endfunction

    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin : requant_lane
            wire signed [31:0] lane_acc =
                $signed(acc_data[lane*32 +: 32]);
            wire signed [31:0] lane_bias =
                $signed(bias_data[lane*32 +: 32]);
            wire signed [31:0] lane_multiplier =
                $signed(multiplier_data[lane*32 +: 32]);
            wire signed [SHIFT_WIDTH-1:0] lane_shift =
                $signed(shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH]);
            wire signed [31:0] biased = lane_acc + lane_bias;
            wire signed [31:0] scaled =
                multiply_by_quantized_multiplier(biased, lane_multiplier, lane_shift);
            wire signed [32:0] offset_value =
                $signed({scaled[31], scaled}) + $signed({output_offset[31], output_offset});
            wire signed [32:0] min_extended =
                {{25{activation_min[7]}}, activation_min};
            wire signed [32:0] max_extended =
                {{25{activation_max[7]}}, activation_max};
            reg signed [7:0] clamped;
            always @* begin
                if (!lane_mask[lane])
                    clamped = 8'sd0;
                else if (offset_value < min_extended)
                    clamped = activation_min;
                else if (offset_value > max_extended)
                    clamped = activation_max;
                else
                    clamped = offset_value[7:0];
            end
            assign result[lane*8 +: 8] = clamped;
        end
    endgenerate

    always @(posedge clk) begin
        if (rst) begin
            out_data <= {LANES*8{1'b0}};
            out_valid <= 1'b0;
        end else if (advance) begin
            out_valid <= input_fire;
            if (input_fire)
                out_data <= result;
        end
    end
endmodule
