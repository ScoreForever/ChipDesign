`timescale 1ns/1ps
// Also instantiated by the integrated DMA test, so the structure regression
// cannot report DMA PASS without exercising delayed write completion.
module npu_dma_checks(output logic finished);
    logic clk = 0;
    always #5 clk = ~clk;
    logic rst_n = 0, start = 0, clear_done = 0, irq_en = 1;
    logic [31:0] src = 32'h80000000, dst = 32'h70001800, len = 0;
    wire busy, done, irq, req, we;
    wire [31:0] addr, wdata;
    wire [3:0] be;
    logic gnt = 0, valid = 0;
    logic [31:0] rdata = 0;
    npu_dma dut(.clk_i(clk), .rst_ni(rst_n), .src_i(src), .dst_i(dst),
        .len_i(len), .start_i(start), .irq_en_i(irq_en), .clear_done_i(clear_done),
        .busy_o(busy), .done_o(done), .irq_o(irq), .req_o(req), .addr_o(addr),
        .we_o(we), .be_o(be), .wdata_o(wdata), .gnt_i(gnt), .valid_i(valid), .rdata_i(rdata));

    task automatic tick;
        @(posedge clk); #1;
    endtask
    task automatic launch(input integer words);
        @(negedge clk); len = words; start = 1;
        tick();
        if (!busy || done || irq) $fatal(1, "DMA launch/stale done");
        @(negedge clk); start = 0;
    endtask
    task automatic beat(input integer index, input bit writing, input integer delay_cycles);
        wait(req);
        if (we !== writing || addr !== (writing ? dst : src) + 4*index || be !== 4'hf)
            $fatal(1, "DMA request address/direction/BE index=%0d", index);
        if (writing && wdata !== (32'h91827300 + index)) $fatal(1, "DMA data");
        // Backpressure must leave the request stable until grant.
        repeat(2) begin
            tick();
            if (!req || we !== writing || addr !== (writing ? dst : src) + 4*index)
                $fatal(1, "DMA request changed before grant");
        end
        @(negedge clk); gnt = 1;
        tick();
        @(negedge clk); gnt = 0;
        repeat(delay_cycles) begin
            tick();
            if (!busy || done || irq || req) $fatal(1, "DMA completed/issued before response");
        end
        @(negedge clk); valid = 1; rdata = 32'h91827300 + index;
        tick();
        @(negedge clk); valid = 0;
    endtask
    task automatic transfer(input integer words);
        launch(words);
        for (integer i=0; i<words; i=i+1) begin
            beat(i, 0, 3);
            beat(i, 1, i == words-1 ? 19 : 4);
        end
        wait(done); #1;
        if (busy || !irq) $fatal(1, "DMA completion flags");
        repeat(3) tick();
        if (!done || !irq) $fatal(1, "DMA done not sticky");
    endtask
    initial begin
        finished = 0;
        repeat(3) tick();
        @(negedge clk); rst_n = 1;
        transfer(1);
        // No reset/clear between transfers: start must remove stale DONE.
        transfer(4);
        irq_en = 0; #1;
        if (irq || !done) $fatal(1, "DMA IRQ mask");
        irq_en = 1;
        @(negedge clk); clear_done = 1;
        tick();
        if (done || irq || busy) $fatal(1, "DMA clear_done");
        @(negedge clk); clear_done = 0;
        transfer(2);
        $display("PASS DMA response barrier: LEN=1, delayed final response, restart, clear_done");
        finished = 1;
    end
    initial begin
        #100000;
        if (!finished) $fatal(1, "DMA unit timeout");
    end
endmodule

module tb_npu_dma_unit;
    wire finished;
    npu_dma_checks checks(finished);
    initial begin
        wait(finished);
        $finish;
    end
endmodule
