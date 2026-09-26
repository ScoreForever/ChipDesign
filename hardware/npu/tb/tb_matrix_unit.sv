`timescale 1ns/1ps
module tb_matrix_unit #(
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8,
    parameter ACT_WIDTH = 8,
    parameter WGT_WIDTH = 8,
    parameter ACC_WIDTH = 32
);
    localparam MAX_TX = 1200;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg weight_start_valid = 0;
    wire weight_start_ready;
    reg weight_valid = 0;
    wire weight_ready;
    reg [ARRAY_COLS*WGT_WIDTH-1:0] weight_data = 0;
    wire weights_loaded;
    wire idle;
    reg in_valid = 0;
    wire in_ready;
    reg [ARRAY_ROWS*ACT_WIDTH-1:0] in_act_data = 0;
    reg [ARRAY_COLS*ACC_WIDTH-1:0] in_psum_data = 0;
    wire out_valid;
    reg out_ready = 1;
    wire [ARRAY_COLS*ACC_WIDTH-1:0] out_psum_data;

    matrix_unit #(
        .ACT_WIDTH(ACT_WIDTH), .WGT_WIDTH(WGT_WIDTH), .ACC_WIDTH(ACC_WIDTH),
        .ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS)
    ) dut (.*);

    reg signed [WGT_WIDTH-1:0] model_w [0:ARRAY_ROWS-1][0:ARRAY_COLS-1];
    reg [ACC_WIDTH-1:0] expected [0:MAX_TX-1][0:ARRAY_COLS-1];
    integer pushed = 0;
    integer popped = 0;
    integer cycle = 0;
    integer continuous_run = 0;
    integer max_continuous_run = 0;
    integer stall_cycles = 0;
    integer bubble_cycles = 0;
    reg throughput_phase = 0;
    reg [ARRAY_COLS*ACC_WIDTH-1:0] stalled_data;
    reg stalled = 0;
    integer c;
    integer r;
    longint signed sum;
    integer signed act_value;
    integer signed psum_value;

    // Scoreboard observes only completed ready/valid handshakes. The expected
    // values are truncated to the DUT accumulator width before comparison.
    always @(posedge clk) begin
        if (!rst) begin
            cycle = cycle + 1;
            if (cycle > 30000)
                $fatal(1, "Matrix Unit timeout R=%0d C=%0d", ARRAY_ROWS, ARRAY_COLS);
            if (stalled && (!out_valid || out_psum_data !== stalled_data))
                $fatal(1, "Output changed while stalled at cycle %0d", cycle);
            stalled = out_valid && !out_ready;
            if (stalled) begin
                stall_cycles = stall_cycles + 1;
                stalled_data = out_psum_data;
                if (in_ready)
                    $fatal(1, "in_ready high during output stall");
            end
            if (!in_valid && in_ready)
                bubble_cycles = bubble_cycles + 1;
            if (in_valid && in_ready) begin
                if (pushed >= MAX_TX)
                    $fatal(1, "Scoreboard overflow");
                for (c = 0; c < ARRAY_COLS; c = c + 1) begin
                    psum_value = $signed(in_psum_data[c*ACC_WIDTH +: ACC_WIDTH]);
                    sum = psum_value;
                    for (r = 0; r < ARRAY_ROWS; r = r + 1) begin
                        act_value = $signed(in_act_data[r*ACT_WIDTH +: ACT_WIDTH]);
                        sum = sum + act_value * $signed(model_w[r][c]);
                    end
                    expected[pushed][c] = sum;
                end
                pushed = pushed + 1;
            end
            if (out_valid && out_ready) begin
                if (popped >= pushed)
                    $fatal(1, "Unexpected or duplicate output at cycle %0d", cycle);
                for (c = 0; c < ARRAY_COLS; c = c + 1)
                    if (out_psum_data[c*ACC_WIDTH +: ACC_WIDTH] !== expected[popped][c])
                        $fatal(1, "R=%0d C=%0d tx=%0d col=%0d got=%h expected=%h",
                               ARRAY_ROWS, ARRAY_COLS, popped, c,
                               out_psum_data[c*ACC_WIDTH +: ACC_WIDTH], expected[popped][c]);
                popped = popped + 1;
                if (throughput_phase) begin
                    continuous_run = continuous_run + 1;
                    if (continuous_run > max_continuous_run)
                        max_continuous_run = continuous_run;
                end
            end else if (throughput_phase && pushed > popped)
                continuous_run = 0;
        end
    end

    task load_tile;
        input integer tile;
        integer rr, cc, value;
        begin
            wait (idle);
            @(negedge clk);
            if (!weight_start_ready)
                $fatal(1, "Weight start not ready while idle");
            weight_start_valid = 1;
            @(posedge clk);
            @(negedge clk);
            weight_start_valid = 0;
            if (weights_loaded || !weight_ready || in_ready)
                $fatal(1, "Incorrect weight-loading state");
            for (rr = 0; rr < ARRAY_ROWS; rr = rr + 1) begin
                weight_valid = 1;
                for (cc = 0; cc < ARRAY_COLS; cc = cc + 1) begin
                    case (tile)
                        0: value = ((rr*17 + cc*11) % 19) - 9;
                        1: value = ((rr*31 - cc*13 + 128) % 37) - 18;
                        default: value = 0;
                    endcase
                    if (tile == 0 && rr == 0 && cc == 0) value = -128;
                    if (tile == 0 && rr == ARRAY_ROWS-1 && cc == ARRAY_COLS-1) value = 127;
                    model_w[rr][cc] = value;
                    weight_data[cc*WGT_WIDTH +: WGT_WIDTH] = value;
                end
                if (!weight_ready)
                    $fatal(1, "Weight row %0d not ready", rr);
                @(posedge clk);
                @(negedge clk);
            end
            weight_valid = 0;
            if (!weights_loaded || !in_ready)
                $fatal(1, "Weights not available after last row");
        end
    endtask

    task make_input;
        input integer index;
        input integer kind;
        integer rr, cc, value;
        begin
            for (rr = 0; rr < ARRAY_ROWS; rr = rr + 1) begin
                if (kind == 0) begin
                    case (index)
                        0: value = 0;
                        1: value = (rr % 2) ? 127 : -128;
                        default: value = index*rr - 7;
                    endcase
                end else if (kind == 2)
                    value = 0;
                else
                    value = $urandom;
                in_act_data[rr*ACT_WIDTH +: ACT_WIDTH] = value;
            end
            for (cc = 0; cc < ARRAY_COLS; cc = cc + 1) begin
                if (kind == 0)
                    value = (index == 0) ? (cc*12345 - 50000) : (index*31 - cc*73);
                else if (kind == 2)
                    value = 1000 + cc*13;
                else
                    value = $urandom;
                in_psum_data[cc*ACC_WIDTH +: ACC_WIDTH] = value;
            end
        end
    endtask

    task send_stream;
        input integer count;
        input integer kind;
        input integer random_bubbles;
        input integer random_stalls;
        integer sent;
        reg pending;
        begin
            sent = 0;
            pending = 0;
            while (sent < count) begin
                @(negedge clk);
                if (random_stalls)
                    out_ready = ($urandom_range(0, 3) != 0);
                else
                    out_ready = 1;
                if (!pending) begin
                    in_valid = !random_bubbles || ($urandom_range(0, 3) != 0);
                    if (in_valid) begin
                        make_input(sent, kind);
                        pending = 1;
                    end
                end
                @(posedge clk);
                if (in_valid && in_ready) begin
                    sent = sent + 1;
                    pending = 0;
                end
            end
            @(negedge clk);
            in_valid = 0;
            out_ready = 1;
        end
    endtask

    task drain;
        begin
            wait (idle);
            @(negedge clk);
            if (pushed != popped)
                $fatal(1, "Drain mismatch pushed=%0d popped=%0d", pushed, popped);
        end
    endtask

    initial begin
`ifdef DUMP_VCD
        $dumpfile("matrix_unit.vcd");
        $dumpvars(0, tb_matrix_unit);
`endif
        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 0;
        if (in_ready || weights_loaded)
            $fatal(1, "Accepted compute before weights were loaded");

        load_tile(0);
        send_stream(4, 0, 0, 0);
        drain();
        $display("PASS deterministic R=%0d C=%0d", ARRAY_ROWS, ARRAY_COLS);

        throughput_phase = 1;
        send_stream(48, 1, 0, 0);
        drain();
        throughput_phase = 0;
        if (max_continuous_run < 16)
            $fatal(1, "Throughput test observed only %0d consecutive outputs", max_continuous_run);
        $display("PASS continuous throughput R=%0d C=%0d max_run=%0d",
                 ARRAY_ROWS, ARRAY_COLS, max_continuous_run);

        send_stream(350, 1, 1, 1);
        drain();
        if (stall_cycles == 0 || bubble_cycles == 0)
            $fatal(1, "Random phase missed a stall or input bubble");
        $display("PASS random bubbles/backpressure R=%0d C=%0d", ARRAY_ROWS, ARRAY_COLS);

        load_tile(1);
        send_stream(120, 1, 1, 1);
        drain();
        $display("PASS weight reload R=%0d C=%0d", ARRAY_ROWS, ARRAY_COLS);

        load_tile(2);
        send_stream(8, 2, 0, 0);
        drain();
        $display("PASS zero weights/activation with nonzero psum R=%0d C=%0d",
                 ARRAY_ROWS, ARRAY_COLS);
        $display("PASS tb_matrix_unit R=%0d C=%0d transactions=%0d",
                 ARRAY_ROWS, ARRAY_COLS, popped);
        $finish;
    end
endmodule
