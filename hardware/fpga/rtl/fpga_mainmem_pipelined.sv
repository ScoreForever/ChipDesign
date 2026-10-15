`timescale 1ns/1ps

module fpga_mainmem_pipelined #(
    parameter int ADDR_W = 11,
    parameter int DATA_W = 32,
    parameter INIT_FILE = "hardware/soc/sim/tb/lab3_test1.hex",
    parameter PIPELINED = 0
) (
    input  logic              clk_i,
    input  logic              rst_ni,
    input  logic              req_i,
    input  logic              we_i,
    input  logic [3:0]        be_i,
    input  logic [31:0]       addr_i,
    input  logic [DATA_W-1:0] wdata_i,
    output logic [DATA_W-1:0] rdata_o
);

  fpga_sram_pipelined #(
      .AddrWidth(ADDR_W),
      .DataWidth(DATA_W),
      .INIT_FILE(INIT_FILE),
      .PIPELINED(PIPELINED)
  ) i_mainmem (
      .clk_i (clk_i),
      .rst_ni(rst_ni),
      .req_i (req_i),
      .wen_i (be_i & {4{we_i}}),
      .addr_i(addr_i[ADDR_W+1:2]),
      .data_i(wdata_i),
      .data_o(rdata_o)
  );

endmodule
