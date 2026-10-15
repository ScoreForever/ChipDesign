`timescale 1ns/1ps
// Unit-level DMA + production wrapper integration using a built workload image.
// This bypasses the CPU and AXI fabric; it is not a full SoC workload run.
module tb_npu_dma_packed #(parameter FUSED_CORE=1);
    logic clk=0, rst_n=0;
    always #5 clk=~clk;
    logic cpu_req=0, cpu_we=0;
    logic [31:0] cpu_addr=0, cpu_data=0;
    wire [31:0] rdata, src, dst, len;
    wire start, clear_done, irq_en, busy, done, irq, dma_irq;
    wire req, we;
    wire [31:0] addr, data;
    wire [3:0] be;
    logic gnt=0, valid=0, bus_write=0;
    logic [31:0] bus_addr=0, bus_data=0, read_data=0;
    integer response_delay=0;
    logic pending=0;
    logic [31:0] memory [0:2047];
    logic [31:0] expected_logits [0:47];
    string image, golden;
    reg [31:0] model_addr, inputs_addr, status, value;
    integer count=12, frame=-1, seen=0, transfer_cycles, clocks=0;
    integer layer, base, paddr;
    always @(posedge clk) clocks <= clocks+1;

    tinycnn8_npu_mmio_wrapper #(.FUSED_CORE(FUSED_CORE)) dut(
        .clk_i(clk), .rst_ni(rst_n), .req_i(cpu_req || bus_write),
        .we_i(cpu_we || bus_write), .addr_i(bus_write ? bus_addr : cpu_addr),
        .wdata_i(bus_write ? bus_data : cpu_data), .rdata_o(rdata), .irq_o(irq),
        .dma_src_o(src), .dma_dst_o(dst), .dma_len_o(len), .dma_start_o(start),
        .dma_clear_done_o(clear_done), .dma_irq_en_o(irq_en), .dma_busy_i(busy), .dma_done_i(done));
    npu_dma dma(.clk_i(clk), .rst_ni(rst_n), .src_i(src), .dst_i(dst), .len_i(len),
        .start_i(start), .clear_done_i(clear_done), .irq_en_i(irq_en), .busy_o(busy),
        .done_o(done), .irq_o(dma_irq), .req_o(req), .we_o(we), .addr_o(addr),
        .wdata_o(data), .be_o(be), .gnt_i(gnt), .valid_i(valid), .rdata_i(read_data));

    // A single outstanding memory adapter with delayed write responses.
    // Deliver the write at grant, then respond eight clocks later, after drain.
    always @(negedge clk) begin
        gnt=0; valid=0; bus_write=0;
        if (rst_n) begin
            if (pending) begin
                if (response_delay==0) begin
                    valid=1;
                    pending=0;
                end else response_delay=response_delay-1;
            end else if (req) begin
                gnt=1;
                pending=1;
                response_delay=we ? 8 : 2;
                if (be !== 4'hf) $fatal(1, "DMA byte enables");
                if (we) begin
                    bus_write=1; bus_addr=addr; bus_data=data;
                end else begin
                    if (addr < 32'h80000000 || addr >= 32'h80002000)
                        $fatal(1, "DMA read outside SRAM");
                    read_data=memory[(addr-32'h80000000)>>2];
                end
            end
        end
    end
    // Check every native byte, including lane order, signed bytes, final address.
    always @(posedge clk) begin
        if (rst_n && dut.host_activation_we && frame!=-1) begin
            if (dut.host_activation_addr !== seen ||
                dut.host_activation_data !== (frame==-2 ?
                    ((32'h807f01ff >> (8*(seen%4))) & 8'hff) :
                    {24'd0, image_byte(inputs_addr + frame*320 + seen)}))
                $fatal(1, "packed byte mismatch frame=%0d byte=%0d", frame, seen);
            seen=seen+1;
        end
    end
    function automatic [7:0] image_byte(input reg [31:0] address);
        image_byte=memory[(address-32'h80000000)>>2] >> (8*address[1:0]);
    endfunction
    task automatic wr(input reg [15:0] offset, input reg [31:0] v);
        @(negedge clk); cpu_req=1; cpu_we=1; cpu_addr=32'h70000000+offset; cpu_data=v;
        @(negedge clk); cpu_req=0; cpu_we=0;
    endtask
    task automatic rd(input reg [15:0] offset, output reg [31:0] v);
        @(negedge clk); cpu_req=1; cpu_we=0; cpu_addr=32'h70000000+offset;
        @(negedge clk); v=rdata; cpu_req=0;
    endtask
    task automatic copy(input reg [31:0] source, input reg [15:0] destination, input integer words);
        wr('h410, 2); wr('h400, source); wr('h404, 32'h70000000+destination);
        wr('h408, words); wr('h40c, 3);
        transfer_cycles=clocks;
        wait(done); #1;
        transfer_cycles=clocks-transfer_cycles;
        if (busy || !dma_irq) $fatal(1, "DMA completion");
        wr('h410, 2);
        rd('h410, status);
        if (status!=0) $fatal(1, "DMA clear status=%h", status);
    endtask
    initial begin
        if (!$value$plusargs("IMAGE=%s", image) ||
            !$value$plusargs("GOLDEN=%s", golden) ||
            !$value$plusargs("MODEL_ADDR=%h", model_addr) ||
            !$value$plusargs("INPUTS_ADDR=%h", inputs_addr))
            $fatal(1, "requires IMAGE, GOLDEN (48 raw INT32 hex words), MODEL_ADDR, INPUTS_ADDR");
        $readmemh(image, memory);
        $readmemh(golden, expected_logits);
        repeat(4) @(negedge clk);
        rst_n=1;
        rd('h30, value);
        if (value !== (FUSED_CORE ? 32'h00020002 : 32'h00020001)) $fatal(1, "VERSION changed");
        // Boundary and alignment errors retain the existing error codes.
        wr('h1801, 0); rd('hc, value);
        if (value!=3) $fatal(1, "packed unaligned error");
        wr(0, 8);
        wr('h1940, 0); rd('hc, value);
        if (value!=6) $fatal(1, "packed upper boundary");
        wr(0, 8);
        wr('h1800, 32'h807f01ff);
        wr(0, 1); rd('hc, value);
        if (value!=2) $fatal(1, "start before packed drain");
        wr(0, 8);
        wr('h1800, 32'h807f01ff);
        wr('h1804, 32'h12345678); rd('hc, value);
        if (value!=2) $fatal(1, "packed overrun");
        wr(0, 8);
        // Exactly four clocks between writes, including the final word.
        frame=-2; seen=0;
        for (integer k=0; k<80; k=k+1) begin
            wr('h1800+4*k, 32'h807f01ff);
            repeat(2) @(negedge clk);
        end
        repeat(4) @(negedge clk);
        if (seen!=320) $fatal(1, "minimum-spacing byte count");
        frame=-1;
        rd('h70, value);
        if (value!=0) $fatal(1, "packed drain");
        rd('h1800, value);
        if (value!=0) $fatal(1, "packed read");
        rd('hc, value);
        if (value!=0) $fatal(1, "packed minimum spacing rejected");
        copy(model_addr, 'h2000, 482);
        for (integer group_index=0; group_index<3; group_index=group_index+1) begin
            layer=group_index==2 ? 3 : group_index;
            base='h3000+layer*'h100;
            paddr=model_addr+1928+group_index*72;
            copy(paddr, base, 16);
            for (integer lane=0; lane<8; lane=lane+1)
                wr(base+'h40+4*lane, $signed(image_byte(paddr+64+lane)));
            wr(base+'h60, 1);
        end
        for (integer f=0; f<count; f=f+1) begin
            frame=f; seen=0;
            copy(inputs_addr+f*320, 'h1800, 80);
            rd('h70, status);
            if (status!=0 || seen!=320) $fatal(1, "packed input incomplete bytes=%0d", seen);
            $display("PACKED_INPUT frame=%0d dma_cycles=%0d", f, transfer_cycles);
            wr(0, 3);
            if (f==0) begin
                wr('h1800, 0); rd('hc, value);
                if (value!=2) $fatal(1, "packed write during inference");
                wr(0, 10);
            end
            wait(irq);
            rd(4, status);
            if (status[3] || !status[2]) $fatal(1, "NPU status=%h", status);
            for (integer lane=0; lane<4; lane=lane+1) begin
                rd('h10+4*lane, value);
                if (value !== expected_logits[f*4+lane])
                    $fatal(1, "raw logit mismatch frame=%0d lane=%0d got=%h expected=%h",
                        f, lane, value, expected_logits[f*4+lane]);
                $display("PACKED_LOGIT frame=%0d lane=%0d raw=%0d", f, lane, $signed(value));
            end
            wr(0, 6);
        end
        $display("PASS DMA packed model and 12 inputs FUSED_CORE=%0d", FUSED_CORE);
        $finish;
    end
    initial begin
        #20000000; $fatal(1, "DMA packed timeout");
    end
endmodule
