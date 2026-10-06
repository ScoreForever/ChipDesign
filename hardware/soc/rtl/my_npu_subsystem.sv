`timescale 1ns/1ps

module my_npu_subsystem (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_i,
    input  logic        we_i,
    input  logic [31:0] addr_i,
    input  logic [31:0] wdata_i,
    output logic [31:0] rdata_o
);

  npu_mmio_wrapper #(
      .ACT_WIDTH  (8),
      .WGT_WIDTH  (8),
      .ACC_WIDTH  (32),
      .ARRAY_ROWS (4),
      .ARRAY_COLS (8),
      .LANES      (8),
      .DATA_WIDTH (8)
  ) i_npu_core (
      .clk_i   (clk_i),
      .rst_ni  (rst_ni),
      .req_i   (req_i),
      .we_i    (we_i),
      .addr_i  (addr_i),
      .wdata_i (wdata_i),
      .rdata_o (rdata_o)
  );

endmodule
