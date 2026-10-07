`timescale 1ns/1ps
// Simple word-by-word AXI DMA engine.
//
// The DMA has a simple memory-like configuration slave (SRC/DST/LEN/CTRL/STATUS)
// and a simple memory-like master interface that is intended to connect to an
// axi_adapter (as used by the cv32e40p data/instr interfaces).
//
// Transfer semantics:
//   - copies LEN 32-bit words from SRC to DST
//   - single outstanding transaction
//   - BUSY while running, DONE set when finished
//   - irq_o = DONE & irq_en_i

module npu_dma #(
    parameter int ADDR_WIDTH = 32,
    parameter int DATA_WIDTH = 32
) (
    input  logic clk_i,
    input  logic rst_ni,

    // Configuration slave
    input  logic [ADDR_WIDTH-1:0] src_i,
    input  logic [ADDR_WIDTH-1:0] dst_i,
    input  logic [ADDR_WIDTH-1:0] len_i,
    input  logic                  start_i,
    input  logic                  irq_en_i,
    input  logic                  clear_done_i,
    output logic                  busy_o,
    output logic                  done_o,
    output logic                  irq_o,

    // Simple memory master (to axi_adapter)
    output logic                  req_o,
    output logic [ADDR_WIDTH-1:0] addr_o,
    output logic                  we_o,
    output logic [DATA_WIDTH/8-1:0] be_o,
    output logic [DATA_WIDTH-1:0]   wdata_o,
    input  logic                    gnt_i,
    input  logic                    valid_i,
    input  logic [DATA_WIDTH-1:0]   rdata_i
);

    localparam int BYTES_PER_WORD = DATA_WIDTH / 8;

    typedef enum logic [2:0] {
        DMA_IDLE,
        DMA_READ,
        DMA_READ_WAIT,
        DMA_WRITE,
        DMA_WRITE_WAIT,
        DMA_DONE
    } dma_state_t;

    dma_state_t state_d, state_q;
    logic [ADDR_WIDTH-1:0] src_q, dst_q;
    logic [ADDR_WIDTH-1:0] cnt_q;
    logic [DATA_WIDTH-1:0] data_q;
    logic                  done_q;

    assign busy_o = (state_q != DMA_IDLE) && (state_q != DMA_DONE);
    assign done_o = done_q;
    assign irq_o  = done_q && irq_en_i;
    assign be_o   = {BYTES_PER_WORD{1'b1}};

    // Next-state / output logic
    always_comb begin
        state_d = state_q;
        req_o   = 1'b0;
        we_o    = 1'b0;
        addr_o  = '0;
        wdata_o = data_q;

        case (state_q)
            DMA_IDLE: begin
                if (start_i && (len_i != '0))
                    state_d = DMA_READ;
            end

            DMA_READ: begin
                req_o  = 1'b1;
                we_o   = 1'b0;
                addr_o = src_q;
                if (gnt_i)
                    state_d = DMA_READ_WAIT;
            end

            DMA_READ_WAIT: begin
                if (valid_i)
                    state_d = DMA_WRITE;
            end

            DMA_WRITE: begin
                req_o   = 1'b1;
                we_o    = 1'b1;
                addr_o  = dst_q;
                wdata_o = data_q;
                if (gnt_i) begin
                    if (cnt_q == 1)
                        state_d = DMA_DONE;
                    else
                        state_d = DMA_WRITE_WAIT;
                end
            end

            DMA_WRITE_WAIT: begin
                if (valid_i)
                    state_d = DMA_READ;
            end

            DMA_DONE: begin
                state_d = DMA_IDLE;
            end

            default: state_d = DMA_IDLE;
        endcase
    end

    // State and datapath registers
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state_q <= DMA_IDLE;
            src_q   <= '0;
            dst_q   <= '0;
            cnt_q   <= '0;
            data_q  <= '0;
            done_q  <= 1'b0;
        end else begin
            if (clear_done_i)
                done_q <= 1'b0;

            state_q <= state_d;

            if (start_i && (len_i != '0)) begin
                src_q  <= src_i;
                dst_q  <= dst_i;
                cnt_q  <= len_i;
                done_q <= 1'b0;
            end else begin
                if (state_q == DMA_READ && gnt_i)
                    src_q <= src_q + BYTES_PER_WORD;

                if (state_q == DMA_WRITE && gnt_i) begin
                    dst_q <= dst_q + BYTES_PER_WORD;
                    cnt_q <= cnt_q - 1;
                end

                if (state_q == DMA_READ_WAIT && valid_i)
                    data_q <= rdata_i;
            end

            if (state_q == DMA_DONE)
                done_q <= 1'b1;
        end
    end

endmodule
