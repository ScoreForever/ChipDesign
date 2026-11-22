`timescale 1ns/1ps
// Physical-pin JTAG verification of Innovation 4: IR=15h (STREAM_WRITE) & IR=16h (STREAM_READ)
// on the exact ASIC top-level my_soc_top (zero new pins!).
module tb_jtag_stream;
    reg clk=0, rst_n=0;
    always #10 clk = ~clk; // 50 MHz system clock (20 ns)
    reg tck=1, tms=1, tdi=0;
    wire tdo, irq;
    my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));

    // 5 MHz TCK (200 ns period, 10x slower than 50 MHz core -> realistic FT232H speed)
    integer tcks = 0;
    task tick(input reg m, input reg d, output reg q);
    begin
        tms = m; tdi = d; #40; tck = 0; #100; q = tdo; tck = 1; #60;
        tcks = tcks + 1; 
    end
    endtask

    reg dummy;
    task reset_tap;
        integer k;
    begin
        for (k = 0; k < 6; k = k + 1) tick(1, 0, dummy);
        tick(0, 0, dummy); // RTI
    end
    endtask

    task select_ir(input [4:0] code);
        integer k;
    begin
        tick(1, 0, dummy); tick(1, 0, dummy); tick(0, 0, dummy); tick(0, 0, dummy); // RTI->SHIR
        for (k = 0; k < 5; k = k + 1) tick(k == 4, code[k], dummy);
        tick(1, 0, dummy); tick(0, 0, dummy); // UIR->RTI
    end
    endtask

    task scan72(input [71:0] tx, output [71:0] rx);
        integer k;
        reg q;
    begin
        rx = 0;
        tick(1, 0, dummy); tick(0, 0, dummy); tick(0, 0, dummy); // RTI->SHDR
        for (k = 0; k < 72; k = k + 1) begin
            tick(k == 71, tx[k], q);
            rx[k] = q;
        end
        tick(1, 0, dummy); tick(0, 0, dummy); // UDR->RTI
    end
    endtask

    task send_cmd(input [7:0] op, input [31:0] addr, input [31:0] data, output [31:0] rdata, output [7:0] status);
        reg [71:0] rx;
        integer tries;
    begin
        select_ir(5'h12);
        scan72({op, addr, data}, rx);
        for (tries = 0; tries < 8; tries = tries + 1) begin
            scan72(72'd0, rx);
            if (rx[71]) begin
                status = rx[71:64];
                rdata = rx[31:0];
                disable send_cmd;
            end
        end
        $fatal(1, "JTAG command timeout op=%0d addr=0x%08x", op, addr);
    end
    endtask

    localparam NWORDS = 64; // 256 bytes continuous stream!
    reg [31:0] expected[0:NWORDS-1];
    reg [31:0] got[0:NWORDS-1];
    reg [31:0] rdata;
    reg [7:0] st;
    integer i, b, t_start, t_stream_wr, t_stream_rd;
    reg q;

    initial begin
        rst_n = 1; #20; rst_n = 0; #200; rst_n = 1; #200;
        reset_tap;

        // 1. Setup minimal valid config so INPUT_BEGIN allows writing 256 bytes at act[0..255]
        send_cmd(8'd3, 32'd0, 32'd0, rdata, st); // MODEL_BEGIN
        send_cmd(8'd2, 32'h10, 32'd64, rdata, st); // model_bytes
        send_cmd(8'd2, 32'h14, 32'd1, rdata, st);  // op_count
        send_cmd(8'd2, 32'h18, 32'd0, rdata, st);  // input_base=0
        send_cmd(8'd2, 32'h1c, NWORDS*4, rdata, st); // input_bytes=256
        send_cmd(8'd2, 32'h20, 32'd0, rdata, st);  // output_base=0
        send_cmd(8'd2, 32'h24, NWORDS*4, rdata, st); // output_bytes=256
        send_cmd(8'd2, 32'h64, 32'd0, rdata, st);  // input_i32=0
        // Cancel model loading with CLEAR (10) -> actually just stream directly into model window 0x10000..
        // Better: model_bytes=256, stream all 64 words into Model SRAM at 0x10000!
        send_cmd(8'd10, 32'd0, 32'd0, rdata, st); // CLEAR
        send_cmd(8'd3, 32'd0, 32'd0, rdata, st);  // MODEL_BEGIN
        send_cmd(8'd2, 32'h10, NWORDS*4, rdata, st);
        send_cmd(8'd2, 32'h14, 32'd1, rdata, st);
        send_cmd(8'd2, 32'h18, 32'd0, rdata, st);
        send_cmd(8'd2, 32'h1c, NWORDS*4, rdata, st);
        send_cmd(8'd2, 32'h20, 32'd0, rdata, st);
        send_cmd(8'd2, 32'h24, NWORDS*4, rdata, st);
        send_cmd(8'd2, 32'h64, 32'd0, rdata, st);

        for (i = 0; i < NWORDS; i = i + 1)
            expected[i] = 32'hA500_0000 ^ (i * 32'h0102_0305);

        // 2. Write word 0 via IR=12h at 0x10000 (seeds burst_word = 0x10004 >> 2)
        //    Actually let's test on Activation SRAM (0x20000) so we can READ BACK with IR=16h!
        //    To write Activation SRAM we need model_valid=1 -> load a 1-op ReLU model first!
        send_cmd(8'd10, 32'd0, 32'd0, rdata, st); // CLEAR
        send_cmd(8'd3, 32'd0, 32'd0, rdata, st);  // MODEL_BEGIN
        send_cmd(8'd2, 32'h10, 32'd64, rdata, st);
        send_cmd(8'd2, 32'h14, 32'd1, rdata, st);
        send_cmd(8'd2, 32'h18, 32'd0, rdata, st);
        send_cmd(8'd2, 32'h1c, NWORDS*4, rdata, st);
        send_cmd(8'd2, 32'h20, 32'd0, rdata, st);
        send_cmd(8'd2, 32'h24, NWORDS*4, rdata, st);
        send_cmd(8'd2, 32'h64, 32'd0, rdata, st);
        // 16-word ReLU descriptor (1x64x8 i4 = 256 bytes, in-place at 0)
        // word0: op=4(ReLU), word1: src=0, word2: dst=0, word3: ih=1, iw=64 -> 0x00400001
        // word4: ic=8, oc=8 -> 0x00080008, word10: dh=1, dw=64 -> 0x00400001, word15: 0x49340f88
        send_cmd(8'd2, 32'h10000, 32'h00000004, rdata, st);
        // Stream words 1..15 of descriptor via IR=15h (STREAM_WRITE)!
        select_ir(5'h15);
        tick(1, 0, dummy); tick(0, 0, dummy); tick(0, 0, dummy); // RTI->SHDR
        for (i = 1; i < 16; i = i + 1) begin
            case (i)
                3: rdata = 32'h0040_0001;
                4: rdata = 32'h0008_0008;
                10: rdata = 32'h0040_0001;
                15: rdata = 32'h4934_0f88;
                default: rdata = 32'd0;
            endcase
            for (b = 0; b < 32; b = b + 1)
                tick((i == 15 && b == 31), rdata[b], q);
        end
        tick(1, 0, dummy); tick(0, 0, dummy); // UDR->RTI
        // Verify overrun == 0 and commit model!
        send_cmd(8'd0, 32'd0, 32'd0, rdata, st);
        if (st[5]) $fatal(1, "Overrun during descriptor STREAM_WRITE!");
        send_cmd(8'd4, 32'd0, 32'd0, rdata, st); // MODEL_COMMIT
        if (st[4:0] != 0) $fatal(1, "MODEL_COMMIT failed error=%0d", st[4:0]);

        // 3. Benchmark & verify 64-word (256-byte) continuous STREAM_WRITE (IR=15h) into Activation SRAM!
        send_cmd(8'd5, 32'd0, 32'd0, rdata, st); // INPUT_BEGIN
        t_start = tcks;
        send_cmd(8'd2, 32'h20000, expected[0], rdata, st); // word 0 seeds burst_word
        select_ir(5'h15);
        tick(1, 0, dummy); tick(0, 0, dummy); tick(0, 0, dummy); // RTI->SHDR ONCE!
        for (i = 1; i < NWORDS; i = i + 1) begin
            for (b = 0; b < 32; b = b + 1)
                tick((i == NWORDS-1 && b == 31), expected[i][b], q);
        end
        tick(1, 0, dummy); tick(0, 0, dummy); // UDR->RTI
        t_stream_wr = tcks - t_start;
        send_cmd(8'd0, 32'd0, 32'd0, rdata, st);
        if (st[5]) $fatal(1, "Overrun during 64-word STREAM_WRITE!");
        send_cmd(8'd6, 32'd0, 32'd0, rdata, st); // INPUT_COMMIT
        if (st[4:0] != 0) $fatal(1, "INPUT_COMMIT failed error=%0d", st[4:0]);

        // 4. Benchmark & verify 64-word (256-byte) continuous STREAM_READ (IR=16h) from Activation SRAM!
        t_start = tcks;
        send_cmd(8'd1, 32'h20000, 32'd0, got[0], st); // read word 0 via IR=12h (reply_data=word0, burst_word=word1)
        select_ir(5'h16);
        tick(1, 0, dummy); tick(0, 0, dummy); tick(0, 0, dummy); // RTI->CDR(prefetch word1)->SHDR ONCE!
        for (i = 0; i < NWORDS - 1; i = i + 1) begin
            for (b = 0; b < 32; b = b + 1) begin
                tick((i == NWORDS-2 && b == 31), 1'b0, q);
                got[i][b] = q;
            end
        end
        tick(1, 0, dummy); tick(0, 0, dummy); // UDR->RTI
        // Final IR=12h poll retrieves last prefetched word (word NWORDS-1) AND checks overrun==0!
        send_cmd(8'd0, 32'd0, 32'd0, got[NWORDS-1], st);
        t_stream_rd = tcks - t_start;
        if (st[5]) $fatal(1, "Overrun during 64-word STREAM_READ!");

        // 5. Bit-exact verification of all 64 words!
        for (i = 0; i < NWORDS; i = i + 1) begin
            if (got[i] !== expected[i])
                $fatal(1, "STREAM mismatch at word %0d: got=0x%08x expected=0x%08x", i, got[i], expected[i]);
        end
        $display("PASS JTAG STREAM_WRITE (IR=15h) & STREAM_READ (IR=16h): %0d/%0d words 0-bit exact!", NWORDS, NWORDS);
        $display("  64-word STREAM_WRITE: %0d TCKs (%.2f TCK/word, vs 37 TCK/word burst & 150+ TCK/word packet)", t_stream_wr, t_stream_wr * 1.0 / NWORDS);
        $display("  64-word STREAM_READ : %0d TCKs (%.2f TCK/word, vs 41 TCK/word burst & 150+ TCK/word packet)", t_stream_rd, t_stream_rd * 1.0 / NWORDS);
        $finish;
    end
endmodule
