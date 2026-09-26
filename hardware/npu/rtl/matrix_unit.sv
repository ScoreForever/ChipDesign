`timescale 1ns/1ps
// One transaction computes P_out[c] = P_in[c] + sum_r A[r]*W[r][c].
// Lane 0 occupies the least-significant bits of each packed vector.
module matrix_unit #(
    parameter ACT_WIDTH = 8,
    parameter WGT_WIDTH = 8,
    parameter ACC_WIDTH = 32,
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8
) (
    input  wire                                 clk,
    input  wire                                 rst,
    input  wire                                 weight_start_valid,
    output wire                                 weight_start_ready,
    input  wire                                 weight_valid,
    output wire                                 weight_ready,
    input  wire [ARRAY_COLS*WGT_WIDTH-1:0]      weight_data,
    output reg                                  weights_loaded,
    output wire                                 idle,
    input  wire                                 in_valid,
    output wire                                 in_ready,
    input  wire [ARRAY_ROWS*ACT_WIDTH-1:0]      in_act_data,
    input  wire [ARRAY_COLS*ACC_WIDTH-1:0]      in_psum_data,
    output wire                                 out_valid,
    input  wire                                 out_ready,
    output wire [ARRAY_COLS*ACC_WIDTH-1:0]      out_psum_data
);
    // A transaction enters PE(0,0) at its acceptance edge. The last
    // column emerges after ROWS+COLS-2 further enabled edges.
    localparam LATENCY = ARRAY_ROWS + ARRAY_COLS - 1;
    localparam ROW_COUNT_WIDTH = $clog2(ARRAY_ROWS+1);

    reg loading;
    reg [ROW_COUNT_WIDTH-1:0] load_row;
    reg [LATENCY-1:0] valid_pipe;
    wire advance = !(out_valid && !out_ready);
    wire start_fire = weight_start_valid && weight_start_ready;
    wire weight_fire = weight_valid && weight_ready;
    wire input_fire = in_valid && in_ready;

    assign out_valid = valid_pipe[LATENCY-1];
    assign idle = !loading && !(|valid_pipe);
    assign weight_start_ready = idle && !rst;
    assign weight_ready = loading && !rst;
    assign in_ready = weights_loaded && !loading && advance && !start_fire && !rst;

    always @(posedge clk) begin
        if (rst) begin
            loading <= 1'b0;
            load_row <= {ROW_COUNT_WIDTH{1'b0}};
            weights_loaded <= 1'b0;
            valid_pipe <= {LATENCY{1'b0}};
        end else begin
            if (start_fire) begin
                loading <= 1'b1;
                load_row <= {ROW_COUNT_WIDTH{1'b0}};
                weights_loaded <= 1'b0;
            end else if (weight_fire) begin
                if (load_row == ARRAY_ROWS-1) begin
                    loading <= 1'b0;
                    weights_loaded <= 1'b1;
                end else begin
                    load_row <= load_row + 1'b1;
                end
            end
            if (advance)
                valid_pipe <= (valid_pipe << 1) | input_fire;
        end
    end

    wire [ARRAY_ROWS*ACT_WIDTH-1:0] skewed_act;
    wire [ARRAY_COLS*ACC_WIDTH-1:0] skewed_psum;
    wire [ARRAY_COLS*ACC_WIDTH-1:0] array_psum;

    genvar r, c;
    generate
        // Row r is delayed r enabled clocks, matching vertical psum travel.
        for (r = 0; r < ARRAY_ROWS; r = r + 1) begin : activation_skew
            if (r == 0) begin : direct
                assign skewed_act[r*ACT_WIDTH +: ACT_WIDTH] =
                    in_act_data[r*ACT_WIDTH +: ACT_WIDTH];
            end else begin : delayed
                reg [ACT_WIDTH-1:0] delay [0:r-1];
                integer d;
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < r; d = d + 1)
                            delay[d] <= {ACT_WIDTH{1'b0}};
                    end else if (advance) begin
                        delay[0] <= in_act_data[r*ACT_WIDTH +: ACT_WIDTH];
                        for (d = 1; d < r; d = d + 1)
                            delay[d] <= delay[d-1];
                    end
                end
                assign skewed_act[r*ACT_WIDTH +: ACT_WIDTH] = delay[r-1];
            end
        end
        // Column c is delayed c enabled clocks, matching horizontal act travel.
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : psum_skew
            if (c == 0) begin : direct
                assign skewed_psum[c*ACC_WIDTH +: ACC_WIDTH] =
                    in_psum_data[c*ACC_WIDTH +: ACC_WIDTH];
            end else begin : delayed
                reg [ACC_WIDTH-1:0] delay [0:c-1];
                integer d;
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < c; d = d + 1)
                            delay[d] <= {ACC_WIDTH{1'b0}};
                    end else if (advance) begin
                        delay[0] <= in_psum_data[c*ACC_WIDTH +: ACC_WIDTH];
                        for (d = 1; d < c; d = d + 1)
                            delay[d] <= delay[d-1];
                    end
                end
                assign skewed_psum[c*ACC_WIDTH +: ACC_WIDTH] = delay[c-1];
            end
        end
    endgenerate

    ws_systolic_array #(
        .ACT_WIDTH(ACT_WIDTH), .WGT_WIDTH(WGT_WIDTH), .ACC_WIDTH(ACC_WIDTH),
        .ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS)
    ) array_inst (
        .clk(clk), .rst(rst), .ce(advance),
        .weight_we(weight_fire), .weight_row(load_row), .weight_data(weight_data),
        .act_left(skewed_act), .psum_top(skewed_psum),
        .psum_bottom(array_psum)
    );

    generate
        // Bottom column c appears c clocks after column 0. Hold early
        // columns for COLS-1-c clocks so all output lanes refer to one input.
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : output_deskew
            if (c == ARRAY_COLS-1) begin : direct
                assign out_psum_data[c*ACC_WIDTH +: ACC_WIDTH] =
                    array_psum[c*ACC_WIDTH +: ACC_WIDTH];
            end else begin : delayed
                localparam DEPTH = ARRAY_COLS-1-c;
                reg [ACC_WIDTH-1:0] delay [0:DEPTH-1];
                integer d;
                always @(posedge clk) begin
                    if (rst) begin
                        for (d = 0; d < DEPTH; d = d + 1)
                            delay[d] <= {ACC_WIDTH{1'b0}};
                    end else if (advance) begin
                        delay[0] <= array_psum[c*ACC_WIDTH +: ACC_WIDTH];
                        for (d = 1; d < DEPTH; d = d + 1)
                            delay[d] <= delay[d-1];
                    end
                end
                assign out_psum_data[c*ACC_WIDTH +: ACC_WIDTH] = delay[DEPTH-1];
            end
        end
    endgenerate
endmodule
