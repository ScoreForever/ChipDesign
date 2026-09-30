`timescale 1ns/1ps
module tb_conv_window_addr_gen;
    localparam DIM_WIDTH = 8, CHANNEL_WIDTH = 8, ADDR_WIDTH = 16;
    reg clk = 0, rst = 1;
    always #5 clk = ~clk;

    reg start_valid = 0;
    wire start_ready;
    reg [DIM_WIDTH-1:0] input_height, input_width, output_height, output_width;
    reg [CHANNEL_WIDTH-1:0] input_channels;
    reg [DIM_WIDTH-1:0] kernel_height, kernel_width;
    reg [DIM_WIDTH-1:0] stride_height, stride_width, pad_top, pad_left;
    wire busy, done, item_valid;
    reg item_ready = 1;
    wire [ADDR_WIDTH-1:0] activation_addr;
    wire is_padding;
    wire [DIM_WIDTH-1:0] out_y, out_x, kernel_y, kernel_x;
    wire [CHANNEL_WIDTH-1:0] input_channel;
    wire first_in_output, last_in_output, first_in_layer, last_in_layer;

    conv_window_addr_gen dut (
        .clk(clk), .rst(rst), .start_valid(start_valid),
        .start_ready(start_ready), .input_height(input_height),
        .input_width(input_width), .input_channels(input_channels),
        .output_height(output_height), .output_width(output_width),
        .kernel_height(kernel_height), .kernel_width(kernel_width),
        .stride_height(stride_height), .stride_width(stride_width),
        .pad_top(pad_top), .pad_left(pad_left), .busy(busy), .done(done),
        .item_valid(item_valid), .item_ready(item_ready),
        .activation_addr(activation_addr), .is_padding(is_padding),
        .out_y(out_y), .out_x(out_x), .kernel_y(kernel_y),
        .kernel_x(kernel_x), .input_channel(input_channel),
        .first_in_output(first_in_output), .last_in_output(last_in_output),
        .first_in_layer(first_in_layer), .last_in_layer(last_in_layer)
    );

    integer expected_count, accepted_count, padding_count, stall_count;
    integer expected_y, expected_x, expected_ky, expected_kx, expected_c;
    integer input_y, input_x, expected_addr;
    reg held_padding;
    reg [ADDR_WIDTH-1:0] held_addr;
    reg [DIM_WIDTH*4+CHANNEL_WIDTH-1:0] held_coords;
    reg was_stalled;

    always @(posedge clk) begin
        if (rst) begin
            accepted_count = 0;
            padding_count = 0;
            stall_count = 0;
        end else begin
            was_stalled = item_valid && !item_ready;
            if (was_stalled) begin
                held_padding = is_padding;
                held_addr = activation_addr;
                held_coords = {out_y, out_x, kernel_y, kernel_x, input_channel};
                stall_count = stall_count + 1;
            end
            if (item_valid && item_ready) begin
                expected_c = accepted_count % input_channels;
                expected_kx = (accepted_count / input_channels) % kernel_width;
                expected_ky = (accepted_count / (input_channels*kernel_width)) % kernel_height;
                expected_x = (accepted_count /
                    (input_channels*kernel_width*kernel_height)) % output_width;
                expected_y = accepted_count /
                    (input_channels*kernel_width*kernel_height*output_width);
                if (out_y !== expected_y || out_x !== expected_x ||
                    kernel_y !== expected_ky || kernel_x !== expected_kx ||
                    input_channel !== expected_c)
                    $fatal(1, "coordinate mismatch item=%0d", accepted_count);
                input_y = expected_y*stride_height + expected_ky - pad_top;
                input_x = expected_x*stride_width + expected_kx - pad_left;
                if (input_y < 0 || input_x < 0 ||
                    input_y >= input_height || input_x >= input_width) begin
                    if (!is_padding || activation_addr !== 0)
                        $fatal(1, "padding mismatch item=%0d", accepted_count);
                    padding_count = padding_count + 1;
                end else begin
                    expected_addr = ((input_y*input_width)+input_x)*input_channels + expected_c;
                    if (is_padding || activation_addr !== expected_addr)
                        $fatal(1, "address mismatch item=%0d expected=%0d actual=%0d",
                               accepted_count, expected_addr, activation_addr);
                end
                if (first_in_output !==
                    (expected_ky == 0 && expected_kx == 0 && expected_c == 0))
                    $fatal(1, "first_in_output mismatch");
                if (last_in_output !==
                    (expected_ky == kernel_height-1 && expected_kx == kernel_width-1 &&
                     expected_c == input_channels-1))
                    $fatal(1, "last_in_output mismatch");
                if (first_in_layer !== (accepted_count == 0))
                    $fatal(1, "first_in_layer mismatch");
                if (last_in_layer !== (accepted_count == expected_count-1))
                    $fatal(1, "last_in_layer mismatch");
                accepted_count = accepted_count + 1;
            end
            #1;
            if (was_stalled && (!item_valid || is_padding !== held_padding ||
                activation_addr !== held_addr ||
                {out_y, out_x, kernel_y, kernel_x, input_channel} !== held_coords))
                $fatal(1, "stream changed during backpressure");
        end
    end

    task start_layer;
        input integer ih, iw, ic, oh, ow, kh, kw, sh, sw, pt, pl;
        begin
            @(negedge clk);
            input_height = ih; input_width = iw; input_channels = ic;
            output_height = oh; output_width = ow;
            kernel_height = kh; kernel_width = kw;
            stride_height = sh; stride_width = sw;
            pad_top = pt; pad_left = pl;
            expected_count = oh*ow*kh*kw*ic;
            accepted_count = 0; padding_count = 0; stall_count = 0;
            start_valid = 1;
            @(posedge clk); while (!start_ready) @(posedge clk);
            @(negedge clk); start_valid = 0;
        end
    endtask

    task wait_done;
        integer cycles;
        begin
            cycles = 0;
            while (!done) begin
                @(negedge clk);
                item_ready = ($urandom_range(0, 4) != 0);
                @(posedge clk);
                cycles = cycles + 1;
                if (cycles > expected_count*3+20) $fatal(1, "timeout");
            end
            @(negedge clk); item_ready = 1;
            if (accepted_count != expected_count)
                $fatal(1, "count expected=%0d actual=%0d", expected_count, accepted_count);
            if (busy) $fatal(1, "busy remained high after done");
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0;

        start_layer(20, 16, 1, 20, 16, 3, 3, 1, 1, 1, 1);
        wait_done();
        if (padding_count != 212) $fatal(1, "Conv1 padding count=%0d", padding_count);
        if (stall_count == 0) $fatal(1, "Conv1 missing stalls");
        $display("PASS TinyCNN Conv1 20x16x1 SAME address stream");

        start_layer(10, 8, 8, 10, 8, 3, 3, 1, 1, 1, 1);
        wait_done();
        if (padding_count != 832) $fatal(1, "Conv2 padding count=%0d", padding_count);
        $display("PASS TinyCNN Conv2 10x8x8 SAME address stream");

        // Also cover non-SAME stride and multi-channel traversal.
        start_layer(7, 6, 3, 3, 2, 3, 3, 2, 2, 0, 0);
        wait_done();
        if (padding_count != 0) $fatal(1, "unexpected VALID padding");
        $display("PASS strided VALID multi-channel address stream");

        // An invalid descriptor must not begin a stream.
        start_layer(4, 4, 0, 4, 4, 3, 3, 1, 1, 1, 1);
        repeat (3) @(posedge clk);
        if (busy || item_valid || done) $fatal(1, "invalid descriptor accepted");
        $display("PASS zero-dimension descriptor rejection");
        $display("ALL CONV WINDOW ADDRESS TESTS PASSED");
        $finish;
    end
endmodule
