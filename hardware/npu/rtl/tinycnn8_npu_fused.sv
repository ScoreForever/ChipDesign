`timescale 1ns/1ps
// Fused TinyCNN-8-Flat core: a drop-in replacement for tinycnn8_npu_top with
// the same host ports, parameter ABI and profiler ports.
//
// Datapath per convolution layer:
//   1 KiB byte-banked activation store --4 B/cycle--> double-buffered 4xN
//   weight-stationary array --> output-side INT32 accumulation (tile of 16
//   positions) --> 2x2 max on INT32 --> one pipelined requant lane --> store.
//
// * Every layer uses K-major tiling: a weight group is loaded once per tile
//   of 16 positions (four 2x2 pooling windows) instead of once per position.
// * PEs keep two weight banks; the bank select travels with each operand, so
//   the next group is loaded while the current one is still computing.
// * Pooling happens before requantization. With a non-negative multiplier
//   the requant chain is monotonic, so max(requant(a..d)) equals
//   requant(max(a..d)) bit-exactly and only pooled values are quantized.
//   The MMIO wrapper rejects negative multipliers to keep that precondition.
// * Conv outputs are never stored; activation storage holds only the input
//   (320 B), Pool1 (640 B) and Pool2 (160 B, reusing the input bytes).
//
// Profiler lanes keep the layered ABI: C1 and C2 report the fused conv+pool
// stage, P1/P2 and the reserved lane stay zero, FC is lane 5.
module tinycnn8_npu_fused #(
    parameter ARRAY_ROWS=4,
    parameter ARRAY_COLS=8,
    parameter ACT_BANK_BYTES=4096,   // accepted for drop-in compatibility; unused
    parameter WEIGHT_WORDS=256,
    parameter ADDR_WIDTH=16,
    parameter SHIFT_WIDTH=6,
    parameter OPT_GATHER_LOAD=0,     // accepted for drop-in compatibility; unused
    parameter OPT_SPATIAL_TILE=0,    // accepted for drop-in compatibility; unused
    parameter SPATIAL_TILE=16        // accepted for drop-in compatibility; unused
) (
    input wire clk,input wire rst,
    input wire start_valid,output wire start_ready,
    input wire [3:0] class_count,
    output wire busy,output reg done,
    output reg [8*32-1:0] logits,
    input wire host_activation_we,
    input wire [ADDR_WIDTH-1:0] host_activation_addr,
    input wire [7:0] host_activation_data,
    input wire host_weight_we,
    input wire [ADDR_WIDTH-1:0] host_weight_addr,
    input wire [ARRAY_COLS*8-1:0] host_weight_data,
    input wire host_parameter_we,
    input wire [1:0] host_parameter_layer,
    input wire [7:0] host_parameter_tile,
    input wire [ARRAY_COLS*32-1:0] host_bias_data,
    input wire [ARRAY_COLS*32-1:0] host_multiplier_data,
    input wire [ARRAY_COLS*SHIFT_WIDTH-1:0] host_shift_data,
    output reg [31:0] perf_total_cycles,
    output reg [6*32-1:0] perf_layer_cycles,
    output reg [31:0] perf_weight_rows,
    output reg [31:0] perf_matrix_issues,
    output reg [31:0] perf_matrix_retires,
    output reg [31:0] perf_peak_inflight,
    output reg perf_overflow,
    output reg perf_valid
);
    generate if (ARRAY_ROWS != 4 || ARRAY_COLS < 1 || (8 % ARRAY_COLS) != 0)
    begin : invalid_shape
        initial $fatal(1, "tinycnn8_npu_fused requires ARRAY_ROWS=4 and ARRAY_COLS dividing 8");
    end endgenerate

    // ------------------------------------------------------------------
    // Fixed model geometry and storage layout.
    // ------------------------------------------------------------------
    localparam OCT_CONV=8/ARRAY_COLS;             // output-channel tiles per conv
    localparam CONV1_WEIGHT_BASE=0;
    localparam CONV2_WEIGHT_BASE=9*OCT_CONV;
    localparam FC_WEIGHT_BASE=CONV2_WEIGHT_BASE+72*OCT_CONV;
    localparam ACT_BYTES=1024;
    localparam ACT_ADDR_W=10;
    localparam ACT_BANKS=8;
    localparam ACT_ROWS=ACT_BYTES/ACT_BANKS;
    localparam INPUT_BASE=0, P1_BASE=320, P2_BASE=0;
    localparam TILE_POS=16;                       // four 2x2 windows
    localparam LATENCY=ARRAY_ROWS+ARRAY_COLS-1;
    localparam [1:0] PH_C1=0, PH_C2=1, PH_FC=2;
    // Tag carried through the array: position, first/last group, oc tile, tile.
    localparam TAG_W=5+1+1+4+5;

    reg running;
    reg [1:0] phase;
    reg [3:0] cfg_class_count;

    reg [ARRAY_COLS*8-1:0] weight_mem[0:WEIGHT_WORDS-1];
    reg [ARRAY_COLS*32-1:0] bias_conv1[0:OCT_CONV-1];
    reg [ARRAY_COLS*32-1:0] bias_conv2[0:OCT_CONV-1];
    reg [ARRAY_COLS*32-1:0] bias_fc[0:OCT_CONV-1];
    reg [ARRAY_COLS*32-1:0] mult_conv1[0:OCT_CONV-1];
    reg [ARRAY_COLS*32-1:0] mult_conv2[0:OCT_CONV-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_conv1[0:OCT_CONV-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_conv2[0:OCT_CONV-1];

    // Per-phase layer descriptor.
    wire [5:0] layer_groups = (phase==PH_C1) ? 6'd3 : (phase==PH_C2) ? 6'd18 : 6'd40;
    wire [4:0] layer_tiles = (phase==PH_C1) ? 5'd20 : (phase==PH_C2) ? 5'd5 : 5'd1;
    wire [4:0] layer_tile_pos = (phase==PH_FC) ? 5'd1 : TILE_POS[4:0];
    wire [3:0] fc_oct = (cfg_class_count + ARRAY_COLS - 1) / ARRAY_COLS;
    wire [3:0] layer_oct = (phase==PH_FC) ? fc_oct : OCT_CONV[3:0];
    wire layer_pool = (phase != PH_FC);
    wire [15:0] layer_weight_base = (phase==PH_C1) ? CONV1_WEIGHT_BASE :
        (phase==PH_C2) ? CONV2_WEIGHT_BASE : FC_WEIGHT_BASE;
    wire [ACT_ADDR_W-1:0] layer_dst = (phase==PH_C1) ? P1_BASE[ACT_ADDR_W-1:0] : P2_BASE[ACT_ADDR_W-1:0];

    // ------------------------------------------------------------------
    // Byte-banked activation store: one 4-byte read at any byte address and
    // one byte write per cycle. Consecutive bytes always hit distinct banks.
    // ------------------------------------------------------------------
    reg [ACT_ADDR_W-1:0] act_read_base;
    wire [7:0] act_bank_rdata[0:ACT_BANKS-1];
    reg act_we;
    reg [ACT_ADDR_W-1:0] act_waddr;
    reg [7:0] act_wdata;
    genvar bank;
    generate for (bank=0; bank<ACT_BANKS; bank=bank+1) begin : g_act_banks
        reg [7:0] mem[0:ACT_ROWS-1];
        wire [ACT_ADDR_W-4:0] raddr = act_read_base[ACT_ADDR_W-1:3] +
            (bank < act_read_base[2:0]);
        assign act_bank_rdata[bank] = mem[raddr];
        always @(posedge clk)
            if (act_we && act_waddr[2:0] == bank)
                mem[act_waddr[ACT_ADDR_W-1:3]] <= act_wdata;
    end endgenerate

    // ------------------------------------------------------------------
    // Weight loader: fills the idle PE bank for group instance m while
    // instance n issues. Instance parity selects the bank.
    // ------------------------------------------------------------------
    reg [15:0] issue_seq, load_seq;
    reg [1:0] bank_ready;
    reg [7:0] inflight[0:1];
    reg load_busy, load_more;
    reg [1:0] load_row;
    reg [5:0] load_g;
    reg [4:0] load_tile;
    reg [3:0] load_oct;
    wire load_bank = load_seq[0];
    wire load_start = running && load_more && !load_busy &&
        ((load_seq == issue_seq) || (load_seq == issue_seq + 1'b1)) &&
        !bank_ready[load_bank] && (inflight[load_bank] == 0);
    wire load_active = load_busy || load_start;
    wire [1:0] load_row_now = load_busy ? load_row : 2'd0;
    reg [15:0] load_k;
    reg [15:0] load_addr;
    reg [ARRAY_COLS*8-1:0] load_data;
    always @* begin
        load_k = (phase==PH_C1) ? (3*load_g + load_row_now) : (4*load_g + load_row_now);
        load_addr = layer_weight_base + load_k*layer_oct + load_oct;
        load_data = (phase==PH_C1 && load_row_now==2'd3) ? {ARRAY_COLS*8{1'b0}} :
                    weight_mem[load_addr];
    end

    // ------------------------------------------------------------------
    // Issue stage: one activation vector per cycle once its bank is loaded.
    // ------------------------------------------------------------------
    reg issue_more;
    reg [4:0] issue_p;
    reg [5:0] issue_g;
    reg [4:0] issue_tile;
    reg [3:0] issue_oct;
    reg [7:0] lastgrp_inflight;
    reg [3:0] win_ready;
    reg [1:0] rd_win;
    reg [3:0] rd_lane;
    reg [7:0] rq_count;
    wire issue_last_group = (issue_g == layer_groups - 1'b1);
    wire window_busy = (lastgrp_inflight != 0) || (win_ready != 0);
    wire issue_fire = running && issue_more && bank_ready[issue_seq[0]] &&
        !(layer_pool && issue_last_group && issue_p == 0 && window_busy);

    integer win_global, wy, wx, oy, ox, iy, ix, ky, kx, lane_i, base_calc;
    reg [3:0] act_lane_valid;
    reg [ARRAY_ROWS*8-1:0] issue_act;
    always @* begin
        win_global = issue_tile*4 + (issue_p>>2);
        act_lane_valid = 4'b0000;
        base_calc = 0;
        if (phase == PH_C1) begin
            wy = win_global >> 3; wx = win_global & 7;
            oy = 2*wy + issue_p[1]; ox = 2*wx + issue_p[0];
            iy = oy + issue_g - 1;
            base_calc = iy*16 + ox - 1;
            for (lane_i = 0; lane_i < 3; lane_i = lane_i + 1)
                act_lane_valid[lane_i] = (iy >= 0) && (iy < 20) &&
                    (ox - 1 + lane_i >= 0) && (ox - 1 + lane_i < 16);
        end else if (phase == PH_C2) begin
            wy = win_global >> 2; wx = win_global & 3;
            oy = 2*wy + issue_p[1]; ox = 2*wx + issue_p[0];
            ky = (issue_g >> 1) / 3; kx = (issue_g >> 1) % 3;
            iy = oy + ky - 1; ix = ox + kx - 1;
            base_calc = P1_BASE + (iy*8 + ix)*8 + (issue_g & 1)*4;
            act_lane_valid = ((iy >= 0) && (iy < 10) && (ix >= 0) && (ix < 8)) ? 4'b1111 : 4'b0000;
        end else begin
            base_calc = P2_BASE + 4*issue_g;
            act_lane_valid = 4'b1111;
        end
        act_read_base = base_calc[ACT_ADDR_W-1:0];
        for (lane_i = 0; lane_i < ARRAY_ROWS; lane_i = lane_i + 1)
            issue_act[lane_i*8 +: 8] = act_lane_valid[lane_i] ?
                act_bank_rdata[(act_read_base + lane_i) & 7] : 8'd0;
    end

    wire [TAG_W-1:0] issue_tag = {issue_tile, issue_oct, (issue_g == 0),
                                  issue_last_group, issue_p};

    wire out_valid, out_bank;
    wire [TAG_W-1:0] out_tag;
    wire [ARRAY_COLS*32-1:0] out_psum;
    fused_ws_array #(.ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS),
        .ACC_WIDTH(32),.TAG_WIDTH(TAG_W)) array(
        .clk(clk),.rst(rst),
        .weight_we(load_active),.weight_bank(load_bank),.weight_row(load_row_now),
        .weight_data(load_data),
        .in_valid(issue_fire),.in_bank(issue_seq[0]),.in_act(issue_act),.in_tag(issue_tag),
        .out_valid(out_valid),.out_bank(out_bank),.out_tag(out_tag),.out_psum(out_psum));

    // ------------------------------------------------------------------
    // Retire: output-side accumulation, INT32 2x2 max, FC logits.
    // ------------------------------------------------------------------
    wire [4:0] ret_p = out_tag[4:0];
    wire ret_last = out_tag[5];
    wire ret_first = out_tag[6];
    wire [3:0] ret_oct = out_tag[10:7];
    wire [4:0] ret_tile = out_tag[15:11];
    reg [ARRAY_COLS*32-1:0] psum_buf[0:TILE_POS-1];
    reg [ARRAY_COLS*32-1:0] win_buf[0:3];
    reg [4:0] win_tile[0:3];
    reg [3:0] win_oct[0:3];
    reg [ARRAY_COLS*32-1:0] ret_bias, ret_sum;
    integer col;
    always @* begin
        ret_bias = (phase==PH_C1) ? bias_conv1[ret_oct] :
                   (phase==PH_C2) ? bias_conv2[ret_oct] : bias_fc[ret_oct];
        for (col = 0; col < ARRAY_COLS; col = col + 1)
            ret_sum[col*32 +: 32] = (ret_first ? ret_bias[col*32 +: 32] :
                psum_buf[ret_p][col*32 +: 32]) + out_psum[col*32 +: 32];
    end

    // ------------------------------------------------------------------
    // Requant reader: one pooled lane per cycle into the pipelined lane.
    // ------------------------------------------------------------------
    wire rd_fire = running && win_ready[rd_win];
    wire [3:0] rd_oct = win_oct[rd_win];
    wire [ARRAY_COLS*32-1:0] rd_mult = (phase==PH_C1) ? mult_conv1[rd_oct] : mult_conv2[rd_oct];
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] rd_shift = (phase==PH_C1) ? shift_conv1[rd_oct] : shift_conv2[rd_oct];
    wire [ACT_ADDR_W-1:0] rd_addr = layer_dst +
        ((win_tile[rd_win]*4 + rd_win) * 8) + rd_oct*ARRAY_COLS + rd_lane;
    wire rq_out_valid;
    wire [7:0] rq_out_data;
    wire [ACT_ADDR_W-1:0] rq_out_addr;
    requant_lane #(.SHIFT_WIDTH(SHIFT_WIDTH),.TAG_WIDTH(ACT_ADDR_W)) requant(
        .clk(clk),.rst(rst),.in_valid(rd_fire),
        .in_value(win_buf[rd_win][rd_lane*32 +: 32]),
        .in_multiplier(rd_mult[rd_lane*32 +: 32]),
        .in_shift(rd_shift[rd_lane*SHIFT_WIDTH +: SHIFT_WIDTH]),
        .activation_min(8'sd0),.activation_max(8'sh7f),.in_tag(rd_addr),
        .out_valid(rq_out_valid),.out_data(rq_out_data),.out_tag(rq_out_addr));

    always @* begin
        act_we = 1'b0; act_waddr = 0; act_wdata = 0;
        if (rq_out_valid) begin
            act_we = 1'b1; act_waddr = rq_out_addr; act_wdata = rq_out_data;
        end else if (!running && host_activation_we && host_activation_addr < ACT_BYTES) begin
            act_we = 1'b1; act_waddr = host_activation_addr[ACT_ADDR_W-1:0];
            act_wdata = host_activation_data;
        end
    end

    // ------------------------------------------------------------------
    // Control.
    // ------------------------------------------------------------------
    wire [8:0] inflight_total = inflight[0] + inflight[1];
    wire layer_drained = !issue_more && (inflight_total == 0) && (win_ready == 0) &&
                         (rq_count == 0) && !rd_fire;
    assign start_ready = !running && !rst && (class_count >= 4'd1) && (class_count <= 4'd8);
    assign busy = running;

    task automatic begin_layer;
        begin
            issue_seq <= 0; load_seq <= 0; bank_ready <= 2'b00;
            load_busy <= 1'b0; load_more <= 1'b1; load_row <= 0;
            load_g <= 0; load_tile <= 0; load_oct <= 0;
            issue_more <= 1'b1; issue_p <= 0; issue_g <= 0; issue_tile <= 0; issue_oct <= 0;
            rd_win <= 0; rd_lane <= 0;
        end
    endtask

    integer lane;
    always @(posedge clk) begin
        if (rst) begin
            running <= 1'b0; phase <= PH_C1; done <= 1'b0; cfg_class_count <= 0; logits <= 0;
            issue_seq <= 0; load_seq <= 0; bank_ready <= 2'b00;
            inflight[0] <= 0; inflight[1] <= 0; lastgrp_inflight <= 0;
            load_busy <= 1'b0; load_more <= 1'b0; load_row <= 0;
            load_g <= 0; load_tile <= 0; load_oct <= 0;
            issue_more <= 1'b0; issue_p <= 0; issue_g <= 0; issue_tile <= 0; issue_oct <= 0;
            win_ready <= 0; rd_win <= 0; rd_lane <= 0; rq_count <= 0;
        end else begin
            done <= 1'b0;
            if (!running) begin
                if (host_weight_we) weight_mem[host_weight_addr] <= host_weight_data;
                if (host_parameter_we && host_parameter_tile < OCT_CONV) begin
                    case (host_parameter_layer)
                        0: begin bias_conv1[host_parameter_tile] <= host_bias_data;
                            mult_conv1[host_parameter_tile] <= host_multiplier_data;
                            shift_conv1[host_parameter_tile] <= host_shift_data; end
                        1: begin bias_conv2[host_parameter_tile] <= host_bias_data;
                            mult_conv2[host_parameter_tile] <= host_multiplier_data;
                            shift_conv2[host_parameter_tile] <= host_shift_data; end
                        2: begin end // reserved: removed GAP parameter group
                        3: bias_fc[host_parameter_tile] <= host_bias_data;
                    endcase
                end
                if (start_valid && start_ready) begin
                    running <= 1'b1; phase <= PH_C1; cfg_class_count <= class_count;
                    logits <= 0;
                    begin_layer();
                end
            end else begin
                // Weight loader.
                if (load_active) begin
                    if (load_row_now == ARRAY_ROWS-1) begin
                        load_busy <= 1'b0;
                        bank_ready[load_bank] <= 1'b1;
                        load_seq <= load_seq + 1'b1;
                        if (load_g != layer_groups - 1'b1) load_g <= load_g + 1'b1;
                        else begin
                            load_g <= 0;
                            if (load_tile != layer_tiles - 1'b1) load_tile <= load_tile + 1'b1;
                            else begin
                                load_tile <= 0;
                                if (load_oct != layer_oct - 1'b1) load_oct <= load_oct + 1'b1;
                                else load_more <= 1'b0;
                            end
                        end
                    end else begin
                        load_busy <= 1'b1;
                        load_row <= load_row_now + 1'b1;
                    end
                end

                // Issue counters.
                if (issue_fire) begin
                    if (issue_p != layer_tile_pos - 1'b1) issue_p <= issue_p + 1'b1;
                    else begin
                        issue_p <= 0;
                        bank_ready[issue_seq[0]] <= 1'b0;
                        issue_seq <= issue_seq + 1'b1;
                        if (!issue_last_group) issue_g <= issue_g + 1'b1;
                        else begin
                            issue_g <= 0;
                            if (issue_tile != layer_tiles - 1'b1) issue_tile <= issue_tile + 1'b1;
                            else begin
                                issue_tile <= 0;
                                if (issue_oct != layer_oct - 1'b1) issue_oct <= issue_oct + 1'b1;
                                else issue_more <= 1'b0;
                            end
                        end
                    end
                end

                // In-flight accounting per weight bank.
                inflight[0] <= inflight[0] + (issue_fire && !issue_seq[0]) - (out_valid && !out_bank);
                inflight[1] <= inflight[1] + (issue_fire && issue_seq[0]) - (out_valid && out_bank);
                lastgrp_inflight <= lastgrp_inflight +
                    (issue_fire && layer_pool && issue_last_group) -
                    (out_valid && layer_pool && ret_last);

                // Retire.
                if (out_valid) begin
                    if (!ret_last) begin
                        psum_buf[ret_p] <= ret_sum;
                    end else if (layer_pool) begin
                        for (lane = 0; lane < ARRAY_COLS; lane = lane + 1)
                            if (ret_p[1:0] == 2'd0 ||
                                $signed(ret_sum[lane*32 +: 32]) >
                                $signed(win_buf[ret_p[3:2]][lane*32 +: 32]))
                                win_buf[ret_p[3:2]][lane*32 +: 32] <= ret_sum[lane*32 +: 32];
                        if (ret_p[1:0] == 2'd3) begin
                            win_ready[ret_p[3:2]] <= 1'b1;
                            win_tile[ret_p[3:2]] <= ret_tile;
                            win_oct[ret_p[3:2]] <= ret_oct;
                        end
                    end else begin
                        for (lane = 0; lane < ARRAY_COLS; lane = lane + 1)
                            if (ret_oct*ARRAY_COLS + lane < cfg_class_count)
                                logits[(ret_oct*ARRAY_COLS + lane)*32 +: 32] <=
                                    ret_sum[lane*32 +: 32];
                    end
                end

                // Requant reader and pipeline occupancy.
                if (rd_fire) begin
                    if (rd_lane != ARRAY_COLS-1) rd_lane <= rd_lane + 1'b1;
                    else begin
                        rd_lane <= 0;
                        win_ready[rd_win] <= 1'b0;
                        rd_win <= rd_win + 1'b1;
                    end
                end
                rq_count <= rq_count + rd_fire - rq_out_valid;

                // Layer sequencing.
                if (layer_drained) begin
                    if (phase == PH_FC) begin
                        running <= 1'b0; done <= 1'b1;
                    end else begin
                        phase <= phase + 1'b1;
                        begin_layer();
                    end
                end
            end
        end
    end

    // ------------------------------------------------------------------
    // Profiler: same contract as the layered top. Lanes C1,P1,C2,P2,
    // reserved,FC; in the fused core P1/P2 are folded into C1/C2.
    // ------------------------------------------------------------------
    always @(posedge clk) begin : profiler
        integer layer;
        if (rst || (start_valid && start_ready)) begin
            perf_total_cycles<=0; perf_layer_cycles<=0; perf_weight_rows<=0;
            perf_matrix_issues<=0; perf_matrix_retires<=0; perf_peak_inflight<=0;
            perf_overflow<=0; perf_valid<=0;
        end else if (running) begin
            layer = (phase==PH_C1) ? 0 : (phase==PH_C2) ? 2 : 5;
            if (perf_total_cycles==32'hffffffff) perf_overflow<=1;
            else perf_total_cycles<=perf_total_cycles+1'b1;
            if (perf_layer_cycles[layer*32 +: 32]==32'hffffffff) perf_overflow<=1;
            else perf_layer_cycles[layer*32 +: 32]<=perf_layer_cycles[layer*32 +: 32]+1'b1;
            if (load_active) begin
                if (perf_weight_rows==32'hffffffff) perf_overflow<=1;
                else perf_weight_rows<=perf_weight_rows+1'b1;
            end
            if (issue_fire) begin
                if (perf_matrix_issues==32'hffffffff) perf_overflow<=1;
                else perf_matrix_issues<=perf_matrix_issues+1'b1;
            end
            if (out_valid) begin
                if (perf_matrix_retires==32'hffffffff) perf_overflow<=1;
                else perf_matrix_retires<=perf_matrix_retires+1'b1;
            end
            if ({23'd0,inflight_total} > perf_peak_inflight)
                perf_peak_inflight <= {23'd0,inflight_total};
            if (phase==PH_FC && layer_drained) perf_valid<=1;
        end
    end
endmodule
