`timescale 1ns/1ps
// Fixed TinyCNN-8 layer sequencer with loadable model parameters. Conv1,
// Conv2, and FC share one conv2d_engine/Matrix Unit; FC selects INT32 output.
module tinycnn8_npu_top #(
    parameter ARRAY_ROWS=4,
    parameter ARRAY_COLS=8,
    parameter ACT_BANK_BYTES=4096,
    parameter WEIGHT_WORDS=256,
    parameter ADDR_WIDTH=16,
    parameter SHIFT_WIDTH=6,
    parameter OPT_GATHER_LOAD=0,
    parameter OPT_SPATIAL_TILE=0,
    parameter SPATIAL_TILE=16
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
    localparam CONV1_WEIGHT_BASE=0,CONV2_WEIGHT_BASE=32,FC_WEIGHT_BASE=192;
    // Bank activation storage by the low address bits. Eight consecutive
    // byte writes then target one location in each bank, which lets Vivado
    // infer distributed RAM instead of expanding each bank into flip-flops.
    localparam ACT_BANKS=8;
    localparam ACT_BANK_DEPTH=ACT_BANK_BYTES/ACT_BANKS;
    localparam ACT_BANK_ADDR_WIDTH=$clog2(ACT_BANK_DEPTH);
    localparam MAX_TILES=(8+ARRAY_COLS-1)/ARRAY_COLS;
    localparam [3:0] IDLE=0,C1_START=1,C1_WAIT=2,P1_START=3,P1_WAIT=4,
        C2_START=5,C2_WAIT=6,P2_START=7,P2_WAIT=8,GAP_START=9,GAP_WAIT=10,
        FC_START=11,FC_WAIT=12;
    reg [3:0] state;
    reg [3:0] cfg_class_count;
    reg [1:0] conv_phase;

    reg [7:0] act_a_bank_wdata[0:ACT_BANKS-1];
    reg [ACT_BANK_ADDR_WIDTH-1:0] act_a_bank_waddr[0:ACT_BANKS-1];
    reg [ACT_BANKS-1:0] act_a_bank_wen;
    reg [7:0] act_b_bank_wdata[0:ACT_BANKS-1];
    reg [ACT_BANK_ADDR_WIDTH-1:0] act_b_bank_waddr[0:ACT_BANKS-1];
    reg [ACT_BANKS-1:0] act_b_bank_wen;
    reg [15:0] act_a_read_base,act_b_read_base;
    wire [ACT_BANK_ADDR_WIDTH-1:0] act_a_bank_raddr[0:ACT_BANKS-1];
    wire [ACT_BANK_ADDR_WIDTH-1:0] act_b_bank_raddr[0:ACT_BANKS-1];
    wire [7:0] act_a_bank_rdata[0:ACT_BANKS-1];
    wire [7:0] act_b_bank_rdata[0:ACT_BANKS-1];
    reg [ARRAY_COLS*8-1:0] weight_mem[0:WEIGHT_WORDS-1];
    reg [ARRAY_COLS*32-1:0] bias_conv1[0:MAX_TILES-1];
    reg [ARRAY_COLS*32-1:0] bias_conv2[0:MAX_TILES-1];
    reg [ARRAY_COLS*32-1:0] bias_fc[0:MAX_TILES-1];
    reg [ARRAY_COLS*32-1:0] mult_conv1[0:MAX_TILES-1];
    reg [ARRAY_COLS*32-1:0] mult_conv2[0:MAX_TILES-1];
    reg [ARRAY_COLS*32-1:0] mult_gap[0:MAX_TILES-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_conv1[0:MAX_TILES-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_conv2[0:MAX_TILES-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_gap[0:MAX_TILES-1];

    // Each bank has one synchronous write port and one asynchronous read
    // port, the supported distributed-RAM pattern for this byte-wide store.
    genvar act_bank;
    generate for(act_bank=0;act_bank<ACT_BANKS;act_bank=act_bank+1)begin: g_act_banks
        reg [7:0] act_a_mem[0:ACT_BANK_DEPTH-1];
        reg [7:0] act_b_mem[0:ACT_BANK_DEPTH-1];
        assign act_a_bank_raddr[act_bank]=
            (act_a_read_base[15:3]+(act_bank<act_a_read_base[2:0]));
        assign act_b_bank_raddr[act_bank]=
            (act_b_read_base[15:3]+(act_bank<act_b_read_base[2:0]));
        assign act_a_bank_rdata[act_bank]=act_a_mem[act_a_bank_raddr[act_bank]];
        assign act_b_bank_rdata[act_bank]=act_b_mem[act_b_bank_raddr[act_bank]];
        always @(posedge clk)begin
            if(act_a_bank_wen[act_bank])
                act_a_mem[act_a_bank_waddr[act_bank]]<=act_a_bank_wdata[act_bank];
            if(act_b_bank_wen[act_bank])
                act_b_mem[act_b_bank_waddr[act_bank]]<=act_b_bank_wdata[act_bank];
        end
    end endgenerate

    wire conv_start_ready,conv_busy,conv_done;
    wire [ADDR_WIDTH-1:0] conv_act_addr,conv_weight_addr,conv_param_addr;
    reg [7:0] conv_act_data;
    wire [ARRAY_COLS*8-1:0] conv_weight_data=weight_mem[conv_weight_addr];
    reg [ARRAY_COLS*32-1:0] conv_bias_data,conv_mult_data;
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] conv_shift_data;
    wire conv_write_valid;
    wire [ADDR_WIDTH-1:0] conv_write_addr;
    wire [ARRAY_COLS*8-1:0] conv_write_data;
    wire [ARRAY_COLS-1:0] conv_write_mask;
    wire conv_i32_valid;
    wire [ADDR_WIDTH-1:0] conv_i32_addr;
    wire [ARRAY_COLS*32-1:0] conv_i32_data;
    wire [ARRAY_COLS-1:0] conv_i32_mask;
    wire conv_start_valid=(state==C1_START)||(state==C2_START)||(state==FC_START);
    wire [7:0] conv_ih=(conv_phase==0)?8'd20:(conv_phase==1)?8'd10:8'd1;
    wire [7:0] conv_iw=(conv_phase==0)?8'd16:(conv_phase==1)?8'd8:8'd1;
    wire [7:0] conv_ic=(conv_phase==0)?8'd1:8'd8;
    wire [7:0] conv_oh=conv_ih,conv_ow=conv_iw;
    wire [7:0] conv_oc=(conv_phase==2)?{4'd0,cfg_class_count}:8'd8;
    wire [7:0] conv_kernel=(conv_phase==2)?8'd1:8'd3;
    wire [7:0] conv_pad=(conv_phase==2)?8'd0:8'd1;
    wire [15:0] conv_weight_base=(conv_phase==0)?CONV1_WEIGHT_BASE:
        (conv_phase==1)?CONV2_WEIGHT_BASE:FC_WEIGHT_BASE;

    wire perf_activation_fire,perf_activation_padding,perf_weight_fire;
    wire perf_matrix_issue,perf_matrix_retire;
    wire [7:0] perf_inflight;
    // Conv and GAP execute in separate sequencer states and share one serial
    // quantizer to avoid duplicating the wide multiply/rounding datapath.
    wire conv_rq_in_valid,conv_rq_in_ready,conv_rq_out_valid,conv_rq_out_ready;
    wire [ARRAY_COLS-1:0] conv_rq_mask;
    wire [ARRAY_COLS*32-1:0] conv_rq_acc,conv_rq_multiplier;
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] conv_rq_shift;
    wire signed [31:0] conv_rq_offset;
    wire signed [7:0] conv_rq_min,conv_rq_max;
    wire gap_rq_in_valid,gap_rq_in_ready,gap_rq_out_valid,gap_rq_out_ready;
    wire [ARRAY_COLS-1:0] gap_rq_mask;
    wire [ARRAY_COLS*32-1:0] gap_rq_acc,gap_rq_multiplier;
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] gap_rq_shift;
    wire signed [31:0] gap_rq_offset;
    wire signed [7:0] gap_rq_min,gap_rq_max;
    wire [ARRAY_COLS*8-1:0] shared_rq_out_data;
    wire shared_rq_in_valid,shared_rq_in_ready,shared_rq_out_valid,shared_rq_out_ready;
    wire shared_rq_gap_active=(state==GAP_WAIT);
    wire [ARRAY_COLS-1:0] shared_rq_mask=shared_rq_gap_active?gap_rq_mask:conv_rq_mask;
    wire [ARRAY_COLS*32-1:0] shared_rq_acc=shared_rq_gap_active?gap_rq_acc:conv_rq_acc;
    wire [ARRAY_COLS*32-1:0] shared_rq_multiplier=
        shared_rq_gap_active?gap_rq_multiplier:conv_rq_multiplier;
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] shared_rq_shift=
        shared_rq_gap_active?gap_rq_shift:conv_rq_shift;
    wire signed [31:0] shared_rq_offset=shared_rq_gap_active?gap_rq_offset:conv_rq_offset;
    wire signed [7:0] shared_rq_min=shared_rq_gap_active?gap_rq_min:conv_rq_min;
    wire signed [7:0] shared_rq_max=shared_rq_gap_active?gap_rq_max:conv_rq_max;
    assign shared_rq_in_valid=shared_rq_gap_active?gap_rq_in_valid:conv_rq_in_valid;
    assign conv_rq_in_ready=shared_rq_in_ready&&!shared_rq_gap_active;
    assign gap_rq_in_ready=shared_rq_in_ready&&shared_rq_gap_active;
    assign conv_rq_out_valid=shared_rq_out_valid&&!shared_rq_gap_active;
    assign gap_rq_out_valid=shared_rq_out_valid&&shared_rq_gap_active;
    assign shared_rq_out_ready=shared_rq_gap_active?gap_rq_out_ready:conv_rq_out_ready;
    requant_unit #(.LANES(ARRAY_COLS),.SHIFT_WIDTH(SHIFT_WIDTH)) shared_requant(
        .clk(clk),.rst(rst),.in_valid(shared_rq_in_valid),.in_ready(shared_rq_in_ready),
        .lane_mask(shared_rq_mask),.acc_data(shared_rq_acc),
        .bias_data({ARRAY_COLS*32{1'b0}}),.multiplier_data(shared_rq_multiplier),
        .shift_data(shared_rq_shift),.output_offset(shared_rq_offset),
        .activation_min(shared_rq_min),.activation_max(shared_rq_max),
        .out_data(shared_rq_out_data),.out_valid(shared_rq_out_valid),
        .out_ready(shared_rq_out_ready));
    conv2d_engine #(.ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS),.EXTERNAL_REQUANT(1),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD),.OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
        .SPATIAL_TILE(SPATIAL_TILE)) conv(
        .clk(clk),.rst(rst),.start_valid(conv_start_valid),.start_ready(conv_start_ready),
        .input_height(conv_ih),.input_width(conv_iw),.input_channels(conv_ic),
        .output_height(conv_oh),.output_width(conv_ow),.output_channels(conv_oc),
        .kernel_height(conv_kernel),.kernel_width(conv_kernel),
        .stride_height(8'd1),.stride_width(8'd1),.pad_top(conv_pad),.pad_left(conv_pad),
        .activation_base(16'd0),.weight_base(conv_weight_base),.output_base(16'd0),
        .output_offset(32'sd0),.activation_min(conv_phase==2?8'sh80:8'sd0),
        .activation_max(8'sh7f),.final_output_int32(conv_phase==2),
        .busy(conv_busy),.done(conv_done),.activation_read_addr(conv_act_addr),
        .activation_read_data(conv_act_data),.weight_read_addr(conv_weight_addr),
        .weight_read_data(conv_weight_data),.parameter_tile_addr(conv_param_addr),
        .bias_read_data(conv_bias_data),.multiplier_read_data(conv_mult_data),
        .shift_read_data(conv_shift_data),.output_write_valid(conv_write_valid),
        .output_write_ready(1'b1),.output_write_addr(conv_write_addr),
        .output_write_data(conv_write_data),.output_write_mask(conv_write_mask),
        .int32_write_valid(conv_i32_valid),.int32_write_ready(1'b1),
        .int32_write_addr(conv_i32_addr),.int32_write_data(conv_i32_data),
        .int32_write_mask(conv_i32_mask),
        .perf_activation_fire(perf_activation_fire),
        .perf_activation_padding(perf_activation_padding),
        .perf_weight_fire(perf_weight_fire),.perf_matrix_issue(perf_matrix_issue),
        .perf_matrix_retire(perf_matrix_retire),.perf_inflight(perf_inflight),
        .requant_in_valid(conv_rq_in_valid),.requant_in_ready(conv_rq_in_ready),
        .requant_lane_mask(conv_rq_mask),.requant_acc_data(conv_rq_acc),
        .requant_multiplier_data(conv_rq_multiplier),.requant_shift_data(conv_rq_shift),
        .requant_output_offset(conv_rq_offset),.requant_activation_min(conv_rq_min),
        .requant_activation_max(conv_rq_max),.requant_out_data(shared_rq_out_data),
        .requant_out_valid(conv_rq_out_valid),.requant_out_ready(conv_rq_out_ready));

    wire pool_start_ready,pool_busy,pool_done,pool_write_valid;
    wire [ADDR_WIDTH-1:0] pool_act_addr,pool_write_addr;
    reg [ARRAY_COLS*8-1:0] pool_act_data;
    wire [ARRAY_COLS*8-1:0] pool_write_data;
    wire [ARRAY_COLS-1:0] pool_write_mask;
    wire pool_start_valid=(state==P1_START)||(state==P2_START);
    wire [7:0] pool_ih=(state==P1_START||state==P1_WAIT)?8'd20:8'd10;
    wire [7:0] pool_iw=(state==P1_START||state==P1_WAIT)?8'd16:8'd8;
    maxpool2x2_engine #(.LANES(ARRAY_COLS)) pool(
        .clk(clk),.rst(rst),.start_valid(pool_start_valid),.start_ready(pool_start_ready),
        .input_height(pool_ih),.input_width(pool_iw),.channels(8'd8),
        .input_base(16'd0),.output_base(16'd0),.busy(pool_busy),.done(pool_done),
        .activation_read_addr(pool_act_addr),.activation_read_data(pool_act_data),
        .output_write_valid(pool_write_valid),.output_write_ready(1'b1),
        .output_write_addr(pool_write_addr),.output_write_data(pool_write_data),
        .output_write_mask(pool_write_mask));

    wire gap_start_ready,gap_busy,gap_done,gap_write_valid;
    wire [ADDR_WIDTH-1:0] gap_act_addr,gap_param_addr,gap_write_addr;
    reg [ARRAY_COLS*8-1:0] gap_act_data;
    wire [ARRAY_COLS*8-1:0] gap_write_data;
    wire [ARRAY_COLS-1:0] gap_write_mask;
    global_avg_pool_engine #(.LANES(ARRAY_COLS),.EXTERNAL_REQUANT(1)) gap(
        .clk(clk),.rst(rst),.start_valid(state==GAP_START),.start_ready(gap_start_ready),
        .input_height(8'd5),.input_width(8'd4),.channels(8'd8),
        .input_base(16'd0),.output_base(16'd0),.output_offset(32'sd0),
        .activation_min(8'sh80),.activation_max(8'sh7f),.busy(gap_busy),.done(gap_done),
        .activation_read_addr(gap_act_addr),.activation_read_data(gap_act_data),
        .parameter_tile_addr(gap_param_addr),.multiplier_read_data(mult_gap[gap_param_addr]),
        .shift_read_data(shift_gap[gap_param_addr]),.output_write_valid(gap_write_valid),
        .output_write_ready(1'b1),.output_write_addr(gap_write_addr),
        .output_write_data(gap_write_data),.output_write_mask(gap_write_mask),
        .requant_in_valid(gap_rq_in_valid),.requant_in_ready(gap_rq_in_ready),
        .requant_lane_mask(gap_rq_mask),.requant_acc_data(gap_rq_acc),
        .requant_multiplier_data(gap_rq_multiplier),.requant_shift_data(gap_rq_shift),
        .requant_output_offset(gap_rq_offset),.requant_activation_min(gap_rq_min),
        .requant_activation_max(gap_rq_max),.requant_out_data(shared_rq_out_data),
        .requant_out_valid(gap_rq_out_valid),.requant_out_ready(gap_rq_out_ready));

    always @* begin : memory_read_mux
        integer lane;
        integer byte_addr;
        act_a_read_base=(state==GAP_WAIT)?gap_act_addr:conv_act_addr;
        act_b_read_base=((state==P1_WAIT)||(state==P2_WAIT))?pool_act_addr:conv_act_addr;
        conv_act_data=(conv_phase==2)?act_b_bank_rdata[conv_act_addr[2:0]]:
                                      act_a_bank_rdata[conv_act_addr[2:0]];
        conv_bias_data=0;conv_mult_data=0;conv_shift_data=0;
        if(conv_phase==0)begin conv_bias_data=bias_conv1[conv_param_addr];
            conv_mult_data=mult_conv1[conv_param_addr];conv_shift_data=shift_conv1[conv_param_addr];end
        else if(conv_phase==1)begin conv_bias_data=bias_conv2[conv_param_addr];
            conv_mult_data=mult_conv2[conv_param_addr];conv_shift_data=shift_conv2[conv_param_addr];end
        else conv_bias_data=bias_fc[conv_param_addr];
        pool_act_data=0;gap_act_data=0;
        for(lane=0;lane<ARRAY_COLS;lane=lane+1)begin
            byte_addr=pool_act_addr+lane;
            pool_act_data[lane*8 +: 8]=act_b_bank_rdata[byte_addr%ACT_BANKS];
            byte_addr=gap_act_addr+lane;
            gap_act_data[lane*8 +: 8]=act_a_bank_rdata[byte_addr%ACT_BANKS];
        end
    end

    // Form one write per bank. The host and pool paths on act_a are mutually
    // exclusive in the sequencer; conv and GAP writes on act_b are likewise
    // in separate states. Consecutive masked lane writes are distributed by
    // address across the banks.
    integer bank_i,lane_i,write_addr_i;
    always @* begin : activation_write_banks
        for(bank_i=0;bank_i<ACT_BANKS;bank_i=bank_i+1)begin
            act_a_bank_wen[bank_i]=0;
            act_a_bank_waddr[bank_i]=0;
            act_a_bank_wdata[bank_i]=0;
            act_b_bank_wen[bank_i]=0;
            act_b_bank_waddr[bank_i]=0;
            act_b_bank_wdata[bank_i]=0;
            if(host_activation_we&&state==IDLE)begin
                if((host_activation_addr%ACT_BANKS)==bank_i)begin
                    act_a_bank_wen[bank_i]=1;
                    act_a_bank_waddr[bank_i]=host_activation_addr/ACT_BANKS;
                    act_a_bank_wdata[bank_i]=host_activation_data;
                end
            end else if(pool_write_valid)begin
                for(lane_i=0;lane_i<ARRAY_COLS;lane_i=lane_i+1)begin
                    write_addr_i=pool_write_addr+lane_i;
                    if(pool_write_mask[lane_i]&&((write_addr_i%ACT_BANKS)==bank_i))begin
                        act_a_bank_wen[bank_i]=1;
                        act_a_bank_waddr[bank_i]=write_addr_i/ACT_BANKS;
                        act_a_bank_wdata[bank_i]=pool_write_data[lane_i*8 +: 8];
                    end
                end
            end
            if(conv_write_valid)begin
                for(lane_i=0;lane_i<ARRAY_COLS;lane_i=lane_i+1)begin
                    write_addr_i=conv_write_addr+lane_i;
                    if(conv_write_mask[lane_i]&&((write_addr_i%ACT_BANKS)==bank_i))begin
                        act_b_bank_wen[bank_i]=1;
                        act_b_bank_waddr[bank_i]=write_addr_i/ACT_BANKS;
                        act_b_bank_wdata[bank_i]=conv_write_data[lane_i*8 +: 8];
                    end
                end
            end else if(gap_write_valid)begin
                for(lane_i=0;lane_i<ARRAY_COLS;lane_i=lane_i+1)begin
                    write_addr_i=gap_write_addr+lane_i;
                    if(gap_write_mask[lane_i]&&((write_addr_i%ACT_BANKS)==bank_i))begin
                        act_b_bank_wen[bank_i]=1;
                        act_b_bank_waddr[bank_i]=write_addr_i/ACT_BANKS;
                        act_b_bank_wdata[bank_i]=gap_write_data[lane_i*8 +: 8];
                    end
                end
            end
        end
    end

    // The public result bus has eight lanes. Reject malformed jobs before
    // they can reach the FC engine or index beyond the logits register.
    assign start_ready=(state==IDLE)&&!rst&&
                       (class_count>=4'd1)&&(class_count<=4'd8);
    assign busy=(state!=IDLE);
    // Profiler contract: count every edge whose pre-edge sequencer state is
    // START or WAIT, including the engine-done transition edge. Accepted start
    // itself is excluded. Layer lanes (LSB first): C1,P1,C2,P2,GAP,FC; their sum
    // equals total absent saturation. Counters saturate at UINT32_MAX and set
    // sticky overflow on an attempted increment beyond it. Accepted start and
    // reset clear all fields; completion sets valid and freezes until next job.
    // Rejected starts and wrapper clear_done do not affect this snapshot.
    always @(posedge clk) begin : profiler
        integer layer;
        if(rst || (start_valid && start_ready))begin
            perf_total_cycles<=0;perf_layer_cycles<=0;perf_weight_rows<=0;
            perf_matrix_issues<=0;perf_matrix_retires<=0;perf_peak_inflight<=0;
            perf_overflow<=0;perf_valid<=0;
        end else if(busy)begin
            layer=(state-1)/2;
            if(perf_total_cycles==32'hffffffff)perf_overflow<=1;
            else perf_total_cycles<=perf_total_cycles+1'b1;
            if(perf_layer_cycles[layer*32 +: 32]==32'hffffffff)perf_overflow<=1;
            else perf_layer_cycles[layer*32 +: 32]<=perf_layer_cycles[layer*32 +: 32]+1'b1;
            if(perf_weight_fire)begin
                if(perf_weight_rows==32'hffffffff)perf_overflow<=1;
                else perf_weight_rows<=perf_weight_rows+1'b1;
            end
            if(perf_matrix_issue)begin
                if(perf_matrix_issues==32'hffffffff)perf_overflow<=1;
                else perf_matrix_issues<=perf_matrix_issues+1'b1;
            end
            if(perf_matrix_retire)begin
                if(perf_matrix_retires==32'hffffffff)perf_overflow<=1;
                else perf_matrix_retires<=perf_matrix_retires+1'b1;
            end
            if({24'd0,perf_inflight}>perf_peak_inflight)
                perf_peak_inflight<={24'd0,perf_inflight};
            if(state==FC_WAIT && conv_done)perf_valid<=1;
        end
    end

    always @(posedge clk) begin : sequencer_and_memory_writes
        integer lane;
        if(rst)begin state<=IDLE;done<=0;cfg_class_count<=0;conv_phase<=0;logits<=0;end
        else begin
            done<=0;
            if(host_weight_we&&state==IDLE)weight_mem[host_weight_addr]<=host_weight_data;
            if(host_parameter_we&&state==IDLE)begin
                case(host_parameter_layer)
                    0:begin bias_conv1[host_parameter_tile]<=host_bias_data;
                        mult_conv1[host_parameter_tile]<=host_multiplier_data;
                        shift_conv1[host_parameter_tile]<=host_shift_data;end
                    1:begin bias_conv2[host_parameter_tile]<=host_bias_data;
                        mult_conv2[host_parameter_tile]<=host_multiplier_data;
                        shift_conv2[host_parameter_tile]<=host_shift_data;end
                    2:begin mult_gap[host_parameter_tile]<=host_multiplier_data;
                        shift_gap[host_parameter_tile]<=host_shift_data;end
                    3:bias_fc[host_parameter_tile]<=host_bias_data;
                endcase
            end
            if(conv_i32_valid)
                for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                    if(conv_i32_mask[lane])logits[(conv_i32_addr+lane)*32 +: 32]<=
                        conv_i32_data[lane*32 +: 32];

            case(state)
                IDLE:if(start_valid&&start_ready)begin cfg_class_count<=class_count;
                    logits<=0;conv_phase<=0;state<=C1_START;end
                C1_START:if(conv_start_ready)state<=C1_WAIT;
                C1_WAIT:if(conv_done)state<=P1_START;
                P1_START:if(pool_start_ready)state<=P1_WAIT;
                P1_WAIT:if(pool_done)begin conv_phase<=1;state<=C2_START;end
                C2_START:if(conv_start_ready)state<=C2_WAIT;
                C2_WAIT:if(conv_done)state<=P2_START;
                P2_START:if(pool_start_ready)state<=P2_WAIT;
                P2_WAIT:if(pool_done)state<=GAP_START;
                GAP_START:if(gap_start_ready)state<=GAP_WAIT;
                GAP_WAIT:if(gap_done)begin conv_phase<=2;state<=FC_START;end
                FC_START:if(conv_start_ready)state<=FC_WAIT;
                FC_WAIT:if(conv_done)begin state<=IDLE;done<=1;end
                default:state<=IDLE;
            endcase
        end
    end
endmodule
