`timescale 1ns/1ps
module tb_instr_fetch_port;
    parameter PIPELINED = 1;
    // Run with Icarus -g2012; override PIPELINED to exercise the legacy port.
    // The scoreboard checks every response, order, latency and outstanding limit.
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 0, req = 0;
    reg [31:0] addr = 0;
    wire gnt, valid;
    wire [31:0] rdata;
    wire mreq, mgnt, mvalid, ereq;
    wire [31:0] mdata;
    reg egnt_enable = 1;
    wire egnt = ereq && egnt_enable && rst;
    reg [5:0] ev = 0;
    reg [31:0] ed [0:5];
    reg dreq = 0, dwe = 0;
    reg [3:0] be = 0;
    reg [31:0] da = 0, dw = 0;
    wire [31:0] dr;
    instr_fetch_router router (
        .clk_i(clk), .rst_ni(rst), .req_i(req), .addr_i(addr),
        .gnt_o(gnt), .rvalid_o(valid), .rdata_o(rdata),
        .imem_req_o(mreq), .imem_gnt_i(mgnt),
        .imem_rvalid_i(mvalid), .imem_rdata_i(mdata),
        .ext_req_o(ereq), .ext_gnt_i(egnt),
        .ext_rvalid_i(ev[5]), .ext_rdata_i(ed[5])
    );
    my_mainmem #(.INIT_FILE(""), .PIPELINED(PIPELINED)) mem (
        .clk_i(clk), .rst_ni(rst), .req_i(dreq), .we_i(dwe),
        .be_i(be), .addr_i(da), .wdata_i(dw), .rdata_o(dr),
        .imem_req_i(mreq), .imem_addr_i(addr), .imem_gnt_o(mgnt),
        .imem_rvalid_o(mvalid), .imem_rdata_o(mdata)
    );
    function [31:0] ext_word(input [31:0] a);
        ext_word = a ^ 32'h13579bdf;
    endfunction
    integer i;
    always @(posedge clk or negedge rst) begin
        if (!rst) ev <= 0;
        else begin
            ev <= {ev[4:0], egnt};
            ed[0] <= ext_word(addr);
            for (integer j=1; j<6; j=j+1) ed[j] <= ed[j-1];
        end
    end

    reg [31:0] golden [0:2047];
    reg [31:0] expected [0:1023];
    integer accepted_cycle [0:1023];
    reg direct [0:1023];
    integer head=0, tail=0, cycle=0, checked=0, grants=0;
    integer last_direct=-10, adjacent=0, peak=0;
    integer stalled_switch=0, stalled_capacity=0, stalled_collision=0;
    always @(posedge clk or negedge rst) begin
        cycle = cycle + 1;
        if (!rst) begin
            head=0; tail=0; last_direct=-10;
            if (clk === 1'b1 && (gnt || valid || mreq || ereq)) $fatal(1,"request/response during reset");
        end else begin
            if (valid) begin
                if (head == tail) $fatal(1,"unsolicited response");
                if (rdata !== expected[head])
                    $fatal(1,"response %0d got %h expected %h",head,rdata,expected[head]);
                if (direct[head] && cycle-accepted_cycle[head] != (PIPELINED ? 2 : 1))
                    $fatal(1,"incorrect direct read latency");
                head=head+1; checked=checked+1;
            end
            if (gnt) begin
                direct[tail] = addr >= 32'h80000000 && addr < 32'h80002000;
                expected[tail] = direct[tail] ? golden[addr[12:2]] : ext_word(addr);
                accepted_cycle[tail] = cycle;
                if (direct[tail]) begin
                    if (last_direct == cycle-1) adjacent=adjacent+1;
                    last_direct=cycle;
                end
                tail=tail+1; grants=grants+1;
            end
            if (tail-head > peak) peak=tail-head;
            if (tail-head > 4) $fatal(1,"outstanding overflow");
            if (req && !gnt && router.select_imem != router.route_q && !router.drained)
                stalled_switch=stalled_switch+1;
            if (req && !gnt && router.outstanding_q == 4)
                stalled_capacity=stalled_capacity+1;
            if (mreq && !mgnt) stalled_collision=stalled_collision+1;
            if (dreq && dwe) begin
                for (integer b=0;b<4;b=b+1)
                    if (be[b]) golden[da[12:2]][8*b +: 8] = dw[8*b +: 8];
            end
        end
    end
    task fetch(input [31:0] a);
        begin
            @(negedge clk); req=1; addr=a;
            @(posedge clk);
            while (!gnt) @(posedge clk);
        end
    endtask
    task drain;
        begin
            @(negedge clk); req=0;
            while (head != tail) @(negedge clk);
        end
    endtask
    task write_word(input [31:0] a, input [31:0] v, input [3:0] mask);
        begin
            @(negedge clk); dreq=1; dwe=1; da=a; dw=v; be=mask;
            @(negedge clk); dreq=0; dwe=0;
            repeat (3) @(negedge clk);
        end
    endtask
    task collision(input [3:0] mask, input [31:0] value);
        begin
            @(negedge clk);
            req=1; addr=32'h80000040;
            dreq=1; dwe=1; da=addr; dw=value; be=mask;
            @(posedge clk); if (gnt) $fatal(1,"new-write collision granted");
            @(negedge clk); dreq=0; dwe=0;
            if (PIPELINED) begin
                @(posedge clk); if (gnt) $fatal(1,"pending-write collision granted");
                @(negedge clk);
            end
            @(posedge clk); if (!gnt) $fatal(1,"write did not release fetch");
            drain();
        end
    endtask
    initial begin
        // Initialize through the data port, as a debugger would do.
        repeat (4) @(negedge clk); rst=1;
        for (i=0;i<16;i=i+1) write_word(32'h80000000+4*i,32'hcafe0000+i,15);
        write_word(32'h80000040,32'h11223344,15);
        write_word(32'h80001ffc,32'h76543210,15);
        for (i=0;i<32;i=i+1) fetch(32'h80000000+4*(i%16));
        drain();
        // Boot/Debug -> SRAM, including a long-latency backlog and capacity limit.
        for (i=0;i<9;i=i+1) fetch(32'h00010000+4*i);
        fetch(32'h80000000);
        fetch(32'h00000800);
        fetch(32'h80001ffc);
        fetch(32'h80002000); // Upper bound must use AXI.
        fetch(32'h7ffffffc); // Lower bound must use AXI.
        fetch(32'h80000004);
        drain();
        // Downstream grant backpressure with a stable OBI address.
        egnt_enable=0;
        fork
            begin fetch(32'h00010008); drain(); end
            begin repeat (4) @(negedge clk); egnt_enable=1; end
        join
        collision(15,32'haabbccdd);
        collision(5,32'h12345678);
        // Every nonzero byte mask must merge with the previous word.
        for (i=1;i<16;i=i+1) collision(i[3:0],32'h01234567 ^ (i*32'h1020304));
        collision(15,32'haa34cc78);
        // An unrelated data write must not stall instruction reads.
        fork
            begin for (integer k=0;k<12;k=k+1) fetch(32'h80000040); drain(); end
            begin write_word(32'h80000020,32'hdeadbeef,15); end
        join
        // Check the original data read pipeline and byte merge.
        @(negedge clk); dreq=1; dwe=0; da=32'h80000040;
        @(posedge clk);
        if (PIPELINED) begin
            @(negedge clk); dreq=0;
            repeat (2) @(posedge clk);
        end
        #1; if (dr !== 32'haa34cc78) $fatal(1,"data port read/latency changed: %h",dr);
        @(negedge clk); dreq=0;
        // An accepted data write may complete during reset, as before.
        @(negedge clk); dreq=1; dwe=1; da=32'h80000040; dw=32'hf00dcafe; be=15;
        @(posedge clk);
        @(negedge clk); dreq=0; dwe=0; rst=0;
        repeat (4) @(negedge clk); rst=1;
        fetch(32'h80000040); drain();
        // A write accepted after an instruction grant must not corrupt the
        // older read; the following fetch must observe the completed write.
        fetch(32'h80000040);
        @(negedge clk); req=0; dreq=1; dwe=1; da=addr; dw=32'hdec0de01; be=15;
        @(negedge clk); dreq=0; dwe=0;
        repeat (3) @(negedge clk);
        fetch(32'h80000040); drain();
        // A reset pulse shorter than one clock also cancels protocol validity.
        fetch(32'h80000040);
        @(negedge clk); req=0; rst=0;
        #1; rst=1;
        repeat (4) @(negedge clk);
        fetch(32'h80000040); drain();
        // Reset discards in-flight responses; SRAM contents survive.
        fetch(32'h00010000);
        @(negedge clk); rst=0; req=1;
        repeat (4) @(negedge clk);
        req=0; rst=1;
        repeat (8) @(negedge clk);
        fetch(32'h80000040); drain();
        fetch(32'h80000000);
        @(negedge clk); rst=0; req=0;
        repeat (4) @(negedge clk); rst=1;
        repeat (4) @(negedge clk);
        fetch(32'h80000040); drain();
        if (adjacent < 30 || peak != 4 || stalled_switch == 0 ||
            stalled_capacity == 0 || stalled_collision < 2)
            $fatal(1,"coverage missing");
        $display("PASS PIPELINED=%0d checked=%0d grants=%0d adjacent=%0d peak=%0d switch_stalls=%0d capacity_stalls=%0d collision_stalls=%0d direct_latency=%0d",PIPELINED,checked,grants,adjacent,peak,stalled_switch,stalled_capacity,stalled_collision,(PIPELINED ? 2 : 1));
        $finish;
    end
    initial begin #100000; $fatal(1,"timeout"); end
endmodule
