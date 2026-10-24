`timescale 1ns/1ps

module my_mainmem #(
    parameter int ADDR_W = 11,
    parameter int DATA_W = 32,
    parameter INIT_FILE = "soc/sim/tb/lab3_test1.hex"
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

`ifdef USE_RA1SHD_MAINMEM

  // ASIC implementation: one course-provided 2048 x 32 single-port SRAM.
  // CEN/OEN/WEN on this macro are active low; WEN is a byte write mask.
  RA1SHD_2048x32M8 i_mainmem (
      .CLK (clk_i),
      .CEN (~req_i),
      .OEN (1'b0),
      .WEN (~(be_i & {4{we_i}})),
      .A   (addr_i[ADDR_W+1:2]),
      .D   (wdata_i),
      .Q   (rdata_o)
  );

`else

  sram_ff #(
      .AddrWidth(ADDR_W),
      .DataWidth(DATA_W),
      .INIT_FILE(INIT_FILE)
  ) i_mainmem (
      .clk_i (clk_i),
      .req_i (req_i),
      .wen_i (be_i & {4{we_i}}),
      .addr_i(addr_i[ADDR_W+1:2]),
      .data_i(wdata_i),
      .data_o(rdata_o)
  );

`endif

endmodule
