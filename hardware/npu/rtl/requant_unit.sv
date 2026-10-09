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

    // Reuse one pipelined quantization datapath across all lanes. This trades
    // latency for substantially less multiplier and rounding logic.
    localparam INDEX_WIDTH = (LANES <= 1) ? 1 : $clog2(LANES);
    reg busy;
    reg [INDEX_WIDTH-1:0] lane_index;
    reg [1:0] lane_phase;
    reg [LANES-1:0] saved_lane_mask;
    reg [LANES*32-1:0] saved_acc_data, saved_bias_data, saved_multiplier_data;
    reg [LANES*SHIFT_WIDTH-1:0] saved_shift_data;
    reg signed [31:0] saved_output_offset;
    reg signed [7:0] saved_activation_min, saved_activation_max;
    reg saved_lane_mask_reg;
    reg signed [31:0] lane_shifted_reg, lane_multiplier_reg, lane_high_product_reg;
    reg signed [63:0] lane_product_reg;
    reg lane_highmul_overflow_reg;
    reg signed [SHIFT_WIDTH-1:0] lane_shift_reg;
    wire input_fire = in_valid && in_ready;

    assign in_ready = !busy && (!out_valid || out_ready) && !rst;

    // gemmlowp/TFLite SaturatingRoundingDoublingHighMul. Division is used
    // here to make truncation toward zero explicit for negative products.
    function automatic signed [31:0] saturating_rounding_high_mul_product;
        input signed [63:0] product;
        input exceptional;
        reg signed [63:0] nudge;
        reg signed [63:0] rounded_product;
        reg signed [63:0] magnitude;
        begin
            if (exceptional) begin
                saturating_rounding_high_mul_product = INT32_MAX_VALUE;
            end else begin
                nudge = (product >= 0) ? 64'sd1073741824 : -64'sd1073741823;
                rounded_product = product + nudge;
                // Signed division by 2^31 with truncation toward zero. Write
                // it as shifts so synthesis cannot infer a general divider.
                if (rounded_product >= 0)
                    magnitude = rounded_product >>> 31;
                else
                    magnitude = -((-rounded_product) >>> 31);
                saturating_rounding_high_mul_product = magnitude[31:0];
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

    wire signed [31:0] lane_acc = $signed(saved_acc_data[lane_index*32 +: 32]);
    wire signed [31:0] lane_bias = $signed(saved_bias_data[lane_index*32 +: 32]);
    wire signed [31:0] selected_lane_multiplier =
        $signed(saved_multiplier_data[lane_index*32 +: 32]);
    wire signed [SHIFT_WIDTH-1:0] selected_lane_shift =
        $signed(saved_shift_data[lane_index*SHIFT_WIDTH +: SHIFT_WIDTH]);
    wire signed [31:0] biased = lane_acc + lane_bias;
    wire signed [31:0] lane_pre_shifted = saturating_left_shift(
        biased, (selected_lane_shift > 0) ? selected_lane_shift : 0);
    wire signed [63:0] lane_product = lane_shifted_reg * lane_multiplier_reg;
    wire lane_highmul_overflow = (lane_shifted_reg == INT32_MIN_VALUE) &&
                                  (lane_multiplier_reg == INT32_MIN_VALUE);
    wire signed [31:0] lane_scaled = rounding_divide_by_pot(
        lane_high_product_reg, (lane_shift_reg < 0) ? -lane_shift_reg : 0);
    wire signed [32:0] offset_value =
        $signed({lane_scaled[31], lane_scaled}) +
        $signed({saved_output_offset[31], saved_output_offset});
    wire signed [32:0] min_extended = {{25{saved_activation_min[7]}}, saved_activation_min};
    wire signed [32:0] max_extended = {{25{saved_activation_max[7]}}, saved_activation_max};
    reg signed [7:0] lane_result;
    always @* begin
        if (!saved_lane_mask_reg)
            lane_result = 8'sd0;
        else if (offset_value < min_extended)
            lane_result = saved_activation_min;
        else if (offset_value > max_extended)
            lane_result = saved_activation_max;
        else
            lane_result = offset_value[7:0];
    end

    always @(posedge clk) begin
        if (rst) begin
            out_data <= {LANES*8{1'b0}};
            out_valid <= 1'b0;
            busy <= 1'b0;
            lane_index <= {INDEX_WIDTH{1'b0}};
            lane_phase <= 2'd0;
            saved_lane_mask <= {LANES{1'b0}};
            saved_acc_data <= {LANES*32{1'b0}};
            saved_bias_data <= {LANES*32{1'b0}};
            saved_multiplier_data <= {LANES*32{1'b0}};
            saved_shift_data <= {LANES*SHIFT_WIDTH{1'b0}};
            saved_output_offset <= 32'sd0;
            saved_activation_min <= 8'sd0;
            saved_activation_max <= 8'sd0;
            saved_lane_mask_reg <= 1'b0;
            lane_shifted_reg <= 32'sd0;
            lane_multiplier_reg <= 32'sd0;
            lane_high_product_reg <= 32'sd0;
            lane_product_reg <= 64'sd0;
            lane_highmul_overflow_reg <= 1'b0;
            lane_shift_reg <= '0;
        end else begin
            if (out_valid && out_ready)
                out_valid <= 1'b0;
            if (input_fire) begin
                busy <= 1'b1;
                lane_index <= {INDEX_WIDTH{1'b0}};
                lane_phase <= 2'd0;
                saved_lane_mask <= lane_mask;
                saved_acc_data <= acc_data;
                saved_bias_data <= bias_data;
                saved_multiplier_data <= multiplier_data;
                saved_shift_data <= shift_data;
                saved_output_offset <= output_offset;
                saved_activation_min <= activation_min;
                saved_activation_max <= activation_max;
            end else if (busy) begin
                case (lane_phase)
                    2'd0: begin
                        lane_shifted_reg <= lane_pre_shifted;
                        lane_multiplier_reg <= selected_lane_multiplier;
                        lane_shift_reg <= selected_lane_shift;
                        saved_lane_mask_reg <= saved_lane_mask[lane_index];
                        lane_phase <= 2'd1;
                    end
                    2'd1: begin
                        lane_product_reg <= lane_product;
                        lane_highmul_overflow_reg <= lane_highmul_overflow;
                        lane_phase <= 2'd2;
                    end
                    2'd2: begin
                        lane_high_product_reg <= saturating_rounding_high_mul_product(
                            lane_product_reg, lane_highmul_overflow_reg);
                        lane_phase <= 2'd3;
                    end
                    default: begin
                        out_data[lane_index*8 +: 8] <= lane_result;
                        lane_phase <= 2'd0;
                        if (lane_index == LANES-1) begin
                            busy <= 1'b0;
                            out_valid <= 1'b1;
                        end else begin
                            lane_index <= lane_index + 1'b1;
                        end
                    end
                endcase
            end
        end
    end
endmodule
