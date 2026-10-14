`timescale 1ns/1ps
// Weight-stationary array for the fused TinyCNN-8 core.
//
// Each PE holds two weight registers. Every activation row carries a one-bit
// bank select that travels with the operand, so a new weight group can be
// loaded into the idle bank while the previous group is still in flight and
// the switch takes effect exactly when the first operand of the new group
// reaches each PE. Partial sums enter as zero; accumulation across K groups
// is done by the caller on the output side, so issues never wait on results.
//
// The array always advances. An issue at edge t is retired with out_valid
// LATENCY edges later, with all columns aligned to that issue.
module fused_ws_array #(
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8,
    parameter ACC_WIDTH = 32,
    parameter TAG_WIDTH = 1
) (
    input  wire                             clk,
    input  wire                             rst,
    // Weight load: one row per cycle into the selected bank.
    input  wire                             weight_we,
    input  wire                             weight_bank,
    input  wire [$clog2(ARRAY_ROWS)-1:0]    weight_row,
    input  wire [ARRAY_COLS*8-1:0]          weight_data,
    // Issue: one ARRAY_ROWS-element activation vector per cycle.
    input  wire                             in_valid,
    input  wire                             in_bank,
    input  wire [ARRAY_ROWS*8-1:0]          in_act,
    input  wire [TAG_WIDTH-1:0]             in_tag,
    output wire                             out_valid,
    output wire                             out_bank,
    output wire [TAG_WIDTH-1:0]             out_tag,
    output wire [ARRAY_COLS*ACC_WIDTH-1:0]  out_psum
);
    localparam LATENCY = ARRAY_ROWS + ARRAY_COLS - 1;

    reg [LATENCY-1:0] valid_pipe;
    reg [LATENCY-1:0] bank_pipe;
    reg [TAG_WIDTH-1:0] tag_pipe [0:LATENCY-1];
    integer t;
    always @(posedge clk) begin
        if (rst) begin
            valid_pipe <= {LATENCY{1'b0}};
            bank_pipe <= {LATENCY{1'b0}};
            for (t = 0; t < LATENCY; t = t + 1)
                tag_pipe[t] <= {TAG_WIDTH{1'b0}};
        end else begin
            valid_pipe <= {valid_pipe[LATENCY-2:0], in_valid};
            bank_pipe <= {bank_pipe[LATENCY-2:0], in_bank};
            tag_pipe[0] <= in_tag;
            for (t = 1; t < LATENCY; t = t + 1)
                tag_pipe[t] <= tag_pipe[t-1];
        end
    end
    assign out_valid = valid_pipe[LATENCY-1];
    assign out_bank = bank_pipe[LATENCY-1];
    assign out_tag = tag_pipe[LATENCY-1];

    // Row r is delayed r clocks so that its operand meets the partial sum
    // travelling down from row r-1. The bank select is skewed identically.
    wire [7:0] row_act [0:ARRAY_ROWS-1];
    wire row_sel [0:ARRAY_ROWS-1];
    genvar r, c;
    generate
        for (r = 0; r < ARRAY_ROWS; r = r + 1) begin : skew
            if (r == 0) begin : direct
                assign row_act[r] = in_act[7:0];
                assign row_sel[r] = in_bank;
            end else begin : delayed
                reg [7:0] act_delay [0:r-1];
                reg sel_delay [0:r-1];
                integer d;
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < r; d = d + 1) begin
                            act_delay[d] <= 8'd0;
                            sel_delay[d] <= 1'b0;
                        end
                    end else begin
                        act_delay[0] <= in_act[r*8 +: 8];
                        sel_delay[0] <= in_bank;
                        for (d = 1; d < r; d = d + 1) begin
                            act_delay[d] <= act_delay[d-1];
                            sel_delay[d] <= sel_delay[d-1];
                        end
                    end
                end
                assign row_act[r] = act_delay[r-1];
                assign row_sel[r] = sel_delay[r-1];
            end
        end
    endgenerate

    wire [7:0] act_link [0:ARRAY_ROWS-1][0:ARRAY_COLS];
    wire sel_link [0:ARRAY_ROWS-1][0:ARRAY_COLS];
    wire [ACC_WIDTH-1:0] psum_link [0:ARRAY_ROWS][0:ARRAY_COLS-1];
    generate
        for (r = 0; r < ARRAY_ROWS; r = r + 1) begin : rows
            assign act_link[r][0] = row_act[r];
            assign sel_link[r][0] = row_sel[r];
            for (c = 0; c < ARRAY_COLS; c = c + 1) begin : cols
                reg signed [7:0] weight_bank0, weight_bank1;
                reg [7:0] act_q;
                reg sel_q;
                reg [ACC_WIDTH-1:0] psum_q;
                wire signed [7:0] weight_sel = sel_link[r][c] ? weight_bank1 : weight_bank0;
                wire signed [15:0] product = $signed(act_link[r][c]) * weight_sel;
                (* use_dsp = "yes" *) wire signed [ACC_WIDTH-1:0] mac =
                    $signed(psum_link[r][c]) + product;
                always @(posedge clk) begin
                    if (rst) begin
                        weight_bank0 <= 8'sd0;
                        weight_bank1 <= 8'sd0;
                        act_q <= 8'd0;
                        sel_q <= 1'b0;
                        psum_q <= {ACC_WIDTH{1'b0}};
                    end else begin
                        if (weight_we && weight_row == r && !weight_bank)
                            weight_bank0 <= weight_data[c*8 +: 8];
                        if (weight_we && weight_row == r && weight_bank)
                            weight_bank1 <= weight_data[c*8 +: 8];
                        act_q <= act_link[r][c];
                        sel_q <= sel_link[r][c];
                        psum_q <= mac;
                    end
                end
                assign act_link[r][c+1] = act_q;
                assign sel_link[r][c+1] = sel_q;
                assign psum_link[r+1][c] = psum_q;
            end
        end
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : top_psum
            assign psum_link[0][c] = {ACC_WIDTH{1'b0}};
        end
        // Column c leaves the bottom c clocks after column 0. Delay early
        // columns so every output lane refers to the same issue.
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : deskew
            if (c == ARRAY_COLS-1) begin : direct
                assign out_psum[c*ACC_WIDTH +: ACC_WIDTH] = psum_link[ARRAY_ROWS][c];
            end else begin : delayed
                localparam DEPTH = ARRAY_COLS-1-c;
                reg [ACC_WIDTH-1:0] delay [0:DEPTH-1];
                integer d;
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < DEPTH; d = d + 1)
                            delay[d] <= {ACC_WIDTH{1'b0}};
                    end else begin
                        delay[0] <= psum_link[ARRAY_ROWS][c];
                        for (d = 1; d < DEPTH; d = d + 1)
                            delay[d] <= delay[d-1];
                    end
                end
                assign out_psum[c*ACC_WIDTH +: ACC_WIDTH] = delay[DEPTH-1];
            end
        end
    endgenerate
endmodule
