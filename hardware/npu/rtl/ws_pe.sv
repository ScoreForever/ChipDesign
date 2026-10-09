`timescale 1ns/1ps
// One weight-stationary processing element. All data registers share ce.
module ws_pe #(
    parameter ACT_WIDTH = 8,
    parameter WGT_WIDTH = 8,
    parameter ACC_WIDTH = 32
) (
    input  wire                     clk,
    input  wire                     rst,
    input  wire                     ce,
    input  wire                     weight_we,
    input  wire [WGT_WIDTH-1:0]     weight_in,
    input  wire [ACT_WIDTH-1:0]     act_in,
    input  wire [ACC_WIDTH-1:0]     psum_in,
    output reg  [ACT_WIDTH-1:0]     act_out,
    output reg  [ACC_WIDTH-1:0]     psum_out
);
    localparam PRODUCT_WIDTH = ACT_WIDTH + WGT_WIDTH;

    reg signed [WGT_WIDTH-1:0] weight_reg;
    wire signed [ACT_WIDTH-1:0] signed_act = $signed(act_in);
    wire signed [PRODUCT_WIDTH-1:0] product = signed_act * weight_reg;
    wire signed [ACC_WIDTH-1:0] extended_product = product;
    wire signed [ACC_WIDTH-1:0] signed_psum = $signed(psum_in);
    // Keep the MAC in a DSP48 instead of expanding each 8x8 multiply and
    // 32-bit accumulation into LUT carry chains on the Artix-7 target.
    (* use_dsp = "yes" *) wire signed [ACC_WIDTH-1:0] mac_result =
        signed_psum + extended_product;

    always @(posedge clk) begin
        if (rst) begin
            weight_reg <= {WGT_WIDTH{1'b0}};
            act_out    <= {ACT_WIDTH{1'b0}};
            psum_out   <= {ACC_WIDTH{1'b0}};
        end else begin
            if (weight_we)
                weight_reg <= $signed(weight_in);
            if (ce) begin
                act_out  <= act_in;
                psum_out <= mac_result;
            end
        end
    end
endmodule
