`timescale 1ns/1ps
module tb_conv2d_engine;
    parameter ARRAY_ROWS = 4;
    parameter ARRAY_COLS = 8;
    parameter TEST_LAYER = 1;
    localparam ADDR_WIDTH = 16, SHIFT_WIDTH = 6;
    localparam IH = (TEST_LAYER == 1) ? 20 : 10;
    localparam IW = (TEST_LAYER == 1) ? 16 : 8;
    localparam IC = (TEST_LAYER == 1) ? 1 : 8;
    localparam OH = IH, OW = IW, OC = 8;
    localparam K = 9*IC;
    localparam [7:0] IH_VALUE = IH, IW_VALUE = IW, IC_VALUE = IC;
    localparam [7:0] OH_VALUE = OH, OW_VALUE = OW, OC_VALUE = OC;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;
    reg start_valid = 0;
    reg [7:0] cfg_input_height = IH_VALUE;
    wire start_ready, busy, done;
    wire [ADDR_WIDTH-1:0] activation_read_addr, weight_read_addr;
    wire [ADDR_WIDTH-1:0] parameter_tile_addr;
    reg [7:0] activation_mem [0:IH*IW*IC-1];
    reg [ARRAY_COLS*8-1:0] weight_mem [0:K*((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*32-1:0] bias_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*32-1:0] multiplier_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    wire [7:0] activation_read_data = activation_mem[activation_read_addr];
    wire [ARRAY_COLS*8-1:0] weight_read_data = weight_mem[weight_read_addr];
    wire [ARRAY_COLS*32-1:0] bias_read_data = bias_mem[parameter_tile_addr];
    wire [ARRAY_COLS*32-1:0] multiplier_read_data = multiplier_mem[parameter_tile_addr];
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_read_data = shift_mem[parameter_tile_addr];
    wire output_write_valid;
    reg output_write_ready = 1;
    wire [ADDR_WIDTH-1:0] output_write_addr;
    wire [ARRAY_COLS*8-1:0] output_write_data;
    wire [ARRAY_COLS-1:0] output_write_mask;

    conv2d_engine #(.ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS)) dut (
        .clk(clk), .rst(rst), .start_valid(start_valid), .start_ready(start_ready),
        .input_height(cfg_input_height), .input_width(IW_VALUE), .input_channels(IC_VALUE),
        .output_height(OH_VALUE), .output_width(OW_VALUE), .output_channels(OC_VALUE),
        .kernel_height(8'd3), .kernel_width(8'd3), .stride_height(8'd1),
        .stride_width(8'd1), .pad_top(8'd1), .pad_left(8'd1),
        .activation_base(16'd0), .weight_base(16'd0), .output_base(16'd0),
        .output_offset(32'sd0), .activation_min(8'sh80), .activation_max(8'sh7f),
        .final_output_int32(1'b0),
        .busy(busy), .done(done), .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .weight_read_addr(weight_read_addr), .weight_read_data(weight_read_data),
        .parameter_tile_addr(parameter_tile_addr), .bias_read_data(bias_read_data),
        .multiplier_read_data(multiplier_read_data), .shift_read_data(shift_read_data),
        .output_write_valid(output_write_valid),
        .output_write_ready(output_write_ready), .output_write_addr(output_write_addr),
        .output_write_data(output_write_data), .output_write_mask(output_write_mask),
        .int32_write_valid(), .int32_write_ready(1'b1), .int32_write_addr(),
        .int32_write_data(), .int32_write_mask()
    );

    integer expected [0:OH*OW*OC-1];
    integer y, x, c, ci, ky, kx, iy, ix, sum, index, tile, lane, windex;
    integer writes = 0, cycles = 0, stalls = 0;

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (cycles > 200000) $fatal(1, "conv engine timeout");
        if (!rst && output_write_valid && !output_write_ready) stalls = stalls + 1;
        if (!rst && output_write_valid && output_write_ready) begin
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1) begin
                index = output_write_addr + lane;
                if (output_write_mask[lane]) begin
                    if ($signed(output_write_data[lane*8 +: 8]) !== expected[index])
                        $fatal(1, "output index=%0d expected=%0d actual=%0d",
                               index, expected[index],
                               $signed(output_write_data[lane*8 +: 8]));
                end
            end
            writes = writes + 1;
        end
    end

    initial begin
        // Deterministic small values avoid output saturation and make the
        // identity Q31 requantization (multiplier=0.5, shift=+1) exact.
        for (index = 0; index < IH*IW*IC; index = index + 1)
            activation_mem[index] = (index % 9) - 4;
        for (tile = 0; tile < (OC+ARRAY_COLS-1)/ARRAY_COLS; tile = tile + 1) begin
            bias_mem[tile] = 0;
            multiplier_mem[tile] = 0;
            shift_mem[tile] = 0;
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1) begin
                bias_mem[tile][lane*32 +: 32] = lane - 3;
                multiplier_mem[tile][lane*32 +: 32] = 32'sh40000000;
                shift_mem[tile][lane*SHIFT_WIDTH +: SHIFT_WIDTH] = 1;
            end
        end
        for (windex = 0; windex < K*((OC+ARRAY_COLS-1)/ARRAY_COLS); windex = windex + 1) begin
            weight_mem[windex] = 0;
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1)
                weight_mem[windex][lane*8 +: 8] = ((windex*5 + lane*3) % 5) - 2;
        end

        for (y = 0; y < OH; y = y + 1)
            for (x = 0; x < OW; x = x + 1)
                for (c = 0; c < OC; c = c + 1) begin
                    tile = c / ARRAY_COLS;
                    lane = c % ARRAY_COLS;
                    sum = lane - 3;
                    for (ky = 0; ky < 3; ky = ky + 1)
                        for (kx = 0; kx < 3; kx = kx + 1)
                            for (ci = 0; ci < IC; ci = ci + 1) begin
                                iy = y + ky - 1;
                                ix = x + kx - 1;
                                if (iy >= 0 && ix >= 0 && iy < IH && ix < IW)
                                    sum = sum +
                                        $signed(activation_mem[(iy*IW+ix)*IC+ci]) *
                                        $signed(weight_mem[((ky*3+kx)*IC+ci)*
                                            ((OC+ARRAY_COLS-1)/ARRAY_COLS)+tile]
                                            [lane*8 +: 8]);
                            end
                    if (sum > 127) sum = 127;
                    if (sum < -128) sum = -128;
                    expected[(y*OW+x)*OC+c] = sum;
                end

        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0; cfg_input_height = 0; start_valid = 1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid descriptor was accepted");
        end
        @(negedge clk); start_valid = 0; cfg_input_height = IH_VALUE;
        @(negedge clk); start_valid = 1;
        @(posedge clk); while (!start_ready) @(posedge clk);
        @(negedge clk); start_valid = 0;

        while (!done) begin
            @(negedge clk);
            output_write_ready = ($urandom_range(0, 7) != 0);
            @(posedge clk);
        end
        @(negedge clk); output_write_ready = 1;
        if (busy) $fatal(1, "busy remained high after completion");
        if (writes != OH*OW*((OC+ARRAY_COLS-1)/ARRAY_COLS))
            $fatal(1, "write count expected=%0d actual=%0d",
                   OH*OW*((OC+ARRAY_COLS-1)/ARRAY_COLS), writes);
        if (stalls == 0) $fatal(1, "missing output backpressure coverage");
        $display("ALL CONV2D ENGINE TESTS PASSED layer=%0d rows=%0d cols=%0d writes=%0d cycles=%0d stalls=%0d",
                 TEST_LAYER, ARRAY_ROWS, ARRAY_COLS, writes, cycles, stalls);
        $finish;
    end
endmodule
