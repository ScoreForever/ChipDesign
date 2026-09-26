`timescale 1ns/1ps
module tb_ws_pe;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    reg ce = 0;
    reg weight_we = 0;
    reg [7:0] weight_in = 0;
    reg [7:0] act_in = 0;
    reg [31:0] psum_in = 0;
    wire [7:0] act_out;
    wire [31:0] psum_out;
    integer checked = 0;
    reg [31:0] held_psum;
    reg [7:0] held_act;

    ws_pe dut (.*);

    task check_case;
        input integer act;
        input integer weight;
        input integer psum;
        reg signed [31:0] expected;
        begin
            @(negedge clk);
            weight_we = 1;
            weight_in = weight;
            ce = 0;
            @(posedge clk);
            @(negedge clk);
            weight_we = 0;
            ce = 1;
            act_in = act;
            psum_in = psum;
            expected = psum + act * weight;
            @(posedge clk);
            #1;
            if ($signed(psum_out) !== expected || act_out !== act_in)
                $fatal(1, "PE act=%0d weight=%0d psum=%0d got=%0d expected=%0d",
                       act, weight, psum, $signed(psum_out), expected);
            checked = checked + 1;
        end
    endtask

    initial begin
        repeat (3) @(posedge clk);
        @(negedge clk);
        rst = 0;
        check_case(7, 9, 0);
        check_case(7, -9, 100);
        check_case(-7, 9, -100);
        check_case(-7, -9, 23);
        check_case(-128, 127, 0);
        check_case(127, -128, 1000);
        check_case(-128, -128, -1000);
        check_case(127, 127, -1);
        check_case(0, -128, 123456);
        check_case(-1, 0, -123456);
        @(negedge clk);
        ce = 0;
        held_act = act_out;
        held_psum = psum_out;
        act_in = 8'h7f;
        psum_in = 32'h12345678;
        repeat (3) begin
            @(posedge clk);
            #1;
            if (act_out !== held_act || psum_out !== held_psum)
                $fatal(1, "PE clock enable failed to hold registers");
        end
        $display("PASS tb_ws_pe: %0d arithmetic cases and clock enable", checked);
        $finish;
    end
endmodule
