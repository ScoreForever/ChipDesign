`timescale 1ns/1ps
module tb_reduction_sum_unit;
    parameter LANES = 8;
    localparam DATA_WIDTH = 8, ACC_WIDTH = 32;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;
    reg in_valid = 0, first = 0, last = 0;
    wire in_ready;
    reg [LANES-1:0] lane_mask = 0;
    reg [LANES*DATA_WIDTH-1:0] in_data = 0;
    wire [LANES*ACC_WIDTH-1:0] out_sum;
    wire out_valid;
    reg out_ready = 1;

    reduction_sum_unit #(.LANES(LANES)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_ready(in_ready),
        .first(first), .last(last), .lane_mask(lane_mask), .in_data(in_data),
        .out_sum(out_sum), .out_valid(out_valid), .out_ready(out_ready)
    );

    integer model [0:LANES-1], expected [0:LANES-1];
    integer lane, position, window, length, accepted = 0, outputs = 0, stalls = 0;
    reg pending_output = 0;
    reg was_stalled;
    reg [LANES*ACC_WIDTH-1:0] held_output;

    always @(posedge clk) begin
        if (rst) begin
            pending_output = 0;
            for (lane = 0; lane < LANES; lane = lane + 1) model[lane] = 0;
        end else begin
            if (out_valid !== pending_output) $fatal(1, "out_valid mismatch");
            was_stalled = out_valid && !out_ready;
            if (was_stalled) begin
                stalls = stalls + 1;
                held_output = out_sum;
            end
            if (out_valid && out_ready) begin
                for (lane = 0; lane < LANES; lane = lane + 1)
                    if ($signed(out_sum[lane*ACC_WIDTH +: ACC_WIDTH]) !== expected[lane])
                        $fatal(1, "lane=%0d expected=%0d actual=%0d", lane,
                               expected[lane],
                               $signed(out_sum[lane*ACC_WIDTH +: ACC_WIDTH]));
                pending_output = 0;
                outputs = outputs + 1;
            end
            if (in_valid && in_ready) begin
                accepted = accepted + 1;
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    if (first) model[lane] = 0;
                    if (lane_mask[lane])
                        model[lane] = model[lane] +
                            $signed(in_data[lane*DATA_WIDTH +: DATA_WIDTH]);
                    if (last) expected[lane] = model[lane];
                end
                if (last) pending_output = 1;
            end
            #1;
            if (out_valid !== pending_output) $fatal(1, "registered valid mismatch");
            if (was_stalled && (!out_valid || out_sum !== held_output))
                $fatal(1, "sum changed under backpressure");
        end
    end

    task send_vector;
        input first_value, last_value;
        begin
            @(negedge clk);
            in_valid = 1; first = first_value; last = last_value;
            @(posedge clk); while (!in_ready) @(posedge clk);
            @(negedge clk); in_valid = 0;
        end
    endtask

    task drain;
        begin
            @(negedge clk); in_valid = 0; out_ready = 1;
            @(posedge clk); @(negedge clk);
            if (out_valid || pending_output) $fatal(1, "drain failed");
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0;

        // TinyCNN-8 GAP: exactly twenty 8-channel spatial positions.
        lane_mask = {LANES{1'b1}};
        for (position = 0; position < 20; position = position + 1) begin
            for (lane = 0; lane < LANES; lane = lane + 1)
                in_data[lane*DATA_WIDTH +: DATA_WIDTH] =
                    ((position*31 + lane*17) % 256) - 128;
            send_vector(position == 0, position == 19);
        end
        drain();
        $display("PASS TinyCNN 5x4 global signed sum");

        // Multiple reductions, random lengths/masks and forced output stalls.
        for (window = 0; window < 200; window = window + 1) begin
            length = $urandom_range(1, 31);
            for (position = 0; position < length; position = position + 1) begin
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    in_data[lane*DATA_WIDTH +: DATA_WIDTH] = $urandom;
                    lane_mask[lane] = $urandom;
                end
                send_vector(position == 0, position == length-1);
            end
            drain();
        end

        // A one-vector reduction covers first and last together. Hold its
        // completed output blocked to verify stable state and backpressure.
        out_ready = 0;
        for (lane = 0; lane < LANES; lane = lane + 1) begin
            in_data[lane*DATA_WIDTH +: DATA_WIDTH] = $urandom;
            lane_mask[lane] = $urandom;
        end
        send_vector(1'b1, 1'b1);
        repeat (4) @(posedge clk);
        @(negedge clk); out_ready = 1;
        drain();
        if (stalls == 0) $fatal(1, "missing stall coverage");
        $display("PASS repeated reductions, masks, restart, and backpressure");

        @(negedge clk); rst = 1;
        repeat (2) @(posedge clk);
        @(negedge clk); rst = 0;
        lane_mask = '1; in_data = '1;
        send_vector(1'b1, 1'b1);
        drain();
        $display("PASS reset and clean restart");
        $display("ALL REDUCTION SUM TESTS PASSED accepted=%0d outputs=%0d stalls=%0d",
                 accepted, outputs, stalls);
        $finish;
    end
endmodule
