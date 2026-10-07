`timescale 1ns/1ps
// Reuse the arithmetic/trace scoreboard with reset-busy, restart, repeated
// jobs and forced Matrix retirement backpressure. Override SPATIAL_TILE to
// cover divisible and tail spatial tiles without copying any compute core.
module tb_conv2d_tiled_lifecycle;
    parameter SPATIAL_TILE = 16;
    parameter OPT_GATHER_LOAD = 1;
    parameter OPT_SPATIAL_TILE = 1;
    parameter FALLBACK_PAD = 1;
    parameter FINAL_INT32 = 0;
    parameter ARRAY_ROWS = 4;
    parameter ARRAY_COLS = 8;
    tb_conv2d_engine #(
        .TEST_LAYER(2), .TEST_LIFECYCLE(1),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD),
        .OPT_SPATIAL_TILE(OPT_SPATIAL_TILE), .SPATIAL_TILE(SPATIAL_TILE),
        .FALLBACK_PAD(FALLBACK_PAD), .FINAL_INT32(FINAL_INT32),
        .ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS)
    ) test();
endmodule
