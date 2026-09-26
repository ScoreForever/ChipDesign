`timescale 1ns/1ps
// Activations move right; partial sums move down. Input lanes are already skewed.
module ws_systolic_array #(
    parameter ACT_WIDTH = 8,
    parameter WGT_WIDTH = 8,
    parameter ACC_WIDTH = 32,
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8
) (
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 ce,
    input  wire                                 weight_we,
    input  wire [$clog2(ARRAY_ROWS+1)-1:0]      weight_row,
    input  wire [ARRAY_COLS*WGT_WIDTH-1:0]      weight_data,
    input  wire [ARRAY_ROWS*ACT_WIDTH-1:0]      act_left,
    input  wire [ARRAY_COLS*ACC_WIDTH-1:0]      psum_top,
    output wire [ARRAY_COLS*ACC_WIDTH-1:0]      psum_bottom
);
    wire [ACT_WIDTH-1:0] act_link [0:ARRAY_ROWS-1][0:ARRAY_COLS];
    wire [ACC_WIDTH-1:0] psum_link [0:ARRAY_ROWS][0:ARRAY_COLS-1];

    genvar r, c;
    generate
        for (r = 0; r < ARRAY_ROWS; r = r + 1) begin : rows
            assign act_link[r][0] = act_left[r*ACT_WIDTH +: ACT_WIDTH];
            for (c = 0; c < ARRAY_COLS; c = c + 1) begin : cols
                ws_pe #(
                    .ACT_WIDTH(ACT_WIDTH), .WGT_WIDTH(WGT_WIDTH),
                    .ACC_WIDTH(ACC_WIDTH)
                ) pe (
                    .clk(clk), .rst(rst), .ce(ce),
                    .weight_we(weight_we && (weight_row == r)),
                    .weight_in(weight_data[c*WGT_WIDTH +: WGT_WIDTH]),
                    .act_in(act_link[r][c]), .act_out(act_link[r][c+1]),
                    .psum_in(psum_link[r][c]), .psum_out(psum_link[r+1][c])
                );
            end
        end
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : columns
            assign psum_link[0][c] = psum_top[c*ACC_WIDTH +: ACC_WIDTH];
            assign psum_bottom[c*ACC_WIDTH +: ACC_WIDTH] = psum_link[ARRAY_ROWS][c];
        end
    endgenerate
endmodule
