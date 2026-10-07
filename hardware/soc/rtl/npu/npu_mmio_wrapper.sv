`timescale 1ns/1ps
// MMIO wrapper that bridges a 32-bit memory interface to the ChipDesign NPU:
//   - Weight-Stationary Matrix Unit (matrix_unit)
//   - INT8 SIMD Vector Unit (vector_unit)
//
// The wrapper lives behind an axi2mem converter; each req_i pulse is one
// 32-bit read or write transaction.  Read data is combinational.
//
// Register map (offsets inside the 16 KiB NPU aperture):
//   0x0000  MATRIX_CTRL       [W] bit0=start compute, bit1=load weights
//   0x0004  MATRIX_STATUS     [R] bit0=idle, bit1=weights_loaded, bit2=out_valid
//   0x0008  VECTOR_CTRL       [W] {dst_sel, src_b_sel, src_a_sel, opcode}
//   0x000C  VECTOR_LANE_MASK  [W]
//   0x0010  VECTOR_SCALAR     [W] signed INT8
//   0x0014  VECTOR_STATUS     [R] bit0=out_valid
//   0x0018  VECTOR_OP         [W] trigger one vector operation
//   0x0020  VECTOR_SRC_A_LO   [W]
//   0x0024  VECTOR_SRC_A_HI   [W]
//   0x0028  VECTOR_SRC_B_LO   [W]
//   0x002C  VECTOR_SRC_B_HI   [W]
//   0x0030  VECTOR_OUT_LO     [R]
//   0x0034  VECTOR_OUT_HI     [R]
//   0x0040..0x005F  MATRIX_WEIGHT[0..7]  [W] weight staging (8 words)
//   0x0100          MATRIX_ACT           [W] 4 INT8 activations
//   0x0110..0x012F  MATRIX_PSUM[0..7]    [W] 8 INT32 partial sums
//   0x0200..0x021F  MATRIX_OUT[0..7]     [R] 8 INT32 outputs
//   0x0300  REQUANT_CTRL      [W] bit0=enable, bit1=copy_to_vec_src_a, bit2=copy_to_vec_src_b
//   0x0304  REQUANT_STATUS    [R] bit0=done
//   0x0310..0x032C  REQUANT_BIAS[0..7]    [W] signed INT32 per-lane bias
//   0x0330..0x034C  REQUANT_MULT[0..7]    [W] signed INT32 Q0.31 per-lane multiplier
//   0x0350  REQUANT_SHIFT     [W] signed 6-bit shift (positive=left, negative=right), broadcast
//   0x0354  REQUANT_OFFSET    [W] signed INT32 output offset
//   0x0360  REQUANT_OUT_LO    [R] lanes 0..3 INT8
//   0x0364  REQUANT_OUT_HI    [R] lanes 4..7 INT8
//   0x0400  DMA_SRC           [W] source address (byte)
//   0x0404  DMA_DST           [W] destination address (byte)
//   0x0408  DMA_LEN           [W] number of 32-bit words to copy
//   0x040C  DMA_CTRL          [W] bit0=start, bit1=irq_en
//   0x0410  DMA_STATUS        [R] bit0=busy, bit1=done

module npu_mmio_wrapper #(
    parameter ACT_WIDTH  = 8,
    parameter WGT_WIDTH  = 8,
    parameter ACC_WIDTH  = 32,
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8,
    parameter LANES      = 8,
    parameter DATA_WIDTH = 8
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        req_i,
    input  logic        we_i,
    input  logic [31:0] addr_i,
    input  logic [31:0] wdata_i,
    output logic [31:0] rdata_o,
    output logic        irq_o,

    // DMA config slave (decoded from NPU MMIO 0x0400..0x041F)
    output logic [31:0] dma_src_o,
    output logic [31:0] dma_dst_o,
    output logic [31:0] dma_len_o,
    output logic        dma_start_o,
    output logic        dma_irq_en_o,
    output logic        dma_clear_done_o,
    input  logic        dma_busy_i,
    input  logic        dma_done_i
);

    localparam int WEIGHT_WORDS = ((ARRAY_COLS * WGT_WIDTH + 31) / 32) * ARRAY_ROWS;
    localparam int PSUM_WORDS    = ARRAY_COLS;
    localparam int VEC_WORDS     = (LANES * DATA_WIDTH + 31) / 32;

    // -------------------------------------------------------------------------
    // Matrix Unit instance
    // -------------------------------------------------------------------------
    logic                                mu_weight_start_valid;
    logic                                mu_weight_start_ready;
    logic                                mu_weight_valid;
    logic                                mu_weight_ready;
    logic [ARRAY_COLS*WGT_WIDTH-1:0]     mu_weight_data;
    logic                                mu_weights_loaded;
    logic                                mu_idle;
    logic                                mu_in_valid;
    logic                                mu_in_ready;
    logic [ARRAY_ROWS*ACT_WIDTH-1:0]     mu_in_act_data;
    logic [ARRAY_COLS*ACC_WIDTH-1:0]     mu_in_psum_data;
    logic                                mu_out_valid;
    logic                                mu_out_ready;
    logic [ARRAY_COLS*ACC_WIDTH-1:0]     mu_out_psum_data;

    matrix_unit #(
        .ACT_WIDTH  (ACT_WIDTH),
        .WGT_WIDTH  (WGT_WIDTH),
        .ACC_WIDTH  (ACC_WIDTH),
        .ARRAY_ROWS (ARRAY_ROWS),
        .ARRAY_COLS (ARRAY_COLS)
    ) i_matrix_unit (
        .clk                (clk_i),
        .rst                (~rst_ni),
        .weight_start_valid (mu_weight_start_valid),
        .weight_start_ready (mu_weight_start_ready),
        .weight_valid       (mu_weight_valid),
        .weight_ready       (mu_weight_ready),
        .weight_data        (mu_weight_data),
        .weights_loaded     (mu_weights_loaded),
        .idle               (mu_idle),
        .in_valid           (mu_in_valid),
        .in_ready           (mu_in_ready),
        .in_act_data        (mu_in_act_data),
        .in_psum_data       (mu_in_psum_data),
        .out_valid          (mu_out_valid),
        .out_ready          (mu_out_ready),
        .out_psum_data      (mu_out_psum_data)
    );

    // -------------------------------------------------------------------------
    // Vector Unit instance
    // -------------------------------------------------------------------------
    logic [2:0]              vu_opcode;
    logic                    vu_src_a_sel;
    logic                    vu_src_b_sel;
    logic                    vu_dst_sel;
    logic [DATA_WIDTH-1:0]   vu_scalar;
    logic [LANES-1:0]        vu_lane_mask;
    logic [LANES*DATA_WIDTH-1:0] vu_vec_a;
    logic [LANES*DATA_WIDTH-1:0] vu_vec_b;
    logic [LANES*DATA_WIDTH-1:0] vu_vec_out;
    logic                    vu_out_valid;
    logic                    vu_out_ready;
    logic                    vu_in_valid;
    logic                    vu_in_ready;

    vector_unit #(
        .LANES      (LANES),
        .DATA_WIDTH (DATA_WIDTH)
    ) i_vector_unit (
        .clk         (clk_i),
        .rst         (~rst_ni),
        .in_valid    (vu_in_valid),
        .in_ready    (vu_in_ready),
        .opcode      (vu_opcode),
        .src_a_sel   (vu_src_a_sel),
        .src_b_sel   (vu_src_b_sel),
        .dst_sel     (vu_dst_sel),
        .scalar      (vu_scalar),
        .lane_mask   (vu_lane_mask),
        .vec_a       (vu_vec_a),
        .vec_b       (vu_vec_b),
        .vec_out     (vu_vec_out),
        .out_valid   (vu_out_valid),
        .out_ready   (vu_out_ready)
    );

    // -------------------------------------------------------------------------
    // MMIO registers
    // -------------------------------------------------------------------------
    logic [31:0] matrix_weight_regs [0:WEIGHT_WORDS-1];
    logic [31:0] matrix_psum_regs   [0:PSUM_WORDS-1];
    logic [31:0] matrix_act_reg;
    logic [31:0] matrix_out_regs    [0:PSUM_WORDS-1];
    logic        matrix_out_valid_latch;

    logic [5:0]  vec_ctrl_reg;     // {dst_sel, src_b_sel, src_a_sel, opcode}
    logic [LANES-1:0] vec_mask_reg;
    logic [DATA_WIDTH-1:0] vec_scalar_reg;
    logic [31:0] vec_src_a_regs [0:VEC_WORDS-1];
    logic [31:0] vec_src_b_regs [0:VEC_WORDS-1];
    logic [31:0] vec_out_regs   [0:VEC_WORDS-1];
    logic        vec_out_valid_latch;

    // Requantization registers (TFLite/gemmlowp double-rounding unit)
    localparam int REQUANT_SHIFT_WIDTH = 6;
    logic [2:0]  requant_ctrl_reg;       // {copy_to_vec_src_b, copy_to_vec_src_a, enable}
    logic        requant_done_latch;
    logic [31:0] requant_bias_regs [0:ARRAY_COLS-1];
    logic [31:0] requant_mult_regs  [0:ARRAY_COLS-1];
    logic [REQUANT_SHIFT_WIDTH-1:0] requant_shift_reg;
    logic [31:0] requant_offset_reg;
    logic [31:0] requant_out_regs [0:VEC_WORDS-1];

    // DMA config registers
    logic [31:0] dma_src_reg;
    logic [31:0] dma_dst_reg;
    logic [31:0] dma_len_reg;
    logic        dma_irq_en_reg;
    logic        dma_start_pulse;
    logic        dma_clear_done_pulse;

    // DMA output assignments
    assign dma_src_o         = dma_src_reg;
    assign dma_dst_o         = dma_dst_reg;
    assign dma_len_o         = dma_len_reg;
    assign dma_start_o       = dma_start_pulse;
    assign dma_irq_en_o      = dma_irq_en_reg;
    assign dma_clear_done_o  = dma_clear_done_pulse;

    // Assign vector unit control from registers
    assign vu_opcode    = vec_ctrl_reg[2:0];
    assign vu_src_a_sel = vec_ctrl_reg[3];
    assign vu_src_b_sel = vec_ctrl_reg[4];
    assign vu_dst_sel   = vec_ctrl_reg[5];
    assign vu_scalar    = vec_scalar_reg;
    assign vu_lane_mask = vec_mask_reg;

    genvar vg;
    generate
        for (vg = 0; vg < VEC_WORDS; vg = vg + 1) begin : gen_vec_pack
            assign vu_vec_a[vg*32 +: 32] = vec_src_a_regs[vg];
            assign vu_vec_b[vg*32 +: 32] = vec_src_b_regs[vg];
        end
    endgenerate

    // Requantization Unit interface signals (TFLite/gemmlowp double-rounding)
    logic [ARRAY_COLS*ACC_WIDTH-1:0]           requant_acc_i;
    logic [ARRAY_COLS*32-1:0]                  requant_bias_i;
    logic [ARRAY_COLS*32-1:0]                  requant_mult_i;
    logic [ARRAY_COLS*REQUANT_SHIFT_WIDTH-1:0] requant_shift_i;
    logic [ARRAY_COLS*DATA_WIDTH-1:0]          requant_out_o;
    logic                                      requant_valid_i;
    logic                                      requant_valid_o;

    // Pack per-lane bias / multiplier and broadcast shift into unit inputs.
    generate
        genvar rq;
        for (rq = 0; rq < ARRAY_COLS; rq = rq + 1) begin : gen_requant_pack
            assign requant_bias_i[rq*32 +: 32] = requant_bias_regs[rq];
            assign requant_mult_i[rq*32 +: 32] = requant_mult_regs[rq];
            assign requant_shift_i[rq*REQUANT_SHIFT_WIDTH +: REQUANT_SHIFT_WIDTH] = requant_shift_reg;
        end
    endgenerate

    assign requant_acc_i   = mu_out_psum_data;
    assign requant_valid_i = mu_out_valid && mu_out_ready && requant_ctrl_reg[0];

    requant_unit #(
        .LANES       (ARRAY_COLS),
        .SHIFT_WIDTH (REQUANT_SHIFT_WIDTH)
    ) i_requant_unit (
        .clk             (clk_i),
        .rst             (~rst_ni),
        .in_valid        (requant_valid_i),
        .in_ready        (),
        .lane_mask       ({ARRAY_COLS{1'b1}}),
        .acc_data        (requant_acc_i),
        .bias_data       (requant_bias_i),
        .multiplier_data (requant_mult_i),
        .shift_data      (requant_shift_i),
        .output_offset   (requant_offset_reg),
        .activation_min  (8'sh80),
        .activation_max  (8'sh7f),
        .out_data        (requant_out_o),
        .out_valid       (requant_valid_o),
        .out_ready       (1'b1)
    );

    // -------------------------------------------------------------------------
    // Matrix weight packing: 8 words -> 4 rows of 64 bits
    // -------------------------------------------------------------------------
    generate
        genvar r;
        for (r = 0; r < ARRAY_ROWS; r = r + 1) begin : gen_mu_wgt_row
            localparam int ROW_WORDS = (ARRAY_COLS * WGT_WIDTH + 31) / 32;
            localparam int BASE_WORD = r * ROW_WORDS;
            localparam int ROW_BITS  = ARRAY_COLS * WGT_WIDTH;
            logic [ROW_BITS-1:0] row_bits;
            always_comb begin
                row_bits = '0;
                for (int w = 0; w < ROW_WORDS; w = w + 1)
                    row_bits[w*32 +: 32] = matrix_weight_regs[BASE_WORD+w];
            end
            assign mu_weight_data[r*ROW_BITS +: ROW_BITS] = row_bits;
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Matrix activation / partial-sum / output packing
    // -------------------------------------------------------------------------
    assign mu_in_act_data = matrix_act_reg[ARRAY_ROWS*ACT_WIDTH-1:0];

    generate
        genvar c;
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : gen_mu_psum
            assign mu_in_psum_data[c*ACC_WIDTH +: ACC_WIDTH] = matrix_psum_regs[c];
        end
        for (c = 0; c < ARRAY_COLS; c = c + 1) begin : gen_mu_out
            always_ff @(posedge clk_i or negedge rst_ni) begin
                if (!rst_ni) begin
                    matrix_out_regs[c] <= '0;
                end else if (mu_out_valid && mu_out_ready) begin
                    matrix_out_regs[c] <= mu_out_psum_data[c*ACC_WIDTH +: ACC_WIDTH];
                end
            end
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Vector output packing / consumption
    // -------------------------------------------------------------------------
    generate
        for (vg = 0; vg < VEC_WORDS; vg = vg + 1) begin : gen_vec_out
            always_ff @(posedge clk_i or negedge rst_ni) begin
                if (!rst_ni) begin
                    vec_out_regs[vg] <= '0;
                end else if (vu_out_valid && vu_out_ready) begin
                    vec_out_regs[vg] <= vu_vec_out[vg*32 +: 32];
                end
            end
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Matrix control state machine
    // -------------------------------------------------------------------------
    typedef enum logic [2:0] {
        MU_IDLE,
        MU_WG_START,
        MU_WG_STREAM,
        MU_COMPUTE,
        MU_WAIT_OUT
    } mu_state_t;

    mu_state_t mu_state, mu_state_d;
    logic [$clog2(ARRAY_ROWS)-1:0] mu_row_cnt, mu_row_cnt_d;
    logic mu_start_load_d, mu_start_compute_d;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            mu_state       <= MU_IDLE;
            mu_row_cnt     <= '0;
        end else begin
            mu_state       <= mu_state_d;
            mu_row_cnt     <= mu_row_cnt_d;
        end
    end

    always_comb begin
        mu_state_d            = mu_state;
        mu_row_cnt_d          = mu_row_cnt;
        mu_weight_start_valid = 1'b0;
        mu_weight_valid       = 1'b0;
        mu_in_valid           = 1'b0;
        mu_out_ready          = 1'b0;

        case (mu_state)
            MU_IDLE: begin
                if (mu_start_load_d && mu_idle && !mu_weights_loaded) begin
                    mu_state_d = MU_WG_START;
                end else if (mu_start_compute_d && mu_idle && mu_weights_loaded) begin
                    mu_state_d = MU_COMPUTE;
                end
            end

            MU_WG_START: begin
                mu_weight_start_valid = 1'b1;
                if (mu_weight_start_ready) begin
                    mu_state_d   = MU_WG_STREAM;
                    mu_row_cnt_d = '0;
                end
            end

            MU_WG_STREAM: begin
                mu_weight_valid = 1'b1;
                if (mu_weight_ready) begin
                    if (mu_row_cnt == ARRAY_ROWS-1) begin
                        mu_state_d = MU_IDLE;
                    end else begin
                        mu_row_cnt_d = mu_row_cnt + 1'b1;
                    end
                end
            end

            MU_COMPUTE: begin
                mu_in_valid = 1'b1;
                if (mu_in_ready) begin
                    mu_state_d = MU_WAIT_OUT;
                end
            end

            MU_WAIT_OUT: begin
                if (mu_out_valid) begin
                    mu_out_ready = 1'b1;
                    mu_state_d   = MU_IDLE;
                end
            end

            default: mu_state_d = MU_IDLE;
        endcase
    end

    // -------------------------------------------------------------------------
    // Vector control state machine
    // -------------------------------------------------------------------------
    typedef enum logic [1:0] {
        VU_IDLE,
        VU_ISSUE,
        VU_WAIT_OUT
    } vu_state_t;

    vu_state_t vu_state, vu_state_d;
    logic vu_start_op_d;

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            vu_state <= VU_IDLE;
        end else begin
            vu_state <= vu_state_d;
        end
    end

    always_comb begin
        vu_state_d  = vu_state;
        vu_in_valid = 1'b0;
        vu_out_ready = 1'b0;

        case (vu_state)
            VU_IDLE: begin
                if (vu_start_op_d && vu_in_ready) begin
                    vu_state_d  = VU_ISSUE;
                    vu_in_valid = 1'b1;
                end
            end

            VU_ISSUE: begin
                // Output appears on the cycle after input acceptance.
                vu_state_d = VU_WAIT_OUT;
            end

            VU_WAIT_OUT: begin
                if (vu_out_valid) begin
                    vu_out_ready = 1'b1;
                    vu_state_d   = VU_IDLE;
                end
            end

            default: vu_state_d = VU_IDLE;
        endcase
    end

    // -------------------------------------------------------------------------
    // Output valid latches
    // -------------------------------------------------------------------------
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            matrix_out_valid_latch <= 1'b0;
            vec_out_valid_latch    <= 1'b0;
        end else begin
            if (mu_out_valid && mu_out_ready)
                matrix_out_valid_latch <= 1'b1;
            if (mu_start_compute_d)
                matrix_out_valid_latch <= 1'b0;

            if (vu_out_valid && vu_out_ready)
                vec_out_valid_latch <= 1'b1;
            if (vu_start_op_d)
                vec_out_valid_latch <= 1'b0;
        end
    end

    // -------------------------------------------------------------------------
    // Interrupt output: level-sensitive, active while any result is pending.
    // Cleared when software starts the next operation.
    // -------------------------------------------------------------------------
    assign irq_o = matrix_out_valid_latch | vec_out_valid_latch;

    // -------------------------------------------------------------------------
    // Address decode helpers
    // -------------------------------------------------------------------------
    logic [15:0] addr_off;
    logic        addr_in_weight, addr_in_psum, addr_in_out;
    logic        addr_in_vec_src_a, addr_in_vec_src_b;
    logic        addr_in_requant_bias, addr_in_requant_mult;

    assign addr_off = addr_i[15:0];

    assign addr_in_weight   = (addr_off[15:8] == 8'h00) && (addr_off[7:6] == 2'b01); // 0x0040-0x005F
    assign addr_in_psum     = (addr_off[15:8] == 8'h01) && (addr_off[7:5] == 3'b001); // 0x0110-0x012F
    assign addr_in_out      = (addr_off[15:8] == 8'h02) && (addr_off[7:5] == 3'b000); // 0x0200-0x021F
    assign addr_in_vec_src_a = (addr_off[15:4] == 12'h002); // 0x0020-0x002F
    assign addr_in_vec_src_b = (addr_off[15:4] == 12'h002); // overlaps, refined below
    assign addr_in_requant_bias = (addr_off >= 16'h0310) && (addr_off <= 16'h032C);
    assign addr_in_requant_mult  = (addr_off >= 16'h0330) && (addr_off <= 16'h034C);

    // Word indices within each register bank (offsets are byte addresses).
    logic [5:0] weight_idx, psum_idx, out_idx;
    logic [2:0] requant_bias_idx, requant_mult_idx;
    assign weight_idx = (addr_off - 16'h0040) >> 2;
    assign psum_idx   = (addr_off - 16'h0110) >> 2;
    assign out_idx    = (addr_off - 16'h0200) >> 2;
    assign requant_bias_idx = (addr_off - 16'h0310) >> 2;
    assign requant_mult_idx  = (addr_off - 16'h0330) >> 2;

    // -------------------------------------------------------------------------
    // MMIO write handling
    // -------------------------------------------------------------------------
    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            for (int i = 0; i < WEIGHT_WORDS; i = i + 1) matrix_weight_regs[i] <= '0;
            for (int i = 0; i < PSUM_WORDS;   i = i + 1) matrix_psum_regs[i]   <= '0;
            matrix_act_reg   <= '0;
            vec_ctrl_reg     <= '0;
            vec_mask_reg     <= '1;
            vec_scalar_reg   <= '0;
            for (int i = 0; i < VEC_WORDS; i = i + 1) begin
                vec_src_a_regs[i] <= '0;
                vec_src_b_regs[i] <= '0;
                requant_out_regs[i] <= '0;
            end
            requant_ctrl_reg   <= '0;
            requant_done_latch <= 1'b0;
            for (int i = 0; i < ARRAY_COLS; i = i + 1) begin
                requant_bias_regs[i] <= '0;
                requant_mult_regs[i]  <= '0;
            end
            requant_shift_reg  <= '0;
            requant_offset_reg <= '0;
            dma_src_reg        <= '0;
            dma_dst_reg        <= '0;
            dma_len_reg        <= '0;
            dma_irq_en_reg     <= 1'b0;
            dma_start_pulse    <= 1'b0;
            dma_clear_done_pulse <= 1'b0;
            mu_start_load_d    <= 1'b0;
            mu_start_compute_d <= 1'b0;
            vu_start_op_d      <= 1'b0;
        end else begin
            mu_start_load_d       <= 1'b0;
            mu_start_compute_d    <= 1'b0;
            vu_start_op_d         <= 1'b0;
            dma_start_pulse       <= 1'b0;
            dma_clear_done_pulse  <= 1'b0;

            if (req_i && we_i) begin
                casez (addr_off)
                    16'h0000: begin // MATRIX_CTRL
                        mu_start_compute_d <= wdata_i[0];
                        mu_start_load_d    <= wdata_i[1];
                    end
                    16'h0008: vec_ctrl_reg   <= wdata_i[5:0];
                    16'h000C: vec_mask_reg   <= wdata_i[LANES-1:0];
                    16'h0010: vec_scalar_reg <= wdata_i[DATA_WIDTH-1:0];
                    16'h0018: vu_start_op_d  <= 1'b1;
                    16'h0020: vec_src_a_regs[0] <= wdata_i;
                    16'h0024: if (VEC_WORDS > 1) vec_src_a_regs[1] <= wdata_i;
                    16'h0028: vec_src_b_regs[0] <= wdata_i;
                    16'h002C: if (VEC_WORDS > 1) vec_src_b_regs[1] <= wdata_i;
                    16'h0100: matrix_act_reg <= wdata_i;
                    16'h0300: requant_ctrl_reg <= wdata_i[2:0];
                    16'h0350: requant_shift_reg <= wdata_i[REQUANT_SHIFT_WIDTH-1:0];
                    16'h0354: requant_offset_reg <= wdata_i;
                    16'h0400: dma_src_reg   <= wdata_i;
                    16'h0404: dma_dst_reg   <= wdata_i;
                    16'h0408: dma_len_reg   <= wdata_i;
                    16'h040C: begin
                        dma_start_pulse  <= wdata_i[0];
                        dma_irq_en_reg   <= wdata_i[1];
                    end
                    16'h0410: dma_clear_done_pulse <= wdata_i[1];
                    default: begin
                        if (addr_in_psum)     matrix_psum_regs[psum_idx]   <= wdata_i;
                        if (addr_in_weight)   matrix_weight_regs[weight_idx] <= wdata_i;
                        if (addr_in_requant_bias) requant_bias_regs[requant_bias_idx] <= wdata_i;
                        if (addr_in_requant_mult)  requant_mult_regs[requant_mult_idx]  <= wdata_i;
                    end
                endcase
            end

            // Requantization result capture: the TFLite unit registers its
            // output one cycle after the Matrix Unit output is accepted, so
            // capture on the unit's out_valid. Optionally copy into the Vector
            // Unit source registers.
            if (mu_start_compute_d)
                requant_done_latch <= 1'b0;

            if (requant_valid_o) begin
                requant_out_regs[0] <= requant_out_o[31:0];
                if (VEC_WORDS > 1)
                    requant_out_regs[1] <= requant_out_o[63:32];
                requant_done_latch  <= 1'b1;

                if (requant_ctrl_reg[1]) begin
                    vec_src_a_regs[0] <= requant_out_o[31:0];
                    if (VEC_WORDS > 1)
                        vec_src_a_regs[1] <= requant_out_o[63:32];
                end
                if (requant_ctrl_reg[2]) begin
                    vec_src_b_regs[0] <= requant_out_o[31:0];
                    if (VEC_WORDS > 1)
                        vec_src_b_regs[1] <= requant_out_o[63:32];
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // MMIO read handling (registered, 1-cycle latency)
    //
    // axi2mem expects a memory-like read with stable data until the response is
    // accepted.  We register the read data on the request edge so rdata_o stays
    // valid even if the address bus changes before the AXI response completes.
    // -------------------------------------------------------------------------
    logic [31:0] rdata_reg;
    logic [31:0] rdata_next;

    always_comb begin
        rdata_next = 32'h0000_0000;
        if      (addr_off == 16'h0004) rdata_next = {29'b0, matrix_out_valid_latch, mu_weights_loaded, mu_idle};
        else if (addr_off == 16'h0014) rdata_next = {31'b0, vec_out_valid_latch};
        else if (addr_off == 16'h0030) rdata_next = vec_out_regs[0];
        else if (addr_off == 16'h0034) rdata_next = (VEC_WORDS > 1) ? vec_out_regs[1] : 32'b0;
        else if (addr_in_out)          rdata_next = matrix_out_regs[out_idx];
        else if (addr_off == 16'h0300) rdata_next = {29'b0, requant_ctrl_reg};
        else if (addr_off == 16'h0304) rdata_next = {31'b0, requant_done_latch};
        else if (addr_in_requant_bias) rdata_next = requant_bias_regs[requant_bias_idx];
        else if (addr_in_requant_mult)  rdata_next = requant_mult_regs[requant_mult_idx];
        else if (addr_off == 16'h0350) rdata_next = {{(32-REQUANT_SHIFT_WIDTH){requant_shift_reg[REQUANT_SHIFT_WIDTH-1]}}, requant_shift_reg};
        else if (addr_off == 16'h0354) rdata_next = requant_offset_reg;
        else if (addr_off == 16'h0360) rdata_next = requant_out_regs[0];
        else if (addr_off == 16'h0364) rdata_next = (VEC_WORDS > 1) ? requant_out_regs[1] : 32'b0;
        else if (addr_off == 16'h0400) rdata_next = dma_src_reg;
        else if (addr_off == 16'h0404) rdata_next = dma_dst_reg;
        else if (addr_off == 16'h0408) rdata_next = dma_len_reg;
        else if (addr_off == 16'h040C) rdata_next = {30'b0, dma_irq_en_reg, 1'b0};
        else if (addr_off == 16'h0410) rdata_next = {30'b0, dma_done_i, dma_busy_i};
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rdata_reg <= '0;
        end else if (req_i && !we_i) begin
            rdata_reg <= rdata_next;
        end
    end

    assign rdata_o = rdata_reg;

endmodule
