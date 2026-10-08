`timescale 1ns/1ps

module tb_tinycnn8_npu_mmio_wrapper;
    localparam ARRAY_COLS = 8;
    localparam SHIFT_WIDTH = 6;

    reg clk = 0;
    reg rst_ni = 0;
    reg req = 0;
    reg we = 0;
    reg [31:0] addr = 0;
    reg [31:0] wdata = 0;
    wire [31:0] rdata;
    wire irq;
    wire [31:0] dma_src, dma_dst, dma_len;
    wire dma_start, dma_irq_en, dma_clear_done;

    integer k, lane, index, ic, expected, cycles;
    reg [63:0] weight_word;
    reg [31:0] status_word;
    reg signed [31:0] logit_word;

    always #5 clk = ~clk;

    tinycnn8_npu_mmio_wrapper dut (
        .clk_i(clk), .rst_ni(rst_ni), .req_i(req), .we_i(we),
        .addr_i(addr), .wdata_i(wdata), .rdata_o(rdata), .irq_o(irq),
        .dma_src_o(dma_src), .dma_dst_o(dma_dst), .dma_len_o(dma_len),
        .dma_start_o(dma_start), .dma_irq_en_o(dma_irq_en),
        .dma_clear_done_o(dma_clear_done), .dma_busy_i(1'b0),
        .dma_done_i(1'b0)
    );

    task mmio_write;
        input [15:0] offset;
        input [31:0] value;
        begin
            @(negedge clk);
            req = 1; we = 1; addr = {16'h7000, offset}; wdata = value;
            @(posedge clk);
            @(negedge clk);
            req = 0; we = 0;
        end
    endtask

    task mmio_read;
        input [15:0] offset;
        output [31:0] value;
        begin
            @(negedge clk);
            req = 1; we = 0; addr = {16'h7000, offset};
            @(posedge clk);
            @(negedge clk);
            value = rdata;
            req = 0;
        end
    endtask

    task write_activation;
        input integer activation_index;
        input integer value;
        begin
            mmio_write(16'h1000 + activation_index*4, value);
        end
    endtask

    task write_weight;
        input integer weight_index;
        input [63:0] value;
        begin
            mmio_write(16'h2000 + weight_index*8, value[31:0]);
            mmio_write(16'h2004 + weight_index*8, value[63:32]);
        end
    endtask

    task stage_parameter_lane;
        input integer layer;
        input integer lane_index;
        input signed [31:0] bias;
        input signed [31:0] multiplier;
        input signed [5:0] shift;
        reg [15:0] base;
        begin
            base = 16'h3000 + layer*16'h0100;
            mmio_write(base + lane_index*4, bias);
            mmio_write(base + 16'h0020 + lane_index*4, multiplier);
            mmio_write(base + 16'h0040 + lane_index*4,
                       {{26{shift[5]}}, shift});
        end
    endtask

    task commit_parameter;
        input integer layer;
        begin
            mmio_write(16'h3060 + layer*16'h0100, 32'd1);
        end
    endtask

    task configure_parameters;
        integer layer_index;
        reg signed [31:0] multiplier;
        reg signed [5:0] shift;
        begin
            // Conv1: alternating encodings of an effective multiplier of 1.
            for (lane = 0; lane < 8; lane = lane + 1) begin
                multiplier = lane[0] ? 32'sh20000000 : 32'sh40000000;
                shift = lane[0] ? 6'sd2 : 6'sd1;
                stage_parameter_lane(0, lane, lane, multiplier, shift);
            end
            commit_parameter(0);

            // Conv2: identity requantization, again with per-channel shifts.
            for (lane = 0; lane < 8; lane = lane + 1) begin
                multiplier = lane[0] ? 32'sh20000000 : 32'sh40000000;
                shift = lane[0] ? 6'sd2 : 6'sd1;
                stage_parameter_lane(1, lane, 0, multiplier, shift);
            end
            commit_parameter(1);

            // FC only consumes bias; multiplier and shift are ignored.
            for (lane = 0; lane < 8; lane = lane + 1)
                stage_parameter_lane(3, lane, lane-2, 0, 0);
            commit_parameter(3);
        end
    endtask

    initial begin
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_ni = 1;

        mmio_read(16'h0030, status_word);
        if (status_word !== 32'h0002_0001)
            $fatal(1, "unexpected Flatten MMIO ABI version=%h", status_word);

        // Reject the reserved signed shift value -32 at parameter commit.
        stage_parameter_lane(0, 0, 0, 32'sh40000000, -6'sd32);
        commit_parameter(0);
        mmio_read(16'h0004, status_word);
        if (!status_word[3] || status_word[7:4] != 4'd5)
            $fatal(1, "invalid shift was not rejected status=%h", status_word);
        mmio_write(16'h0000, 32'h00000008); // clear error

        // Layer 2 belonged to the removed GAP stage and must be rejected.
        commit_parameter(2);
        mmio_read(16'h0004, status_word);
        if (!status_word[3] || status_word[7:4] != 4'd6)
            $fatal(1, "reserved parameter layer was not rejected status=%h", status_word);
        mmio_write(16'h0000, 32'h00000008);

        // Reject an invalid class count without changing the configured model.
        mmio_write(16'h0008, 32'd9);
        mmio_read(16'h0004, status_word);
        if (!status_word[3] || status_word[7:4] != 4'd1)
            $fatal(1, "invalid class count was not rejected status=%h", status_word);
        mmio_write(16'h0000, 32'h00000008);
        mmio_write(16'h0008, 32'd6);

        for (index = 0; index < 20*16; index = index + 1)
            write_activation(index, 2);

        // Conv1: center tap only, producing channel c = 2+c after bias.
        for (k = 0; k < 9; k = k + 1) begin
            weight_word = 0;
            for (lane = 0; lane < 8; lane = lane + 1)
                if (k == 4)
                    weight_word[lane*8 +: 8] = 1;
            write_weight(k, weight_word);
        end

        // Conv2: identity matrix at the center spatial tap.
        for (k = 0; k < 72; k = k + 1) begin
            weight_word = 0;
            ic = k % 8;
            for (lane = 0; lane < 8; lane = lane + 1)
                if ((k >= 32) && (k < 40) && (lane == ic))
                    weight_word[lane*8 +: 8] = 1;
            write_weight(9+k, weight_word);
        end

        // Flatten FC weights for six output classes (160 NHWC inputs).
        for (k = 0; k < 160; k = k + 1) begin
            weight_word = 0;
            for (lane = 0; lane < 6; lane = lane + 1)
                weight_word[lane*8 +: 8] = ((k+lane)%3)-1;
            write_weight(81+k, weight_word);
        end

        configure_parameters();

        // Start one complete inference and enable the completion IRQ.
        mmio_write(16'h0000, 32'h00000003);
        cycles = 0;
        while (!irq) begin
            @(posedge clk);
            cycles = cycles + 1;
            if (cycles > 250000)
                $fatal(1, "MMIO TinyCNN inference timeout");
        end

        mmio_read(16'h0004, status_word);
        if (!status_word[2] || status_word[1] || status_word[3])
            $fatal(1, "bad completion status=%h", status_word);

        for (lane = 0; lane < 6; lane = lane + 1) begin
            expected = lane-2;
            for (ic = 0; ic < 160; ic = ic + 1)
                expected = expected + (2+(ic%8))*(((ic+lane)%3)-1);
            mmio_read(16'h0010 + lane*4, logit_word);
            if (logit_word !== expected)
                $fatal(1, "logit=%0d expected=%0d actual=%0d",
                       lane, expected, logit_word);
        end

        // Clear the latched completion while retaining IRQ enable.
        mmio_write(16'h0000, 32'h00000006);
        if (irq)
            $fatal(1, "IRQ did not clear");
        mmio_read(16'h0004, status_word);
        if (status_word[2])
            $fatal(1, "done latch did not clear status=%h", status_word);

        $display("ALL TINYCNN8 MMIO WRAPPER TESTS PASSED cycles=%0d", cycles);
        $finish;
    end
endmodule
