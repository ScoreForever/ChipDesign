`timescale 1ns/1ps
// ABI 3: single Q15 lane, II=1, four stages. Saturating INT32 left shift,
// rounded signed 32x16 product / 2^15, rounded POT division, INT4 clamp.
// The validated multiplier is nonnegative (0..32767); shift is -31..31.
module requant_lane #(
    parameter SHIFT_WIDTH = 6,
    parameter TAG_WIDTH = 1
) (
    input  wire                         clk,
    input  wire                         rst,
    input  wire                         in_valid,
    input  wire signed [31:0]           in_value,
    input  wire signed [15:0]           in_multiplier,
    input  wire signed [SHIFT_WIDTH-1:0] in_shift,
    input  wire signed [3:0]            activation_min,
    input  wire signed [3:0]            activation_max,
    input  wire [TAG_WIDTH-1:0]         in_tag,
    output reg                          out_valid,
    output reg  [3:0]                   out_data,
    output reg  [TAG_WIDTH-1:0]         out_tag
);
    localparam signed [31:0] INT32_MIN_VALUE = 32'sh80000000;
    localparam signed [31:0] INT32_MAX_VALUE = 32'sh7fffffff;

    function automatic signed [31:0] saturating_left_shift;
        input signed [31:0] value;
        input integer amount;
        begin
            // Constant threshold shifts avoid a 64-bit barrel shifter.
            if (value > (INT32_MAX_VALUE >>> amount))
                saturating_left_shift = INT32_MAX_VALUE;
            else if (value < (INT32_MIN_VALUE >>> amount))
                saturating_left_shift = INT32_MIN_VALUE;
            else
                saturating_left_shift = value <<< amount;
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

    // Stage 1: left shift. Stage 2: 32x16 product. Stage 3: rounded high
    // half. Stage 4: rounding right shift and clamp.
    reg s1_valid, s2_valid, s3_valid;
    reg signed [31:0] s1_value;
    reg signed [15:0] s1_multiplier;
    reg signed [SHIFT_WIDTH-1:0] s1_shift, s2_shift, s3_shift;

    reg signed [47:0] s2_product;
    reg signed [31:0] s3_high;
    reg signed [3:0] s1_min, s1_max, s2_min, s2_max, s3_min, s3_max;
    reg [TAG_WIDTH-1:0] s1_tag, s2_tag, s3_tag;

    reg signed [31:0] s3_scaled;
    always @* begin
        s3_scaled = rounding_divide_by_pot(s3_high, (s3_shift < 0) ? -s3_shift : 0);
    end

    always @(posedge clk) begin
        if (rst) begin
            s1_valid <= 1'b0; s2_valid <= 1'b0; s3_valid <= 1'b0; out_valid <= 1'b0;
            s1_value <= 32'sd0; s1_multiplier <= 16'sd0; s1_shift <= '0;
            s2_shift <= '0; s3_shift <= '0;
            s2_product <= 48'sd0; s3_high <= 32'sd0;
            s1_min <= 4'sd0; s1_max <= 4'sd0; s2_min <= 4'sd0; s2_max <= 4'sd0;
            s3_min <= 4'sd0; s3_max <= 4'sd0;
            s1_tag <= '0; s2_tag <= '0; s3_tag <= '0; out_tag <= '0;
            out_data <= 4'd0;
        end else begin
            s1_valid <= in_valid;
            s1_value <= saturating_left_shift(in_value, (in_shift > 0) ? in_shift : 0);
            s1_multiplier <= in_multiplier;
            s1_shift <= in_shift;
            s1_min <= activation_min; s1_max <= activation_max; s1_tag <= in_tag;

            s2_valid <= s1_valid;
            s2_product <= s1_value * s1_multiplier;
            s2_shift <= s1_shift; s2_min <= s1_min; s2_max <= s1_max; s2_tag <= s1_tag;

            s3_valid <= s2_valid;
            s3_high <= $signed(s2_product[46:15]) + {31'd0,s2_product[14]};
            s3_shift <= s2_shift; s3_min <= s2_min; s3_max <= s2_max; s3_tag <= s2_tag;

            out_valid <= s3_valid;
            out_tag <= s3_tag;
            if (s3_scaled < s3_min)
                out_data <= s3_min;
            else if (s3_scaled > s3_max)
                out_data <= s3_max;
            else
                out_data <= s3_scaled[3:0];
        end
    end
endmodule
