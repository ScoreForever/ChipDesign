`timescale 1ns/1ps
module tb_requant_unit;
    parameter LANES = 8;
    localparam SHIFT_WIDTH = 6;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg in_valid = 0;
    wire in_ready;
    reg [LANES-1:0] lane_mask = 0;
    reg [LANES*32-1:0] acc_data = 0, bias_data = 0, multiplier_data = 0;
    reg [LANES*SHIFT_WIDTH-1:0] shift_data = 0;
    reg signed [31:0] output_offset = 0;
    reg signed [7:0] activation_min = -128, activation_max = 127;
    wire [LANES*8-1:0] out_data;
    wire out_valid;
    reg out_ready = 1;

    requant_unit #(.LANES(LANES), .SHIFT_WIDTH(SHIFT_WIDTH)) dut (
        .clk(clk), .rst(rst), .in_valid(in_valid), .in_ready(in_ready),
        .lane_mask(lane_mask), .acc_data(acc_data), .bias_data(bias_data),
        .multiplier_data(multiplier_data), .shift_data(shift_data),
        .output_offset(output_offset), .activation_min(activation_min),
        .activation_max(activation_max), .out_data(out_data),
        .out_valid(out_valid), .out_ready(out_ready)
    );

    function automatic signed [31:0] ref_high_mul;
        input signed [31:0] a, b;
        reg signed [63:0] product, nudge;
        begin
            if (a == -32'sd2147483648 && b == -32'sd2147483648)
                ref_high_mul = 32'sh7fffffff;
            else begin
                product = a * b;
                nudge = product >= 0 ? 64'sd1073741824 : -64'sd1073741823;
                ref_high_mul = (product + nudge) / 64'sd2147483648;
            end
        end
    endfunction

    function automatic signed [31:0] ref_div_pot;
        input signed [31:0] value;
        input integer exponent;
        reg [31:0] mask, remainder, threshold;
        reg signed [31:0] base;
        begin
            if (exponent == 0)
                ref_div_pot = value;
            else begin
                mask = (32'h1 << exponent) - 1;
                remainder = $unsigned(value) & mask;
                threshold = (mask >> 1) + (value < 0);
                base = value >>> exponent;
                ref_div_pot = base + (remainder > threshold);
            end
        end
    endfunction

    function automatic signed [31:0] ref_left_shift;
        input signed [31:0] value;
        input integer amount;
        reg signed [63:0] wide;
        begin
            wide = value;
            wide = wide <<< amount;
            if (wide > 64'sh7fffffff) ref_left_shift = 32'sh7fffffff;
            else if (wide < -64'sd2147483648) ref_left_shift = 32'sh80000000;
            else ref_left_shift = wide;
        end
    endfunction

    function automatic signed [31:0] ref_requant;
        input signed [31:0] value, multiplier;
        input integer shift;
        reg signed [31:0] shifted, high;
        begin
            shifted = ref_left_shift(value, shift > 0 ? shift : 0);
            high = ref_high_mul(shifted, multiplier);
            ref_requant = ref_div_pot(high, shift < 0 ? -shift : 0);
        end
    endfunction

    integer expected [0:LANES-1];
    integer accepted = 0, consumed = 0, stalls = 0;
    reg pending = 0;
    reg [LANES*8-1:0] held_output;
    integer lane, value, shift_value, test_index;

    always @(posedge clk) begin
        if (rst) begin
            pending = 0;
        end else begin
            if (out_valid !== pending) $fatal(1, "out_valid mismatch");
            if (out_valid && !out_ready) begin
                stalls = stalls + 1;
                held_output = out_data;
                #1;
                if (!out_valid || out_data !== held_output)
                    $fatal(1, "output changed under backpressure");
            end
            if (out_valid && out_ready) begin
                for (lane = 0; lane < LANES; lane = lane + 1)
                    if ($signed(out_data[lane*8 +: 8]) !== expected[lane])
                        $fatal(1, "lane=%0d acc=%0d bias=%0d mult=%0d shift=%0d expected=%0d actual=%0d",
                               lane, $signed(acc_data[lane*32 +: 32]),
                               $signed(bias_data[lane*32 +: 32]),
                               $signed(multiplier_data[lane*32 +: 32]),
                               $signed(shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH]),
                               expected[lane], $signed(out_data[lane*8 +: 8]));
                consumed = consumed + 1;
                pending = 0;
            end
            if (in_valid && in_ready) begin
                if (pending) $fatal(1, "accepted input over pending output");
                accepted = accepted + 1;
                pending = 1;
                for (lane = 0; lane < LANES; lane = lane + 1) begin
                    value = $signed(acc_data[lane*32 +: 32]) +
                            $signed(bias_data[lane*32 +: 32]);
                    shift_value = $signed(shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH]);
                    value = ref_requant(value,
                        $signed(multiplier_data[lane*32 +: 32]), shift_value);
                    value = value + output_offset;
                    if (!lane_mask[lane]) value = 0;
                    else if (value < activation_min) value = activation_min;
                    else if (value > activation_max) value = activation_max;
                    expected[lane] = value;
                end
            end
            #1;
            if (out_valid !== pending) $fatal(1, "registered valid mismatch");
        end
    end

    task send_current;
        begin
            @(negedge clk); in_valid = 1;
            @(posedge clk); while (!in_ready) @(posedge clk);
            @(negedge clk); in_valid = 0;
        end
    endtask

    task drain;
        begin
            @(negedge clk); in_valid = 0; out_ready = 1;
            @(posedge clk); @(negedge clk);
            if (out_valid || pending) $fatal(1, "drain failed");
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0;

        lane_mask = {LANES{1'b1}};
        for (lane = 0; lane < LANES; lane = lane + 1) begin
            acc_data[lane*32 +: 32] = (lane-4) * 100000;
            bias_data[lane*32 +: 32] = lane * 37 - 91;
            multiplier_data[lane*32 +: 32] = 32'sh40000000 + lane*12345;
            shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH] = lane-5;
        end
        send_current();
        drain();
        $display("PASS directed signed multipliers/shifts");

        activation_min = 0; activation_max = 127; output_offset = 3;
        lane_mask = '1; lane_mask[LANES-1] = 0;
        for (lane = 0; lane < LANES; lane = lane + 1) begin
            acc_data[lane*32 +: 32] = (lane-3) * 32'sd100000000;
            bias_data[lane*32 +: 32] = 0;
            multiplier_data[lane*32 +: 32] = 32'sh60000000;
            shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH] = -2;
        end
        send_current();
        drain();
        $display("PASS ReLU clamp, offset, saturation, and mask");

        activation_min = -128; activation_max = 127;
        for (test_index = 0; test_index < 3000; test_index = test_index + 1) begin
            @(negedge clk);
            out_ready = ($urandom_range(0, 3) != 0);
            if (!pending || out_ready) begin
                in_valid = ($urandom_range(0, 3) != 0);
                if (in_valid) begin
                    output_offset = $urandom_range(0, 30) - 15;
                    for (lane = 0; lane < LANES; lane = lane + 1) begin
                        acc_data[lane*32 +: 32] = $urandom;
                        bias_data[lane*32 +: 32] = $urandom_range(0, 2000000) - 1000000;
                        multiplier_data[lane*32 +: 32] =
                            32'sh20000000 + $urandom_range(0, 32'h5fffffff);
                        shift_data[lane*SHIFT_WIDTH +: SHIFT_WIDTH] =
                            $urandom_range(0, 12) - 10;
                        lane_mask[lane] = $urandom;
                    end
                end
            end
            @(posedge clk);
        end
        drain();
        if (stalls == 0) $fatal(1, "missing stall coverage");
        $display("PASS randomized requantization and backpressure");
        $display("ALL REQUANT UNIT TESTS PASSED accepted=%0d consumed=%0d stalls=%0d",
                 accepted, consumed, stalls);
        $finish;
    end
endmodule
