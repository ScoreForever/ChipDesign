`timescale 1ns / 1ps
//////////////////////////////////////////////////////////////////////////////////
// Company: 
// Engineer: Zhuolin Li.
// 
// Create Date: 2026/03/18 19:21:06
// Design Name: 
// Module Name: cv32e40p_fpga_clock_gate
// Project Name: 
// Target Devices: 
// Tool Versions: 
// Description: 
// 
// Dependencies: 
// 
// Revision:
// Revision 0.01 - File Created
// Additional Comments: just use for fpga synthesis.
// 
//////////////////////////////////////////////////////////////////////////////////


module cv32e40p_clock_gate(
    input clk_i,
    input en_i,
    input scan_cg_en_i,
    output clk_o
    );
    assign clk_o = clk_i;
endmodule
