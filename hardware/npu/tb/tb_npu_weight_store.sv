`timescale 1ns/1ps
module tb_npu_weight_store;
    parameter DATA_WIDTH=64;
    reg clk=0,rst=1,wv=0,req=0,rr=1;
    always #5 clk=~clk;
    wire wr,ready,valid;
    reg [15:0] wa=0,ra=0;
    reg [DATA_WIDTH-1:0] wd=0;
    wire [DATA_WIDTH-1:0] data;
    npu_weight_store #(.DATA_WIDTH(DATA_WIDTH)) dut(
        .clk(clk),.rst(rst),.write_valid(wv),.write_ready(wr),.write_addr(wa),.write_data(wd),
        .read_request(req),.read_ready(ready),.read_addr(ra),
        .read_valid(valid),.read_response_ready(rr),.read_data(data));
    reg [DATA_WIDTH-1:0] expected[0:255];
    reg [15:0] requests[0:2047];
    integer issued=0,returned=0,checked=0,i,pass,waits;
    reg [DATA_WIDTH-1:0] held;
    reg stalled=0;
    always @(posedge clk) begin
        if(rst) begin issued=0;returned=0;stalled=0;end
        else begin
            if(stalled && (!valid || data!==held))$fatal(1,"response changed under backpressure");
            if(valid && rr)begin
                if(returned>=issued)$fatal(1,"unsolicited response");
                if(data!==expected[requests[returned]])$fatal(1,"row=%0d got=%h expected=%h",
                    requests[returned],data,expected[requests[returned]]);
                returned=returned+1;checked=checked+1;
            end
            if(req && ready)begin requests[issued]=ra;issued=issued+1;end
            stalled=valid && !rr;held=data;
        end
    end
    initial begin
        repeat(3)@(negedge clk);rst=0;
        for(pass=0;pass<2;pass=pass+1)begin
            // Different halves and every byte/bit distinguish addressing,
            // lane ordering and replacement of an already populated model.
            for(i=0;i<256;i=i+1)begin
                @(negedge clk);wv=1;wa=i;
                wd=64'hfedcba9876543210 ^ (64'h010305070b0d1113*i) ^ pass;
                expected[i]=wd;
                while(!wr)@(negedge clk);
            end
            @(negedge clk);wv=0;
            while(!wr)@(negedge clk);
            for(i=0;i<256;i=i+1)begin
                @(negedge clk);req=1;ra=(i*73+19)%256;
                while(!ready)@(negedge clk);
            end
            @(negedge clk);req=0;
            while(returned<issued)@(negedge clk);
        end
        // Hold a real response and verify that neither port accepts a new
        // request until the consumer releases ownership.
        @(negedge clk);req=1;ra=255;
        while(!ready)@(negedge clk);
        @(negedge clk);req=0;rr=0;
        while(!valid)@(negedge clk);
        repeat(11)begin @(negedge clk);if(ready||wr)$fatal(1,"stalled response lost ownership");end
        rr=1;@(negedge clk);
        // Reset cancels a pending response, leaves SRAM data intact, and
        // permits a fresh read rather than returning the abandoned one.
        req=1;ra=37;
        while(!ready)@(negedge clk);
        @(negedge clk);req=0;rr=0;
        while(!valid)@(negedge clk);
        rst=1;repeat(2)@(negedge clk);rst=0;rr=1;
        repeat(3)begin @(negedge clk);if(valid)$fatal(1,"stale response after reset");end
        req=1;ra=37;
        while(!ready)@(negedge clk);
        @(negedge clk);req=0;
        while(returned<issued)@(negedge clk);
        $display("PASS weight_store width=%0d checked=%0d",DATA_WIDTH,checked);$finish;
    end
    initial begin #2000000;$fatal(1,"weight store timeout");end
endmodule
