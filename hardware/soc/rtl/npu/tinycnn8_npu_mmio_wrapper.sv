`timescale 1ns/1ps

// Production MMIO adapter for the complete fixed-function TinyCNN-8 NPU.
//
// The CPU sees 32-bit registers and write-only loading windows.  The adapter
// converts those accesses into the native host ports of tinycnn8_npu_top and
// latches the one-cycle inference-done pulse into a level-sensitive IRQ.
// The production SoC uses ARRAY_ROWS=4 and ARRAY_COLS=8.
module tinycnn8_npu_mmio_wrapper #(
    parameter int ARRAY_ROWS = 4,
    parameter int ARRAY_COLS = 8,
    parameter int SHIFT_WIDTH = 6,
    parameter int INPUT_BYTES = 320,
    parameter int WEIGHT_WORDS = 256,
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

    // DMA configuration passthrough.  DMA completion has its own SoC IRQ.
    output logic [31:0] dma_src_o,
    output logic [31:0] dma_dst_o,
    output logic [31:0] dma_len_o,
    output logic        dma_start_o,
    output logic        dma_irq_en_o,
    output logic        dma_clear_done_o,
    input  logic        dma_busy_i,
    input  logic        dma_done_i
);
    localparam int ADDR_WIDTH = 16;

    // Control/status registers.
    logic [3:0] class_count_reg;
    logic       irq_enable_reg;
    logic       done_latched;
    logic       error_latched;
    logic [7:0] error_code;

    // Native complete-NPU interface.
    logic                       npu_start_pulse;
    logic                       npu_start_ready;
    logic                       npu_busy;
    logic                       npu_done;
    logic [8*32-1:0]            npu_logits;
    logic                       host_activation_we;
    logic [ADDR_WIDTH-1:0]      host_activation_addr;
    logic [7:0]                 host_activation_data;
    logic                       host_weight_we;
    logic [ADDR_WIDTH-1:0]      host_weight_addr;
    logic [ARRAY_COLS*8-1:0]    host_weight_data;
    logic                       host_parameter_we;
    logic [1:0]                 host_parameter_layer;
    logic [7:0]                 host_parameter_tile;
    logic [ARRAY_COLS*32-1:0]   host_bias_data;
    logic [ARRAY_COLS*32-1:0]   host_multiplier_data;
    logic [ARRAY_COLS*SHIFT_WIDTH-1:0] host_shift_data;

    // One-word staging is sufficient because software/DMA writes each packed
    // 64-bit weight word as low 32 bits followed by high 32 bits.
    logic [31:0] weight_low_stage;
    logic [ADDR_WIDTH-1:0] weight_low_index;
    logic weight_low_valid;

    // Parameter staging is reused for each of the four parameter groups.
    logic [ARRAY_COLS*32-1:0] param_bias_stage;
    logic [ARRAY_COLS*32-1:0] param_mult_stage;
    logic [ARRAY_COLS*SHIFT_WIDTH-1:0] param_shift_stage;
    logic invalid_shift_stage;

    // DMA registers retain the existing SoC programming model.
    logic [31:0] dma_src_reg;
    logic [31:0] dma_dst_reg;
    logic [31:0] dma_len_reg;
    logic        dma_irq_en_reg;
    logic        dma_start_pulse;
    logic        dma_clear_done_pulse;

    logic [15:0] addr_off;
    logic [15:0] input_index;
    logic [15:0] weight_index;
    logic [1:0]  parameter_layer_index;
    logic [7:0]  parameter_suboffset;
    logic [2:0]  parameter_lane_index;
    logic        addr_in_input;
    logic        addr_in_weight;
    logic        addr_in_parameters;

    assign addr_off = addr_i[15:0];
    assign addr_in_input = (addr_off >= 16'h1000) &&
                           (addr_off < 16'h1000 + INPUT_BYTES*4);
    assign addr_in_weight = (addr_off >= 16'h2000) &&
                            (addr_off < 16'h2000 + WEIGHT_WORDS*8);
    assign addr_in_parameters = (addr_off >= 16'h3000) &&
                                (addr_off <= 16'h33ff);
    assign input_index = (addr_off - 16'h1000) >> 2;
    assign weight_index = (addr_off - 16'h2000) >> 3;
    assign parameter_layer_index = addr_off[9:8];
    assign parameter_suboffset = addr_off[7:0];
    assign parameter_lane_index = addr_off[4:2];

    always_comb begin
        invalid_shift_stage = 1'b0;
        for (int i = 0; i < ARRAY_COLS; i++) begin
            if ($signed(param_shift_stage[i*SHIFT_WIDTH +: SHIFT_WIDTH]) == -32)
                invalid_shift_stage = 1'b1;
        end
    end

    assign irq_o = irq_enable_reg && done_latched;
    assign dma_src_o = dma_src_reg;
    assign dma_dst_o = dma_dst_reg;
    assign dma_len_o = dma_len_reg;
    assign dma_start_o = dma_start_pulse;
    assign dma_irq_en_o = dma_irq_en_reg;
    assign dma_clear_done_o = dma_clear_done_pulse;

    logic [31:0] perf_total_cycles,perf_weight_rows,perf_matrix_issues;
    logic [31:0] perf_matrix_retires,perf_peak_inflight;
    logic [191:0] perf_layer_cycles;
    logic perf_valid,perf_overflow;

    tinycnn8_npu_top #(
        .ARRAY_ROWS   (ARRAY_ROWS),
        .ARRAY_COLS   (ARRAY_COLS),
        .WEIGHT_WORDS (WEIGHT_WORDS),
        .SHIFT_WIDTH  (SHIFT_WIDTH),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD),
        .OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
        .SPATIAL_TILE(SPATIAL_TILE)
    ) i_tinycnn8_npu (
        .clk                  (clk_i),
        .rst                  (~rst_ni),
        .start_valid          (npu_start_pulse),
        .start_ready          (npu_start_ready),
        .class_count          (class_count_reg),
        .busy                 (npu_busy),
        .done                 (npu_done),
        .logits               (npu_logits),
        .host_activation_we   (host_activation_we),
        .host_activation_addr (host_activation_addr),
        .host_activation_data (host_activation_data),
        .host_weight_we       (host_weight_we),
        .host_weight_addr     (host_weight_addr),
        .host_weight_data     (host_weight_data),
        .host_parameter_we    (host_parameter_we),
        .host_parameter_layer (host_parameter_layer),
        .host_parameter_tile  (host_parameter_tile),
        .host_bias_data       (host_bias_data),
        .host_multiplier_data (host_multiplier_data),
        .host_shift_data      (host_shift_data),
        .perf_total_cycles(perf_total_cycles),.perf_layer_cycles(perf_layer_cycles),
        .perf_weight_rows(perf_weight_rows),.perf_matrix_issues(perf_matrix_issues),
        .perf_matrix_retires(perf_matrix_retires),.perf_peak_inflight(perf_peak_inflight),
        .perf_overflow(perf_overflow),.perf_valid(perf_valid)
    );

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            class_count_reg       <= 4'd4;
            irq_enable_reg        <= 1'b0;
            done_latched          <= 1'b0;
            error_latched         <= 1'b0;
            error_code            <= 8'd0;
            npu_start_pulse       <= 1'b0;
            host_activation_we    <= 1'b0;
            host_activation_addr  <= '0;
            host_activation_data  <= '0;
            host_weight_we        <= 1'b0;
            host_weight_addr      <= '0;
            host_weight_data      <= '0;
            host_parameter_we     <= 1'b0;
            host_parameter_layer  <= '0;
            host_parameter_tile   <= '0;
            host_bias_data        <= '0;
            host_multiplier_data  <= '0;
            host_shift_data       <= '0;
            weight_low_stage      <= '0;
            weight_low_index      <= '0;
            weight_low_valid      <= 1'b0;
            param_bias_stage      <= '0;
            param_mult_stage      <= '0;
            param_shift_stage     <= '0;
            dma_src_reg           <= '0;
            dma_dst_reg           <= '0;
            dma_len_reg           <= '0;
            dma_irq_en_reg        <= 1'b0;
            dma_start_pulse       <= 1'b0;
            dma_clear_done_pulse  <= 1'b0;
        end else begin
            npu_start_pulse      <= 1'b0;
            host_activation_we   <= 1'b0;
            host_weight_we       <= 1'b0;
            host_parameter_we    <= 1'b0;
            dma_start_pulse      <= 1'b0;
            dma_clear_done_pulse <= 1'b0;

            if (npu_done)
                done_latched <= 1'b1;

            if (req_i && we_i) begin
                if (addr_off == 16'h0000) begin
                    irq_enable_reg <= wdata_i[1];
                    if (wdata_i[2])
                        done_latched <= 1'b0;
                    if (wdata_i[3]) begin
                        error_latched <= 1'b0;
                        error_code <= 8'd0;
                    end
                    if (wdata_i[0]) begin
                        if (npu_start_ready) begin
                            npu_start_pulse <= 1'b1;
                            done_latched <= 1'b0;
                        end else begin
                            error_latched <= 1'b1;
                            error_code <= (npu_busy || npu_start_pulse) ? 8'd2 : 8'd1;
                        end
                    end
                end else if (addr_off == 16'h0008) begin
                    if (npu_busy || npu_start_pulse) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd2;
                    end else if ((wdata_i[3:0] < 1) || (wdata_i[3:0] > 8)) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd1;
                    end else begin
                        class_count_reg <= wdata_i[3:0];
                    end
                end else if ((addr_off >= 16'h0400) && (addr_off <= 16'h0410)) begin
                    case (addr_off)
                        16'h0400: dma_src_reg <= wdata_i;
                        16'h0404: dma_dst_reg <= wdata_i;
                        16'h0408: dma_len_reg <= wdata_i;
                        16'h040c: begin
                            dma_start_pulse <= wdata_i[0];
                            dma_irq_en_reg <= wdata_i[1];
                        end
                        16'h0410: dma_clear_done_pulse <= wdata_i[1];
                        default: ;
                    endcase
                end else if (addr_in_input) begin
                    if (npu_busy || npu_start_pulse) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd2;
                    end else if (addr_off[1:0] != 2'b00) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd3;
                    end else begin
                        host_activation_addr <= input_index;
                        host_activation_data <= wdata_i[7:0];
                        host_activation_we <= 1'b1;
                    end
                end else if (addr_in_weight) begin
                    if (npu_busy || npu_start_pulse) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd2;
                    end else if (addr_off[1:0] != 2'b00) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd3;
                    end else if (!addr_off[2]) begin
                        weight_low_stage <= wdata_i;
                        weight_low_index <= weight_index;
                        weight_low_valid <= 1'b1;
                    end else if (!weight_low_valid || weight_low_index != weight_index) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd4;
                        weight_low_valid <= 1'b0;
                    end else begin
                        host_weight_addr <= weight_index;
                        host_weight_data <= {wdata_i, weight_low_stage};
                        host_weight_we <= 1'b1;
                        weight_low_valid <= 1'b0;
                    end
                end else if (addr_in_parameters) begin
                    if (npu_busy || npu_start_pulse) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd2;
                    end else if (addr_off[1:0] != 2'b00) begin
                        error_latched <= 1'b1;
                        error_code <= 8'd3;
                    end else if (parameter_suboffset <= 8'h1c) begin
                        param_bias_stage[parameter_lane_index*32 +: 32] <= wdata_i;
                    end else if ((parameter_suboffset >= 8'h20) &&
                                 (parameter_suboffset <= 8'h3c)) begin
                        param_mult_stage[parameter_lane_index*32 +: 32] <= wdata_i;
                    end else if ((parameter_suboffset >= 8'h40) &&
                                 (parameter_suboffset <= 8'h5c)) begin
                        param_shift_stage[parameter_lane_index*SHIFT_WIDTH +: SHIFT_WIDTH]
                            <= wdata_i[SHIFT_WIDTH-1:0];
                    end else if (parameter_suboffset == 8'h60) begin
                        if ((parameter_layer_index != 2'd3) && invalid_shift_stage) begin
                            error_latched <= 1'b1;
                            error_code <= 8'd5;
                        end else begin
                            host_parameter_layer <= parameter_layer_index;
                            host_parameter_tile <= 8'd0;
                            host_bias_data <= param_bias_stage;
                            host_multiplier_data <= param_mult_stage;
                            host_shift_data <= param_shift_stage;
                            host_parameter_we <= 1'b1;
                        end
                    end else begin
                        error_latched <= 1'b1;
                        error_code <= 8'd6;
                    end
                end else begin
                    error_latched <= 1'b1;
                    error_code <= 8'd6;
                end
            end
        end
    end

    logic [31:0] rdata_next;
    always_comb begin
        rdata_next = 32'd0;
        if (addr_off == 16'h0000)
            rdata_next = {30'd0, irq_enable_reg, 1'b0};
        else if (addr_off == 16'h0004)
            rdata_next = {24'd0, error_code[3:0], error_latched,
                          done_latched, npu_busy, npu_start_ready};
        else if (addr_off == 16'h0008)
            rdata_next = {28'd0, class_count_reg};
        else if (addr_off == 16'h000c)
            rdata_next = {24'd0, error_code};
        else if ((addr_off >= 16'h0010) && (addr_off <= 16'h002c))
            rdata_next = npu_logits[((addr_off - 16'h0010) >> 2)*32 +: 32];
        else if (addr_off == 16'h0030)
            rdata_next = 32'h0001_0001;
        // Read-only profile snapshot/live counters. 0x44..0x58 are
        // C1,P1,C2,P2,GAP,FC. Status: bit0 valid, bit1 overflow, bit2 active.
        // Writes fall through to existing unsupported-address error code 6.
        else if (addr_off == 16'h0040)
            rdata_next = perf_total_cycles;
        else if ((addr_off >= 16'h0044) && (addr_off <= 16'h0058) && addr_off[1:0]==0)
            rdata_next = perf_layer_cycles[((addr_off-16'h0044)>>2)*32 +: 32];
        else if (addr_off == 16'h005c)
            rdata_next = perf_weight_rows;
        else if (addr_off == 16'h0060)
            rdata_next = perf_matrix_issues;
        else if (addr_off == 16'h0064)
            rdata_next = perf_matrix_retires;
        else if (addr_off == 16'h0068)
            rdata_next = perf_peak_inflight;
        else if (addr_off == 16'h006c)
            rdata_next = {29'd0,npu_busy,perf_overflow,perf_valid};
        else if (addr_off == 16'h0400)
            rdata_next = dma_src_reg;
        else if (addr_off == 16'h0404)
            rdata_next = dma_dst_reg;
        else if (addr_off == 16'h0408)
            rdata_next = dma_len_reg;
        else if (addr_off == 16'h040c)
            rdata_next = {30'd0, dma_irq_en_reg, 1'b0};
        else if (addr_off == 16'h0410)
            rdata_next = {30'd0, dma_done_i, dma_busy_i};
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni)
            rdata_o <= 32'd0;
        else if (req_i && !we_i)
            rdata_o <= rdata_next;
    end
endmodule
