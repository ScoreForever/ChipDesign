`timescale 1ns/1ps
// Single-lane, fully pipelined TFLite-style requantizer (II=1, 4 stages).
//
// Arithmetic is bit-identical to requant_unit with zero bias and zero output
// offset: saturating left shift, SaturatingRoundingDoublingHighMul,
// RoundingDivideByPOT, then clamp to [activation_min, activation_max]. The
// fused core feeds one INT32 value per cycle after 2x2 max pooling, so one
// lane replaces the eight parallel 32x32 multipliers of the layered core.
module requant_lane #(
    parameter SHIFT_WIDTH = 6,
    parameter TAG_WIDTH = 1
) (
    input  wire                         clk,
    input  wire                         rst,
    input  wire                         in_valid,
    input  wire signed [31:0]           in_value,
    input  wire signed [31:0]           in_multiplier,
    input  wire signed [SHIFT_WIDTH-1:0] in_shift,
    input  wire signed [7:0]            activation_min,
    input  wire signed [7:0]            activation_max,
    input  wire [TAG_WIDTH-1:0]         in_tag,
    output reg                          out_valid,
    output reg  [7:0]                   out_data,
    output reg  [TAG_WIDTH-1:0]         out_tag
);
    localparam signed [31:0] INT32_MIN_VALUE = 32'sh80000000;
    localparam signed [31:0] INT32_MAX_VALUE = 32'sh7fffffff;

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
                // Reserved shift=-32: same deterministic underflow as requant_unit.
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

    // Stage 1: left shift. Stage 2: 32x32 product. Stage 3: rounded high
    // half. Stage 4: rounding right shift and clamp.
    reg s1_valid, s2_valid, s3_valid;
    reg signed [31:0] s1_value, s1_multiplier;
    reg signed [SHIFT_WIDTH-1:0] s1_shift, s2_shift, s3_shift;
    reg s2_exceptional;
    reg signed [63:0] s2_product;
    reg signed [31:0] s3_high;
    reg signed [7:0] s1_min, s1_max, s2_min, s2_max, s3_min, s3_max;
    reg [TAG_WIDTH-1:0] s1_tag, s2_tag, s3_tag;

    reg signed [31:0] s3_scaled;
    always @* begin
        s3_scaled = rounding_divide_by_pot(s3_high, (s3_shift < 0) ? -s3_shift : 0);
    end

    always @(posedge clk) begin
        if (rst) begin
            s1_valid <= 1'b0; s2_valid <= 1'b0; s3_valid <= 1'b0; out_valid <= 1'b0;
            s1_value <= 32'sd0; s1_multiplier <= 32'sd0; s1_shift <= '0;
            s2_shift <= '0; s3_shift <= '0; s2_exceptional <= 1'b0;
            s2_product <= 64'sd0; s3_high <= 32'sd0;
            s1_min <= 8'sd0; s1_max <= 8'sd0; s2_min <= 8'sd0; s2_max <= 8'sd0;
            s3_min <= 8'sd0; s3_max <= 8'sd0;
            s1_tag <= '0; s2_tag <= '0; s3_tag <= '0; out_tag <= '0;
            out_data <= 8'd0;
        end else begin
            s1_valid <= in_valid;
            s1_value <= saturating_left_shift(in_value, (in_shift > 0) ? in_shift : 0);
            s1_multiplier <= in_multiplier;
            s1_shift <= in_shift;
            s1_min <= activation_min; s1_max <= activation_max; s1_tag <= in_tag;

            s2_valid <= s1_valid;
            s2_product <= s1_value * s1_multiplier;
            s2_exceptional <= (s1_value == INT32_MIN_VALUE) &&
                              (s1_multiplier == INT32_MIN_VALUE);
            s2_shift <= s1_shift; s2_min <= s1_min; s2_max <= s1_max; s2_tag <= s1_tag;

            s3_valid <= s2_valid;
            s3_high <= s2_exceptional ? INT32_MAX_VALUE : s2_product[62:31] + {31'd0, s2_product[30]};
            s3_shift <= s2_shift; s3_min <= s2_min; s3_max <= s2_max; s3_tag <= s2_tag;

            out_valid <= s3_valid;
            out_tag <= s3_tag;
            if (s3_scaled < s3_min)
                out_data <= s3_min;
            else if (s3_scaled > s3_max)
                out_data <= s3_max;
            else
                out_data <= s3_scaled[7:0];
        end
    end
endmodule
