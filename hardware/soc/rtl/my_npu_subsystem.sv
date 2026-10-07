`timescale 1ns/1ps

module my_npu_subsystem #(
    parameter int OPT_GATHER_LOAD = 0,
    parameter int OPT_SPATIAL_TILE = 0,
    parameter int SPATIAL_TILE = 16
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_i,
    input  logic        we_i,
    input  logic [31:0] addr_i,
    input  logic [31:0] wdata_i,
    output logic [31:0] rdata_o,
    output logic        irq_o,

    // DMA config passthrough
    output logic [31:0] dma_src_o,
    output logic [31:0] dma_dst_o,
    output logic [31:0] dma_len_o,
    output logic        dma_start_o,
    output logic        dma_irq_en_o,
    output logic        dma_clear_done_o,
    input  logic        dma_busy_i,
    input  logic        dma_done_i
);

  tinycnn8_npu_mmio_wrapper #(
      .ARRAY_ROWS (4),
      .ARRAY_COLS (8),
      .SHIFT_WIDTH(6),
      .OPT_GATHER_LOAD(OPT_GATHER_LOAD),
      .OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
      .SPATIAL_TILE(SPATIAL_TILE)
  ) i_npu_core (
      .clk_i   (clk_i),
      .rst_ni  (rst_ni),
      .req_i   (req_i),
      .we_i    (we_i),
      .addr_i  (addr_i),
      .wdata_i (wdata_i),
      .rdata_o (rdata_o),
      .irq_o   (irq_o),
      .dma_src_o        (dma_src_o),
      .dma_dst_o        (dma_dst_o),
      .dma_len_o        (dma_len_o),
      .dma_start_o      (dma_start_o),
      .dma_irq_en_o     (dma_irq_en_o),
      .dma_clear_done_o (dma_clear_done_o),
      .dma_busy_i       (dma_busy_i),
      .dma_done_i       (dma_done_i)
  );

endmodule
