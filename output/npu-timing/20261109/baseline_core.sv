`timescale 1ns/1ps
// ABI 5.0 INT4 descriptor sequencer and shared seven-operator backend.
// No model geometry, layer index or output class count is hardwired.
// Model RAM is immutable while validating/running. Descriptors/model use byte addresses; activation ports use nibble addresses.
module npu_program_core #(
    parameter ACT_BYTES=8192, MODEL_BYTES=8192, COLS=8, ROWS=8, SHARED_MODEL=0,
`ifdef NPU_TILE
    parameter TILE=`NPU_TILE,
`else
    parameter TILE=16,
`endif
    parameter MAX_OPS=128, MAX_K=1024, PSUM_BITS=18
) (
    input wire clk,rst, input wire validate,run,
    input wire [15:0] model_bytes,input_base,input_bytes,output_base,output_bytes,
    input wire [7:0] op_count,
    input wire input_i32,
    output wire ready, output wire busy,
    output reg validation_done,validation_ok,done,
    output reg [4:0] error, output reg [7:0] error_op,
    output reg [31:0] cycles,issues,model_reads,act_reads,act_writes,
    output wire [24*32-1:0] perf_counters,
    output wire trace_clear,trace_active,
    output wire [127:0] trace_sample,
    output wire [3:0] trace_events,
    output wire [31:0] trace_descriptor,
    output reg m_valid, input wire m_ready, output reg [15:0] m_addr,
    input wire m_rsp_valid, output wire m_rsp_ready, input wire [31:0] m_data,
    output reg a_valid, input wire a_ready, output reg [15:0] a_addr,
    input wire a_rsp_valid, output wire a_rsp_ready, input wire [31:0] a_data,
    output wire w_valid, input wire w_ready, output wire [15:0] w_addr,
    output wire [31:0] w_data, output wire [7:0] w_mask,
    output wire memory_flush, input wire memory_idle
);
    localparam IDLE=0,FREQ=1,FWAIT=2,BASIC=3,CHECK=4,PREQ=5,PWAIT=6,
        SETUP=7,EXECUTE=8,NEXT=9,FAILED=10,INIT_A=11,INIT_B=12,
        LOOKUP_WAIT=13,DERIVE=14;
    localparam CONV=1,FC=2,POOL=3,RELU=4,REQUANT=5,AVGPOOL=6,ADD=7;
    localparam L_IDLE=0,L_REQ=1,L_WAIT=2,L_HAVE=3;
    reg [3:0] state;
    reg checking;
    reg [7:0] pc;
    reg [3:0] fetch_word;
    reg [31:0] d[0:15];
    // Full opcode is checked before execution. Cache the hot one-bit modes
    // rather than repeating wide opcode comparisons on memory address paths.
    wire [2:0] op=d[0][2:0];
    reg q_enable,relu_enable,pool_enable,mac_op,dwconv,pool1d,elem_pool;
    wire src32=d[0][11],dst32=d[0][12];
    // u4: 4-bit tensors holding unsigned 0..15 (post-ReLU activations).
    wire su=d[0][15],su2=d[0][16],du=d[0][17];
    wire [15:0] src={d[1][14:0],1'b0},dst={d[2][14:0],1'b0};
    // Upper halves of words 3 and 11.. and all reserved bits are checked as
    // each word is fetched (fetch_bad) and never stored for later use.
    wire [15:0] ih={8'd0,d[3][7:0]},iw={8'd0,d[3][23:16]};
    wire [15:0] ic=d[4][15:0],oc=d[4][31:16];
    wire [7:0] kh=d[5][7:0],kw=d[5][15:8],sh=d[5][23:16],sw=d[5][31:24];
    // Standalone pooling window/stride; an all-zero word is the ABI 3 2x2/s2 pool.
    wire pool_default=d[5]==0;
    wire [7:0] pkh=pool_default ? 8'd2 : kh,pkw=pool_default ? 8'd2 : kw,
               psh=pool_default ? 8'd2 : sh,psw=pool_default ? 8'd2 : sw;
    wire [15:0] src2={d[11][14:0],1'b0};
    wire signed [7:0] add_wa=d[12][7:0],add_wb=d[12][15:8];
    wire [7:0] pt=d[6][7:0],pl=d[6][15:8],pb=d[6][23:16],pr=d[6][31:24];
    wire [15:0] weights=d[7][15:0],params=d[8][15:0];
    wire signed [4:0] clamp_min=d[9][4:0],clamp_max=d[9][9:5];
    reg [15:0] oh,ow,dh,dw,k_count,groups,octs,row_stride;
    reg [31:0] src_span,dst_span,weight_span,param_span;
    reg [15:0] positions;
    reg [15:0] src_pixels,dst_pixels;
    // Descriptor/tile setup products share one 16x16 multiplier, one product
    // per cycle (DERIVE steps 0-7, SETUP steps 0-9) instead of ~20 parallel
    // multipliers that sat idle during execution.
    reg [3:0] astep;
    reg fetch_bad;
    reg [15:0] pool_chk_h,pool_chk_w,origin_y,origin_x;
    reg [15:0] mul_a,mul_b;
    wire [31:0] mul_p=mul_a*mul_b;
    reg lookup_done;
    wire scan_done,source_known,source2_known;
    wire [15:0] src_end=src+src_span[15:0],src2_end=src2+src_span[15:0];
    wire [15:0] dst_end=dst+dst_span[15:0];
    wire [15:0] input_first=input_base<<1,input_end=(input_base+input_bytes)<<1;
    // One 32-bit parameter word per channel: bias[17:0], Q8 multiplier[25:18],
    // shift[31:26]. INT4 accumulations never need wider bias or multiplier.
    reg [15:0] param_channel;
    reg signed [17:0] bias_cache[0:COLS-1];
    reg [7:0] mult_cache[0:COLS-1];
    reg signed [5:0] shift_cache[0:COLS-1];
    reg [15:0] oct;
    // Only checked channel counts (<=256) reach the execution path.
    wire [8:0] channels=mac_op ? oc[8:0] : ic[8:0];
    wire need_params=mac_op || q_enable;
    // IC=1 kernels up to eight taps wide: each kernel row is padded to one
    // eight-tap K group, so every group is a single contiguous activation read.
    wire rowpad=mac_op && !dwconv && ic==1 && kw<=ROWS && kh>1;
    wire [15:0] chb={7'd0,channels}<<(dst32 ? 3 : 0);
    wire [15:0] icb=ic<<(src32 ? 3 : 0);
    wire last_param=checking ? param_channel==channels-1 :
         (param_channel==channels-1 || (param_channel%COLS)==COLS-1);
    wire param_bad=m_data[31:26]==6'b100000 || (!q_enable && m_data[31:18]!=0) || (!mac_op && m_data[17:0]!=0);
    wire [15:0] next_param_channel=param_channel+1'b1;
    assign ready=state==IDLE && !rst;
    assign busy=state!=IDLE;
    assign m_rsp_ready=1'b1;
    assign a_rsp_ready=1'b1;

    // Validation uses bounded dimensions. Derived quantities are latched once
    // per descriptor; execution never recomputes tensor-size multiplications.
    integer xoh,xow,xdh,xdw,xk,xsrc,xdst,xweight,xparam;
    reg basic_bad,shape_bad,range_bad;
    wire output_bad=pc==op_count-1 && (dst!=(output_base<<1) || dst_span!=(output_bytes<<1));
    wire lookup_save=state==CHECK && lookup_done && source_known && source2_known && !output_bad;
    wire lookup_seed=state==IDLE && (validate || run);
    wire lookup_start=state==CHECK && !lookup_done && !shape_bad && !range_bad;
    (* keep_hierarchy = "yes" *) npu_tensor_lookup #(.MAX_OPS(MAX_OPS)) i_tensor_lookup (
        .clk(clk),.rst(rst),.seed(lookup_seed),.start(lookup_start),.save(lookup_save),.pc(pc),
        .input_i32(input_i32),.input_first(input_first),.input_end(input_end),
        .src_i32(src32),.src(src),.src_end(src_end),.add_op(op==ADD),.src2(src2),.src2_end(src2_end),
        .dst_i32(dst32),.dst(dst),.dst_end(dst_end),
        .done(scan_done),.source_known(source_known),.source2_known(source2_known)
    );
    always @* begin
        basic_bad=fetch_bad || d[0][7:0]<CONV || d[0][7:0]>ADD ||
            ih==0 || iw==0 || ic==0 || ic>MAX_K || oc==0 || oc>256 ||
            (su && src32) || (su2 && op!=ADD) || (du && dst32);
        if(mac_op) begin
            // Rectangular kernels up to 7x7. Depthwise convolution keeps one
            // input channel per output channel and whole COLS-wide tiles.
            basic_bad=basic_bad || src32 || (q_enable==dst32) ||
                kh==0 || kh>7 || kw==0 || kw>7 || (sh!=1 && sh!=2) || (sw!=1 && sw!=2) ||
                pt>=kh || pb>=kh || pl>=kw || pr>=kw || weights[1:0]!=0 || params[1:0]!=0 ||
                (pool1d && !pool_enable) || (dwconv && (op!=CONV || oc!=ic || oc%COLS!=0));
            if(op==FC) basic_bad=basic_bad || ih!=1 || iw!=1 || kh!=1 || kw!=1 || sh!=1 || sw!=1 || pool_enable;
        end else begin
            // Elementwise heads may fuse Requant/ReLU, never MAC-only modes.
            basic_bad=basic_bad || oc!=ic || d[0][14:13]!=0 || d[0][10] || d[6]!=0 || weights!=0 ||
                (need_params ? params[1:0]!=0 : params!=0) ||
                (!elem_pool && d[5]!=0) ||
                (elem_pool && !pool_default && (kh==0 || kw==0 || sh==0 || sw==0)) ||
                ((op==RELU || op==REQUANT) && d[0][9:8]!=0);
            case(op)
                REQUANT:basic_bad=basic_bad || dst32;
                RELU:basic_bad=basic_bad || src32!=dst32 || su!=du;
                POOL:basic_bad=basic_bad || (q_enable ? dst32 : (src32!=dst32 || su!=du));
                default:basic_bad=basic_bad || src32 || dst32==q_enable; // AvgPool/Add: i4 in, raw INT32
            endcase
        end
        if(q_enable) basic_bad=basic_bad || clamp_min>clamp_max || (relu_enable && clamp_max<0) ||
            (du ? clamp_min<0 : clamp_max>7);
        else basic_bad=basic_bad || d[9][9:0]!=0;
        xoh=ih; xow=iw; xk=ic;
        if(mac_op && sh!=0 && sw!=0) begin
            xoh=(($signed({1'b0,ih})+$signed({1'b0,pt})+$signed({1'b0,pb})-$signed({1'b0,kh})) >>> (sh==2))+1;
            xow=(($signed({1'b0,iw})+$signed({1'b0,pl})+$signed({1'b0,pr})-$signed({1'b0,kw})) >>> (sw==2))+1;
            // Row-padded single-channel kernels use one K group per kernel row.
            xk=rowpad ? kh[2:0]*ROWS : kh[2:0]*kw[2:0]*(dwconv ? COLS : ic);
        end
        // A standalone pool takes its output size from word 10; validate it
        // as the floor size instead of dividing by the stride.
        if(elem_pool) begin xoh=d[10][15:0]; xow=d[10][31:16]; end
        xdh=pool_enable && !pool1d ? xoh/2 : xoh;
        xdw=pool_enable ? xow/2 : xow;
        xsrc=src_span;
        xdst=dst_span;
        xweight=weight_span;
        xparam=param_span;
        shape_bad=xoh<1 || xoh>255 || xow<1 || xow>255 || xdh<1 || xdw<1 ||
            (mac_op && ($signed({1'b0,ih})+$signed({1'b0,pt})+$signed({1'b0,pb})<$signed({1'b0,kh}) ||
                         $signed({1'b0,iw})+$signed({1'b0,pl})+$signed({1'b0,pr})<$signed({1'b0,kw}) || xk>MAX_K)) ||
            (elem_pool && (ih<pkh || iw<pkw || pool_chk_h+pkh>ih || pool_chk_h+psh+pkh<=ih ||
                           pool_chk_w+pkw>iw || pool_chk_w+psw+pkw<=iw)) ||
            d[10]!={xdw[15:0],xdh[15:0]};
        range_bad=(SHARED_MODEL && (src+xsrc>(ACT_BYTES-model_bytes)*2 ||
            dst+xdst>(ACT_BYTES-model_bytes)*2 ||
            (op==ADD && src2+xsrc>(ACT_BYTES-model_bytes)*2))) || xsrc<1 || xsrc>ACT_BYTES*2 || xdst<1 || xdst>ACT_BYTES*2 ||
            $signed({1'b0,src})+xsrc>ACT_BYTES*2 || $signed({1'b0,dst})+xdst>ACT_BYTES*2 ||
            (mac_op && ($signed({1'b0,weights})+xweight>model_bytes || weights<op_count*64)) ||
            (need_params && ($signed({1'b0,params})+xparam>model_bytes || params<op_count*64)) ||
            (($signed({1'b0,src})<$signed({1'b0,dst})+xdst && $signed({1'b0,dst})<$signed({1'b0,src})+xsrc) &&
             !((op==RELU || op==ADD) && src==dst && xsrc==xdst)) ||
            // Elementwise ops read every element before writing it, so ReLU
            // and Add may run exactly in place (Add onto either operand).
            (op==ADD && ($signed({1'b0,src2})+xsrc>ACT_BYTES*2 ||
             (($signed({1'b0,src2})<$signed({1'b0,dst})+xdst && $signed({1'b0,dst})<$signed({1'b0,src2})+xsrc) &&
              !(src2==dst && xsrc==xdst))));
    end

    // Current output-channel tile caches are the only live parameter vectors.
    // Weight prefetch fills the idle array bank while the other bank issues.
    reg [1:0] lstate;
    reg [15:0] load_seq,issue_seq,load_g,issue_g,load_tile,issue_tile;
    reg [$clog2(ROWS)-1:0] load_row;
    reg load_more,issue_more;
    reg [COLS*4-1:0] load_data;
    reg [1:0] bank_ready;
    reg [4:0] inflight[0:1];
    wire load_bank=load_seq[0];
    // Packed rows use 4 bytes for eight INT4 lanes, 2 bytes for four.
    // An eight-column tail of <=4 channels also uses a 2-byte row.
    wire [15:0] tile_channels=channels-oct*COLS;
    wire narrow=COLS==8 && tile_channels<=4;
    wire [15:0] row_bytes=narrow ? 16'd2 : COLS/2;
    wire load_return=lstate==L_WAIT && m_rsp_valid;
    // Padded K rows still program zero into every PE, but consume no model
    // read bandwidth. This matters for eight-row groups and narrow kernels.
    wire load_zero=!dwconv && (rowpad ? load_row>=kw : load_g*ROWS+load_row>=k_count);
    wire next_load_zero=!dwconv && (rowpad ? load_row+1>=kw : load_g*ROWS+load_row+1>=k_count);
    wire array_weight_we=state==EXECUTE && mac_op && (lstate==L_HAVE || load_return || (lstate==L_REQ && load_zero)) && inflight[load_bank]==0;
    wire [COLS*4-1:0] response_row=narrow ? (m_data & 32'h0000ffff) : m_data;
    wire [COLS*4-1:0] loaded_row=load_zero ? 0 : load_return ? response_row : load_data;
    // Depthwise: one stored row per tap carries every lane's weight; group g
    // of the tap keeps lane (g%(COLS/4))*4+r in row r, a block diagonal.
    wire [$clog2(COLS)-1:0] dw_lane=(load_g*ROWS+load_row)%COLS;
    wire [COLS*4-1:0] array_weights=dwconv ? (loaded_row & ({{(COLS*4-4){1'b0}},4'hf}<<(dw_lane*4))) : loaded_row;
    reg [15:0] weight_tile_base,load_address;
    wire load_start=load_more && lstate==L_IDLE && !bank_ready[load_bank] &&
        (load_seq==issue_seq || load_seq==issue_seq+1'b1);

    // At most four completed vectors are resident. Reserve their slots before
    // issuing the final K group, including outputs still inside the MAC pipe.
    reg [COLS*PSUM_BITS-1:0] psum[0:TILE-1];
    reg [COLS*32-1:0] results[0:3];
    // Each completed slot keeps its output-position address dst+pos*channels;
    // positions complete in order, so it is a running sum, not a product.
    reg [15:0] result_addr[0:3];
    reg [15:0] ret_pos_addr,element_addr;
    reg [3:0] slot_busy,slot_ready;
    reg [1:0] read_slot;
    reg [$clog2(COLS)-1:0] read_lane;
    reg [4:0] rq_pending;
    // Reserve space for every quantization result before entering its fixed
    // four-stage pipeline. The output FIFO decouples all five operators from
    // activation-SRAM backpressure without stalling or dropping pipeline data.
    localparam WRITE_DEPTH=8;
    reg [15:0] write_addr[0:WRITE_DEPTH-1];
    reg [31:0] write_data[0:WRITE_DEPTH-1];
    reg [7:0] write_mask[0:WRITE_DEPTH-1];
    reg [2:0] write_head,write_tail;
    reg [3:0] write_count;
    wire write_fire=w_valid && w_ready;
    // Use registered occupancy to reserve space. Same-cycle write_ready
    // would feed SRAM arbitration back into the postprocessor's FIFO write.
    wire post_credit={1'b0,write_count}+rq_pending<WRITE_DEPTH;
    wire post_fire=state==EXECUTE && slot_ready[read_slot] && post_credit;
    wire [15:0] post_channel=oct*COLS+read_lane;
    // Nibble outputs drain two lanes per fire through two Requant lanes; the
    // adjacent nibbles leave as one masked write. INT32 outputs take one lane.
    wire pair=!dst32;
    wire [$clog2(COLS)-1:0] read_lane1=read_lane+1'b1;
    wire lane1_valid=pair && read_lane!=COLS-1 && post_channel+16'd1<channels;
    wire post_last=pair ? (read_lane>=COLS-2 || post_channel+16'd2>=channels) :
        (read_lane==COLS-1 || post_channel==channels-1);
    wire [31:0] post_raw=results[read_slot][read_lane*32+:32];
    wire [31:0] post_raw1=results[read_slot][read_lane1*32+:32];
    wire signed [31:0] post_value=relu_enable && !q_enable && $signed(post_raw)<0 ? 32'd0 : post_raw;
    wire [3:0] post_nibble1=relu_enable && !q_enable && post_raw1[31] ? 4'd0 : post_raw1[3:0];
    wire [15:0] post_addr=result_addr[read_slot]+(post_channel<<(dst32 ? 3 : 0));
    // Execution only follows range validation: the padded end fits the
    // 16-bit nibble address space, so no 32-bit adder is needed here.
    wire [15:0] packed_tail_addr=dst+dst_span[15:0]-16'd2;
    wire rq_valid,rq_pair,rq1_valid; wire [3:0] rq_data,rq1_data; wire [15:0] rq_addr;
    wire signed [4:0] effective_min=relu_enable && clamp_min<0 ? 5'sd0 : clamp_min;
    requant_lane #(.TAG_WIDTH(17)) i_requant (
        .clk(clk),.rst(rst),.in_valid(post_fire && q_enable),.in_value(post_raw),
        .in_multiplier(mult_cache[read_lane]),.in_shift(shift_cache[read_lane]),
        .activation_min(effective_min),.activation_max(clamp_max),.in_tag({lane1_valid,post_addr}),
        .out_valid(rq_valid),.out_data(rq_data),.out_tag({rq_pair,rq_addr}));
    requant_lane #(.TAG_WIDTH(1)) i_requant1 (
        .clk(clk),.rst(rst),.in_valid(post_fire && q_enable && lane1_valid),.in_value(post_raw1),
        .in_multiplier(mult_cache[read_lane1]),.in_shift(shift_cache[read_lane1]),
        .activation_min(effective_min),.activation_max(clamp_max),.in_tag(1'b0),
        .out_valid(rq1_valid),.out_data(rq1_data),.out_tag());
    wire write_push=state==EXECUTE && (rq_valid || (post_fire && !q_enable));
    // Write nibbles: one or two adjacent results; an odd tensor's final nibble
    // also clears the byte's padding nibble.
    wire write_two=q_enable ? rq_pair : lane1_valid;
    wire [15:0] write_at=q_enable ? rq_addr : post_addr;
    wire write_tail_pad=dst_pixels[0] && channels[0] && write_at+write_two==packed_tail_addr;
    assign w_valid=state==EXECUTE && write_count!=0;
    assign w_addr=write_addr[write_head];
    assign w_data=write_data[write_head];
    assign w_mask=write_mask[write_head];

    // Gather one address-consecutive subset per read. Irregular channel
    // groups may cross kernel rows: consume each subset once instead of
    // reading every lane separately. gather_mask tracks lanes still pending.
    reg [4:0] issue_p;
    reg [1:0] gather_state; reg [$clog2(ROWS)-1:0] gather_row;
    reg [ROWS*4-1:0] gathered;
    reg [15:0] gather_addr[0:ROWS-1];
    reg [ROWS-1:0] gather_mask;
    reg [ROWS*ROWS-1:0] k_consecutive;
    reg [ROWS-1:0] gather_chunk,gather_remaining;
    reg [$clog2(ROWS)-1:0] gather_next;
    reg [ROWS*4-1:0] returned_vector;
    wire gather_return=gather_state==2 && a_rsp_valid && gather_remaining==0;
    wire gather_full=gather_state==3 || gather_return;
    integer gv;
    always @* begin
        gather_chunk=0;
        for(gv=0;gv<ROWS;gv=gv+1)
            gather_chunk[gv]=gather_mask[gv] && gv>=gather_row &&
                k_consecutive[gather_row*ROWS+gv];
        gather_remaining=gather_mask & ~gather_chunk;
        gather_next=0;
        for(gv=ROWS-1;gv>=0;gv=gv-1)
            if(gather_remaining[gv])gather_next=gv;
        returned_vector=gathered;
        for(gv=0;gv<ROWS;gv=gv+1)
            if(gather_chunk[gv])returned_vector[gv*4+:4]=a_data>>((gv-gather_row)*4);
    end
    wire [31:0] remaining=positions-issue_tile*TILE;
    reg [4:0] tile_positions;
    reg last_group;
    // Fused pooling: 2x2 windows take four positions per output, 1x2 two.
    // Position tag: with TILE=8 the spare top bit carries the tile parity, so a
    // 2x2 pool's slot index (bits 3:2) is still the output number modulo four.
    wire [3:0] tag_pos=TILE==16 ? issue_p[3:0] : {issue_tile[0],issue_p[2:0]};
    wire [1:0] issue_slot=!pool_enable ? tag_pos[1:0] : pool1d ? tag_pos[2:1] : tag_pos[3:2];
    wire reserve_slot=!pool_enable || (pool1d ? !issue_p[0] : issue_p[1:0]==0);
    wire pool_tap_last=pool1d ? issue_p[0] : issue_p[1:0]==3;
    wire slot_available=!last_group || !reserve_slot || !slot_busy[issue_slot];
    wire issue_can_fire=mac_op && issue_more && gather_full && bank_ready[issue_seq[0]] && slot_available;
    wire issue_fire=state==EXECUTE && issue_can_fire;
    // First and last flags plus position (six bits total).
    wire [5:0] in_tag={issue_g==0,last_group,tag_pos};
    wire out_valid,out_bank; wire [5:0] out_tag; wire [COLS*11-1:0] out_sum;
    npu_mac_array #(.ARRAY_ROWS(ROWS),.ARRAY_COLS(COLS),.ACC_WIDTH(11),.TAG_WIDTH(6)) i_array (
        .clk(clk),.rst(rst),.weight_we(array_weight_we),.weight_bank(load_bank),
        .weight_row(load_row),.weight_data(array_weights),.in_valid(issue_fire),
        .in_bank(issue_seq[0]),.in_act(gather_return ? returned_vector : gathered),.in_unsigned(su),.in_tag(in_tag),
        .out_valid(out_valid),.out_bank(out_bank),.out_tag(out_tag),.out_psum(out_sum));
    wire [3:0] ret_p=out_tag[3:0];
    wire [$clog2(TILE)-1:0] psum_index=ret_p[$clog2(TILE)-1:0];
    wire ret_last=out_tag[4],ret_first=out_tag[5];
    wire [1:0] ret_slot=!pool_enable ? ret_p[1:0] : pool1d ? ret_p[2:1] : ret_p[3:2];
    wire ret_tap_first=pool1d ? !ret_p[0] : ret_p[1:0]==0;
    wire ret_tap_last=pool1d ? ret_p[0] : ret_p[1:0]==3;
    reg signed [PSUM_BITS-1:0] reduced;
    reg signed [31:0] biased;
    integer lane;
    // Layer setup computes strides; group setup computes eight kernel offsets.
    // The issue path contains only bounded additions/comparisons, no multipliers.
    reg [7:0] cursor_y,cursor_x,tile_y,tile_x;
    // Keep the row-end comparison off the gather/address path. The cursor
    // advances only on issue_fire, so this flag can be updated with it.
    reg cursor_at_last_x;
    reg [7:0] scan_last_x,element_last_x;
    reg [10:0] kernel_c;
    reg [2:0] kernel_y,kernel_x;
    reg [15:0] kernel_offset,k_offsets[0:ROWS-1],cursor_base,tile_base;
    reg [8:0] k_y_begin[0:ROWS-1],k_y_end[0:ROWS-1];
    reg [8:0] k_x_begin[0:ROWS-1],k_x_end[0:ROWS-1];
    reg [ROWS-1:0] k_valid;
    // ceil((extent + padding - tap) / stride), clamped at zero. Extent zero
    // gives the first valid output coordinate; the real extent gives its end.
    // Compute these bounds at K-group setup instead of subtracting padding
    // and adding each tap on every activation request.
    function automatic [8:0] coordinate_bound(
        input [7:0] extent,input [2:0] padding,tap,input stride_two
    );
        reg signed [10:0] delta;
        begin
            delta=$signed({3'd0,extent})+$signed({8'd0,padding})-$signed({8'd0,tap});
            coordinate_bound=delta<=0 ? 9'd0 : (delta+stride_two)>>stride_two;
        end
    endfunction
    // Equal row-normalized offsets are contiguous even when the spatial
    // boundary masks out some rows. Compare the registered offsets once.
    reg [15:0] k_normalized[0:ROWS-1];
    reg kernel_prepare;
    reg [$clog2(ROWS):0] prep_row;
    reg [15:0] x_step,y_step,origin,kernel_row_jump,kernel_col_jump;
    // All scan/pool increments are configured once per channel tile. The
    // activation request path selects a registered delta then adds it once.
    reg [15:0] cursor_step[0:7];
    // Depthwise kernels walk only the COLS channels of the current tile per tap.
    wire [10:0] kernel_ic=dwconv ? COLS : ic[10:0];
    reg [31:0] scan_product,pool_scan_product;
    wire [$clog2(ROWS)-1:0] prep_index=prep_row[$clog2(ROWS)-1:0];
    wire [15:0] prep_row_ext={{(16-$clog2(ROWS)){1'b0}},prep_index};
    wire prep_end_channels=kernel_c==kernel_ic-11'd1;
    wire prep_end_column=kernel_x==kw[2:0]-3'd1;
    wire [15:0] prep_step=prep_end_channels ?
        (prep_end_column ? kernel_row_jump : kernel_col_jump) : 16'd1;
    // These groups stay within one kernel tap, so all row addresses are a
    // constant offset apart. Irregular channel counts retain the serial walk.
    wire prep_contiguous=rowpad || op==FC || (kernel_ic%ROWS)==0;
    wire prep_group_end=kernel_c==kernel_ic-ROWS;
    reg [9:0] cy,cx;
    reg [4:0] calc_p;
    reg [8:0] calc_y,calc_x,next_y,next_x;
    reg [15:0] calc_base,next_base;
    integer r,rr;
    reg [15:0] calc_addr[0:ROWS-1];reg [ROWS-1:0] calc_mask;reg calc_contiguous;
    reg [$clog2(ROWS)-1:0] calc_first;
    // Position and tile length change only at group setup or issue_fire.
    // Register the lookahead predicate so issue_p's terminal-count compare
    // does not precede the gather mask/address and activation forwarding path.
    reg has_next_q;
    wire has_next=has_next_q;
    always @* begin
        next_y=cursor_y;next_x=cursor_x;next_base=cursor_base;
        if(!pool_enable || pool_tap_last)begin
            if(cursor_at_last_x)begin next_x=0;next_y=cursor_y+1;end
            else next_x=cursor_x+1;
        end
        next_base=cursor_base+cursor_step[{cursor_at_last_x,issue_p[1:0]}];
        calc_p=issue_p;calc_y=cursor_y;calc_x=cursor_x;calc_base=cursor_base;
        // Once a gather has started, its metadata lives in gather_* until
        // issue_fire. Prepare the next position throughout that interval,
        // rather than selecting it from the returning activation response.
        // Only issue_fire consumes this lookahead. This keeps SRAM response
        // valid -> issue_fire out of the address/mask/contiguity arithmetic,
        // while retaining same-cycle response/issue/next-request throughput.
        if(gather_state!=0 && has_next)begin calc_p=issue_p+1;calc_y=next_y;calc_x=next_x;calc_base=next_base;end
        if(pool_enable)begin cy=pool1d ? calc_y : calc_y*2+calc_p[1];cx=calc_x*2+calc_p[0];end
        else begin cy=calc_y;cx=calc_x;end
        calc_mask=0;
        for(r=0;r<ROWS;r=r+1)begin
            calc_addr[r]=calc_base+k_offsets[r];
            calc_mask[r]=k_valid[r] && cy>=k_y_begin[r] && cy<k_y_end[r] &&
                cx>=k_x_begin[r] && cx<k_x_end[r];
        end
        calc_first=0;
        for(r=ROWS-1;r>=0;r=r-1)if(calc_mask[r])calc_first=r;
        // All selected pairs must preserve their nibble-lane distance. Empty
        // and singleton masks are contiguous, including masked padding lanes.
        calc_contiguous=1;
        for(r=0;r<ROWS;r=r+1)for(rr=0;rr<ROWS;rr=rr+1)
            if(r<rr && calc_mask[r] && calc_mask[rr] && !k_consecutive[r*ROWS+rr])calc_contiguous=0;
    end

    // Standalone operators feed the same four result slots and postprocessor.
    // Pools walk a kh x kw window per lane (max or INT32 sum); Add reads
    // its two i4 operands in turn and forms wa*a+wb*b.
    reg [2:0] estate;
    reg [15:0] element_pos;
    reg [$clog2(COLS)-1:0] element_lane;
    reg [1:0] element_slot;
    reg [7:0] tap_y,tap_x;
    reg [15:0] tap_offset,tap_row_jump,tap_col_step;
    reg add_second;
    reg [7:0] element_y,element_x;
    reg [15:0] element_base,element_base2,element_col_step,element_row_step,element_row_jump;
    reg [31:0] element_scan_product;
    reg signed [31:0] element_acc;
    wire signed [31:0] element_value=src32 ? $signed(a_data) : $signed({{28{a_data[3] && !su}},a_data[3:0]});
    wire tap_first=tap_y==0 && tap_x==0;
    wire tap_last=!elem_pool || (tap_y==pkh-8'd1 && tap_x==pkw-8'd1);
    wire signed [12:0] add_term=$signed(add_second ? add_wb : add_wa)*
        $signed({a_data[3] && !(add_second ? su2 : su),a_data[3:0]});
    reg signed [31:0] selected_element;
    always @* begin
        selected_element=element_value;
        if(op==POOL && !tap_first && element_acc>element_value) selected_element=element_acc;
        if(op==AVGPOOL && !tap_first) selected_element=element_acc+element_value;
        if(op==ADD) selected_element=(add_second ? element_acc : 32'sd0)+add_term;
    end
    wire element_done=op==ADD ? add_second : tap_last;
    // Nibble tensors (i4/u4): one 8-nibble read covers all COLS channels of the
    // tile at a pixel, so every lane updates per read. Lane arithmetic needs 21
    // bits (largest window sum 255*255*15; Add |128*15|*2). INT32 sources keep
    // the scalar lane-by-lane path.
    wire evec=!src32;
    reg signed [20:0] lane_next[0:COLS-1];
    reg signed [20:0] lane_acc,lane_v;
    integer vl;
    always @* begin
        for(vl=0;vl<COLS;vl=vl+1)begin
            lane_acc=$signed(results[element_slot][vl*32+:21]);
            lane_v=$signed({{16{a_data[vl*4+3] && !(op==ADD && add_second ? su2 : su)}},a_data[vl*4+:4]});
            case(op)
                POOL:lane_next[vl]=!tap_first && lane_acc>lane_v ? lane_acc : lane_v;
                AVGPOOL:lane_next[vl]=tap_first ? lane_v : lane_acc+lane_v;
                ADD:lane_next[vl]=(add_second ? lane_acc : 21'sd0)+$signed(add_second ? add_wb : add_wa)*lane_v;
                default:lane_next[vl]=lane_v;
            endcase
        end
    end
    reg [15:0] eaddr;
    always @* begin
        eaddr=(op==ADD && add_second ? element_base2 : element_base)+(element_lane<<(src32 ? 3 : 0));
        if(elem_pool)eaddr=eaddr+tap_offset;
    end

    always @* begin
        m_valid=0; m_addr=0; a_valid=0;
        // The address is meaningful only on a request handshake. Do not add
        // state/valid gating to the data path merely to drive idle addresses 0.
        // Start each subset at its first valid lane. Subset membership is
        // derived from registered group offsets, not the returning data.
        a_addr=mac_op ? (gather_state==1 ? gather_addr[gather_row] :
            calc_addr[calc_first]) : eaddr;
        if(state==FREQ) begin m_valid=1; m_addr=pc*64+fetch_word*4; end
        // The model store accepts one read per cycle: request the next
        // descriptor/parameter word in the cycle the previous one returns.
        else if(state==FWAIT && m_rsp_valid && fetch_word!=15) begin
            m_valid=1; m_addr=pc*64+(fetch_word+1'b1)*4;
        end
        else if(state==PREQ) begin m_valid=1; m_addr=params+param_channel*4; end
        else if(state==PWAIT && m_rsp_valid && !last_param && !param_bad) begin
            m_valid=1; m_addr=params+next_param_channel*4;
        end
        else if(state==EXECUTE && mac_op && lstate==L_REQ && !load_zero) begin
            m_valid=1; m_addr=load_address;

        end else if(state==EXECUTE && mac_op && array_weight_we && load_row!=ROWS-1 && !next_load_zero) begin
            m_valid=1; m_addr=dwconv ? load_address : load_address+row_bytes;
        end
        if(state==EXECUTE) begin
            if(mac_op && !kernel_prepare && issue_more && (gather_state==0 || (issue_fire && has_next))) begin
                a_valid=|calc_mask;
            end else if(mac_op && !kernel_prepare && gather_state==1) begin
                a_valid=|gather_mask;
            end else if(!mac_op && estate==1) begin a_valid=1; end
        end
    end

    // Setup operand select for the shared multiplier (see astep).
    always @* begin
        mul_a=0; mul_b=0;
        if(state==DERIVE) case(astep)
            0:begin mul_a=ih; mul_b=iw; end
            1:begin mul_a=dh; mul_b=dw; end
            2:begin mul_a=iw; mul_b=icb; end
            3:begin mul_a=src_pixels; mul_b=icb; end
            4:begin mul_a=dst_pixels; mul_b=chb; end
            5:begin mul_a=oh-16'd1; mul_b=psh; end
            6:begin mul_a=ow-16'd1; mul_b=psw; end
            default:begin mul_a=octs; mul_b=groups; end
        endcase
        else if(state==SETUP) case(astep)
            0:begin mul_a=icb; mul_b=elem_pool ? psw : 8'd1; end
            1:begin mul_a=row_stride; mul_b=elem_pool ? psh : 8'd1; end
            2:begin mul_a=pkw-8'd1; mul_b=icb; end
            3:begin mul_a=ow-16'd1; mul_b=x_step; end
            4:begin mul_a=dw*2-16'd1; mul_b=x_step; end
            5:begin mul_a=dw-16'd1; mul_b=element_col_step; end
            6:begin mul_a=pt[2:0]; mul_b=row_stride; end
            7:begin mul_a=pl[2:0]; mul_b=ic; end
            8:begin mul_a=oct; mul_b=groups; end
            default:begin mul_a=dwconv ? kw[2:0]-3'd1 : kw[2:0]; mul_b=ic; end
        endcase
    end

    task automatic begin_tile;
        begin
            load_seq<=0; issue_seq<=0; load_g<=0; issue_g<=0; load_tile<=0; issue_tile<=0;
            load_row<=0; lstate<=L_IDLE; load_more<=1; issue_more<=1;
            bank_ready<=0; inflight[0]<=0; inflight[1]<=0; issue_p<=0;
            gather_state<=0; gather_row<=0; slot_busy<=0; slot_ready<=0;
            read_slot<=0; read_lane<=0; rq_pending<=0;
            write_head<=0;write_tail<=0;write_count<=0;
            estate<=0; element_pos<=0; element_lane<=0; element_slot<=0;
            tap_y<=0; tap_x<=0; tap_offset<=0; add_second<=0;
            ret_pos_addr<=dst; element_addr<=dst;
            cursor_y<=0; cursor_x<=0; tile_y<=0; tile_x<=0;cursor_at_last_x<=0;
            tile_positions<=TILE;last_group<=0;has_next_q<=TILE>1;
            kernel_c<=0; kernel_x<=0; kernel_y<=0;kernel_offset<=0;kernel_prepare<=1;prep_row<=0;
            element_y<=0; element_x<=0;
        end
    endtask
    task automatic fail(input [4:0] code);
        begin error<=code; error_op<=pc; state<=FAILED; end
    endtask
    wire compute_drained=write_count==0 && (mac_op ? (!issue_more && inflight[0]==0 && inflight[1]==0 &&
        slot_busy==0 && rq_pending==0 && !rq_valid && lstate==L_IDLE && !load_more) :
        (element_pos==dst_pixels && estate==0 && slot_busy==0 && rq_pending==0 && !rq_valid));
    // A partial write-combining word must reach physical SRAM before the next
    // channel tile/layer can reuse its address, or before done is published.
    assign memory_flush=state==EXECUTE && compute_drained;
    wire drained=compute_drained && memory_idle;

    // Passive one-cycle sampled monitoring; no feedback into datapath control.
    // Count execution only. The bank settles one cycle after execution ends.
    wire perf_active=state!=IDLE && !checking;
    wire perf_mac=state==EXECUTE && mac_op && issue_more && !kernel_prepare;
    wire perf_gather=perf_mac && (gather_state==0 || (issue_fire && has_next));
    wire [2:0] perf_slots={2'b0,slot_busy[0]}+{2'b0,slot_busy[1]}+
        {2'b0,slot_busy[2]}+{2'b0,slot_busy[3]};
    wire [1:0] perf_banks={1'b0,bank_ready[0]}+{1'b0,bank_ready[1]};
    wire [5:0] perf_inflight={1'b0,inflight[0]}+{1'b0,inflight[1]};
    wire perf_clear=rst || (state==IDLE && (validate || run));
    assign trace_clear=perf_clear;
    assign trace_active=perf_active;
    assign trace_events={state==FAILED,state==BASIC,state==NEXT,state==FREQ && fetch_word==0};
    assign trace_descriptor=d[0];
    // Samples describe values immediately before the rising execution edge.
    // Opcode is zero while fetching; the descriptor table carries fusion flags.
    assign trace_sample={
        pc,state,
        (write_fire ? (w_mask==8'hff ? 4'd8 : w_mask==8'h03 ? 4'd2 : 4'd1) : 4'd0),(write_fire ? w_addr : 16'b0),
        (a_valid ? a_addr : 16'b0),(m_valid ? m_addr : 16'b0),
        lstate,gather_state,perf_inflight,bank_ready,slot_ready,slot_busy,
        kernel_prepare,
        (perf_mac && !slot_available),
        (perf_mac && slot_available && !bank_ready[issue_seq[0]]),
        (perf_mac && slot_available && bank_ready[issue_seq[0]] && !gather_full),
        issue_fire,write_fire,a_rsp_valid,a_ready,a_valid,m_rsp_valid,m_ready,m_valid,
        cycles};
    reg perf_sample_active;
    reg [17:0] perf_events;
    reg [2:0] perf_sample_slots;
    reg [1:0] perf_sample_banks;
    reg [5:0] perf_sample_inflight;
    reg [31:0] perf_count[0:23];
    reg [31:0] perf_m_streak,perf_a_streak;
    reg perf_m_pending,perf_a_pending;
    integer pi;
    genvar pg;
    generate for(pg=0;pg<24;pg=pg+1) begin:g_perf
        assign perf_counters[pg*32+:32]=perf_count[pg];
    end endgenerate
    always @(posedge clk) begin
        if(perf_clear) begin
            perf_sample_active<=0;perf_events<=0;
            perf_sample_slots<=0;perf_sample_banks<=0;perf_sample_inflight<=0;
            perf_m_streak<=0;perf_a_streak<=0;perf_m_pending<=0;perf_a_pending<=0;
            for(pi=0;pi<24;pi=pi+1)perf_count[pi]<=0;
        end else begin
            perf_sample_active<=perf_active;
            // MAC wait reasons are exclusive: slot, then weight, then gather.
            perf_events<={
                perf_gather && calc_mask==0,
                perf_gather && calc_mask!=0 && !calc_contiguous,
                perf_gather && calc_mask!=0 && calc_contiguous,
                post_fire,
                state==EXECUTE && mac_op && issue_more && kernel_prepare,
                perf_mac && !slot_available,
                perf_mac && slot_available && !bank_ready[issue_seq[0]],
                perf_mac && slot_available && bank_ready[issue_seq[0]] && !gather_full,
                issue_fire,write_fire,
                perf_a_pending && !a_rsp_valid,a_rsp_valid,a_valid && !a_ready,a_valid && a_ready,
                perf_m_pending && !m_rsp_valid,m_rsp_valid,m_valid && !m_ready,m_valid && m_ready};
            perf_sample_slots<=perf_slots;perf_sample_banks<=perf_banks;perf_sample_inflight<=perf_inflight;
            if(perf_active) begin
                if(m_valid && m_ready)perf_m_pending<=1;
                else if(m_rsp_valid)perf_m_pending<=0;
                if(a_valid && a_ready)perf_a_pending<=1;
                else if(a_rsp_valid)perf_a_pending<=0;
            end
            if(perf_sample_active) begin
                perf_count[0]<=perf_count[0]+1'b1;
                if(perf_events[0])perf_count[1]<=perf_count[1]+1'b1;
                if(perf_events[1])perf_count[2]<=perf_count[2]+1'b1;
                if(perf_events[2])perf_count[4]<=perf_count[4]+1'b1;
                if(perf_events[3])perf_count[5]<=perf_count[5]+1'b1;
                if(perf_events[4])perf_count[6]<=perf_count[6]+1'b1;
                if(perf_events[5])perf_count[7]<=perf_count[7]+1'b1;
                if(perf_events[6])perf_count[9]<=perf_count[9]+1'b1;
                if(perf_events[7])perf_count[10]<=perf_count[10]+1'b1;
                if(perf_events[8])perf_count[11]<=perf_count[11]+1'b1;
                if(perf_events[9])perf_count[12]<=perf_count[12]+1'b1;
                if(perf_events[10])perf_count[13]<=perf_count[13]+1'b1;
                if(perf_events[11])perf_count[14]<=perf_count[14]+1'b1;
                if(perf_events[12])perf_count[15]<=perf_count[15]+1'b1;
                if(perf_events[13])perf_count[16]<=perf_count[16]+1'b1;
                if(perf_sample_slots>perf_count[17])perf_count[17]<=perf_sample_slots;
                if(perf_sample_banks>perf_count[18])perf_count[18]<=perf_sample_banks;
                if(perf_sample_inflight>perf_count[19])perf_count[19]<=perf_sample_inflight;
                if(perf_events[14])perf_count[20]<=perf_count[20]+1'b1;
                if(perf_events[15])perf_count[21]<=perf_count[21]+1'b1;
                if(perf_events[16])perf_count[22]<=perf_count[22]+1'b1;
                if(perf_events[17])perf_count[23]<=perf_count[23]+1'b1;
                if(perf_events[1])begin
                    perf_m_streak<=perf_m_streak+1'b1;
                    if(perf_m_streak>=perf_count[3])perf_count[3]<=perf_m_streak+1'b1;
                end else perf_m_streak<=0;
                if(perf_events[5])begin
                    perf_a_streak<=perf_a_streak+1'b1;
                    if(perf_a_streak>=perf_count[8])perf_count[8]<=perf_a_streak+1'b1;
                end else perf_a_streak<=0;
            end else begin perf_m_streak<=0;perf_a_streak<=0;end
        end
    end

    integer v;
    always @(posedge clk) begin
        if(rst) begin
            state<=IDLE; checking<=0; pc<=0; fetch_word<=0;
            q_enable<=0;relu_enable<=0;pool_enable<=0;mac_op<=0;dwconv<=0;pool1d<=0;elem_pool<=0;
            astep<=0;fetch_bad<=0;
            validation_done<=0; validation_ok<=0; done<=0; error<=0; error_op<=0;
            cycles<=0; issues<=0; model_reads<=0; act_reads<=0; act_writes<=0;
            lookup_done<=0;
            oct<=0; param_channel<=0;
            oh<=1; ow<=1; dh<=1; dw<=1; k_count<=1; groups<=1; octs<=1; positions<=1; row_stride<=1;
            src_span<=0; dst_span<=0; weight_span<=0; param_span<=0;src_pixels<=0;dst_pixels<=0;
            begin_tile;
        end else begin
            validation_done<=0; done<=0;
            if(state!=IDLE && !checking) cycles<=cycles+1;
            if(m_valid && m_ready) model_reads<=model_reads+1;
            if(a_valid && a_ready) act_reads<=act_reads+1;
            if(write_fire) act_writes<=act_writes+1;
            case(state)
                IDLE: if(validate || run) begin
                    checking<=validate; pc<=0; fetch_word<=0; error<=0; error_op<=0;
                    validation_ok<=0; state<=FREQ; cycles<=0; issues<=0;
                    model_reads<=0; act_reads<=0; act_writes<=0;
                    if(op_count==0 || op_count>MAX_OPS || model_bytes>MODEL_BYTES ||
                       model_bytes<op_count*64 || input_bytes==0 || output_bytes==0 ||
                       input_base[1:0]!=0 || output_base[1:0]!=0 ||
                       {1'b0,input_base}+{1'b0,input_bytes}>ACT_BYTES ||
                       {1'b0,output_base}+{1'b0,output_bytes}>ACT_BYTES) begin
                        error<=1; state<=FAILED;
                    end
                end
                FREQ: begin if(m_ready) state<=FWAIT; if(fetch_word==0) fetch_bad<=0; end
                FWAIT: if(m_rsp_valid) begin
                    d[fetch_word]<=m_data;
                    // Reserved/range fields are checked here so they are never stored.
                    case(fetch_word)
                        0: if(m_data[31:18]!=0) fetch_bad<=1;
                        1,2: if(m_data[31:15]!=0 || m_data[1:0]!=0) fetch_bad<=1;
                        3: if(m_data[15:8]!=0 || m_data[31:24]!=0) fetch_bad<=1;
                        7,8: if(m_data[31:16]!=0) fetch_bad<=1;
                        9: if(m_data[31:10]!=0) fetch_bad<=1;
                        11: if(d[0][7:0]==ADD ? (m_data[31:15]!=0 || m_data[1:0]!=0) : m_data!=0) fetch_bad<=1;
                        12: if(d[0][7:0]==ADD ? m_data[31:16]!=0 : m_data!=0) fetch_bad<=1;
                        13,14: if(m_data!=0) fetch_bad<=1;
                        15: if(m_data!=(32'h49340f80|COLS)) fetch_bad<=1;
                        default: ;
                    endcase
                    if(fetch_word==15) begin
                        mac_op<=d[0][7:0]==CONV || d[0][7:0]==FC;
                        q_enable<=d[0][8] || d[0][7:0]==REQUANT;
                        relu_enable<=d[0][9] || d[0][7:0]==RELU;
                        // pool_enable is the fused MAC pool; standalone pools use elem_pool.
                        pool_enable<=d[0][10] && (d[0][7:0]==CONV || d[0][7:0]==FC);
                        pool1d<=d[0][14];
                        dwconv<=d[0][13];
                        elem_pool<=d[0][7:0]==POOL || d[0][7:0]==AVGPOOL;
                        state<=BASIC;
                    end
                    else begin fetch_word<=fetch_word+1'b1; state<=m_ready ? FWAIT : FREQ; end
                end
                BASIC: if(basic_bad) fail(2); else begin
                    oh<=xoh;ow<=xow;dh<=xdh;dw<=xdw;k_count<=xk;
                    groups<=(xk+ROWS-1)/ROWS;octs<=(channels+COLS-1)/COLS;
                    astep<=0;state<=DERIVE;
                end
                DERIVE:begin
                    astep<=astep+1'b1;
                    case(astep)
                        0:src_pixels<=mul_p;
                        1:dst_pixels<=mul_p;
                        2:row_stride<=mul_p;
                        3:src_span<=(mul_p+1)&32'hfffffffe;
                        4:dst_span<=(mul_p+1)&32'hfffffffe;
                        5:pool_chk_h<=mul_p;
                        6:pool_chk_w<=mul_p;
                        default:begin
                            weight_span<=dwconv ? mul_p*(ROWS/2) :
                                mul_p*ROWS*(COLS/2)-(COLS==8 && oc%8!=0 && oc%8<=4 ? groups*ROWS*2 : 0);
                            param_span<=channels*4;
                            positions<=dst_pixels<<(!pool_enable ? 0 : pool1d ? 1 : 2);
                            lookup_done<=0;
                            state<=CHECK;
                        end
                    endcase
                end
                CHECK: begin
                    if(!lookup_done) begin
                        if(shape_bad) fail(3);
                        else if(range_bad) fail(4);
                        else state<=LOOKUP_WAIT;
                    end
                    else if(!source_known || !source2_known) fail(5);
                    else if(output_bad) fail(3);
                    else begin
                        oct<=0; param_channel<=0;
                        astep<=0;
                        if(need_params) state<=PREQ;
                        else state<=checking ? NEXT : SETUP;
                    end
                end
                LOOKUP_WAIT: if(scan_done) begin lookup_done<=1; state<=CHECK; end
                PREQ: if(m_ready) state<=PWAIT;
                PWAIT: if(m_rsp_valid) begin
                    if(param_bad) fail(6);
                    else begin
                        if(!checking) begin
                            bias_cache[param_channel%COLS]<=m_data[17:0];
                            mult_cache[param_channel%COLS]<=m_data[25:18];
                            shift_cache[param_channel%COLS]<=m_data[31:26];
                        end
                        if(last_param) state<=checking ? NEXT : SETUP;
                        else begin
                            param_channel<=next_param_channel;
                            state<=m_ready ? PWAIT : PREQ;
                        end
                    end
                end
                SETUP: begin
                    astep<=astep+1'b1;
                    case(astep)
                        0:begin
                            begin_tile;
                            x_step<=sw==2 ? ic<<1 : ic;y_step<=sh==2 ? row_stride<<1 : row_stride;
                            tap_col_step<=icb; element_col_step<=mul_p;
                        end
                        1:element_row_step<=mul_p;
                        2:tap_row_jump<=row_stride-mul_p;
                        3:scan_product<=mul_p;
                        4:pool_scan_product<=mul_p;
                        5:element_scan_product<=mul_p;
                        6:origin_y<=mul_p;
                        7:origin_x<=mul_p;
                        8:begin
                            // Depthwise tiles hold one COLS-lane row per tap: groups*2 bytes.
                            weight_tile_base<=weights+mul_p*(dwconv ? ROWS/2 : ROWS*(COLS/2));
                            load_address<=weights+mul_p*(dwconv ? ROWS/2 : ROWS*(COLS/2));
                        end
                        default:begin
                            // Normal kernels step over IC contiguous channels per tap;
                            // depthwise ones over COLS of them, then to the next pixel.
                            kernel_row_jump<=dwconv ? row_stride-mul_p-COLS+1 : row_stride-mul_p+1;
                            state<=INIT_A;
                        end
                    endcase
                end
                INIT_A:begin
                    scan_last_x<=(pool_enable ? dw : ow)-1; element_last_x<=dw-1;
                    cursor_at_last_x<=(pool_enable ? dw : ow)==1;
                    origin<=src+(dwconv ? oct*COLS : 16'd0)-origin_y-origin_x;
                    state<=INIT_B;
                end
                INIT_B:begin
                    cursor_base<=origin;tile_base<=origin;
                    for(v=0;v<8;v=v+1)begin
                        if(!pool_enable)cursor_step[v]<=v>=4 ? y_step-scan_product : x_step;
                        else if(pool1d)cursor_step[v]<=(v>=4 && (v%2)==1) ? y_step-pool_scan_product : x_step;
                        else case(v%4)
                            0,2:cursor_step[v]<=x_step;
                            1:cursor_step[v]<=y_step-x_step;
                            3:cursor_step[v]<=v>=4 ? y_step-pool_scan_product : x_step-y_step;
                        endcase
                    end
                    kernel_col_jump<=dwconv ? ic-COLS+1 : 16'd1;
                    element_base<=src+((oct*COLS)<<(src32 ? 3 : 0));
                    element_base2<=src2+oct*COLS;
                    element_row_jump<=element_row_step-element_scan_product;state<=EXECUTE;
                end
                EXECUTE: begin
                    write_count<=write_count+write_push-write_fire;
                    if(write_fire)write_head<=write_head+1'b1;
                    if(write_push)begin
                        write_addr[write_tail]<=write_at;
                        write_data[write_tail]<=q_enable ? {24'd0,rq_pair ? rq1_data : 4'd0,rq_data} :
                            (dst32 ? post_value : {24'd0,lane1_valid ? post_nibble1 : 4'd0,post_value[3:0]});
                        write_mask[write_tail]<=dst32 ? 8'hff :
                            write_two ? (write_tail_pad ? 8'h07 : 8'h03) : (write_tail_pad ? 8'h03 : 8'h01);
                        write_tail<=write_tail+1'b1;
                    end
                    inflight[0]<=inflight[0]+(issue_fire && !issue_seq[0])-(out_valid && !out_bank);
                    inflight[1]<=inflight[1]+(issue_fire && issue_seq[0])-(out_valid && out_bank);
                    rq_pending<=rq_pending+(post_fire && q_enable)-rq_valid;
                    if(post_fire) begin
                        if(post_last) begin
                            read_lane<=0; slot_ready[read_slot]<=0; slot_busy[read_slot]<=0;
                            read_slot<=read_slot+1'b1;
                        end else read_lane<=read_lane+(pair ? 2'd2 : 2'd1);
                    end
                    if(mac_op) begin
                        if(kernel_prepare && issue_more)begin
                            if(prep_row<ROWS && prep_contiguous) begin
                                for(v=0;v<ROWS;v=v+1)begin
                                    k_offsets[v]<=kernel_offset+v;
                                    k_normalized[v]<=kernel_offset;
                                    k_y_begin[v]<=coordinate_bound(0,pt[2:0],rowpad ? issue_g[2:0] : kernel_y,sh[1]);
                                    k_y_end[v]<=coordinate_bound(ih[7:0],pt[2:0],rowpad ? issue_g[2:0] : kernel_y,sh[1]);
                                    k_x_begin[v]<=coordinate_bound(0,pl[2:0],rowpad ? v : kernel_x,sw[1]);
                                    k_x_end[v]<=coordinate_bound(iw[7:0],pl[2:0],rowpad ? v : kernel_x,sw[1]);
                                    k_valid[v]<=rowpad ? v<kw : issue_g*ROWS+v<k_count;
                                end
                                prep_row<=ROWS;
                                if(rowpad)kernel_offset<=kernel_offset+row_stride;
                                else if(op==FC)kernel_offset<=kernel_offset+ROWS;
                                else begin
                                    kernel_offset<=kernel_offset+ROWS-1+
                                        (prep_group_end ? (prep_end_column ? kernel_row_jump : kernel_col_jump) : 16'd1);
                                    kernel_c<=prep_group_end ? 11'd0 : kernel_c+ROWS;
                                    if(prep_group_end)begin
                                        kernel_x<=prep_end_column ? 3'd0 : kernel_x+3'd1;
                                        if(prep_end_column)kernel_y<=kernel_y+3'd1;
                                    end
                                end
                            end else if(prep_row<ROWS) begin
                                for(v=0;v<ROWS;v=v+1) if(prep_index==v) begin
                                    k_offsets[v]<=rowpad ? kernel_offset+prep_row_ext : kernel_offset;
                                    k_normalized[v]<=rowpad ? kernel_offset : kernel_offset-prep_row_ext;
                                    k_y_begin[v]<=coordinate_bound(0,pt[2:0],kernel_y,sh[1]);
                                    k_y_end[v]<=coordinate_bound(ih[7:0],pt[2:0],kernel_y,sh[1]);
                                    k_x_begin[v]<=coordinate_bound(0,pl[2:0],kernel_x,sw[1]);
                                    k_x_end[v]<=coordinate_bound(iw[7:0],pl[2:0],kernel_x,sw[1]);
                                    k_valid[v]<=rowpad ? prep_row<kw : issue_g*ROWS+prep_row<k_count;
                                end
                                prep_row<=prep_row+1'b1;
                                if(rowpad) begin
                                    if(prep_row==ROWS-1) kernel_offset<=kernel_offset+row_stride;
                                end else begin
                                    kernel_offset<=kernel_offset+prep_step;
                                    kernel_c<=prep_end_channels ? 11'd0 : kernel_c+11'd1;
                                    if(prep_end_channels) begin
                                        kernel_x<=prep_end_column ? 3'd0 : kernel_x+3'd1;
                                        if(prep_end_column) kernel_y<=kernel_y+3'd1;
                                    end
                                end
                            end else begin
                                for(v=0;v<ROWS;v=v+1)for(integer vv=0;vv<ROWS;vv=vv+1)
                                    k_consecutive[v*ROWS+vv]<=(v>=vv) || k_normalized[vv]==k_normalized[v];
                                kernel_prepare<=0;
                                tile_positions<=remaining<TILE ? remaining[4:0] : TILE;
                                has_next_q<=remaining>1;
                                last_group<=issue_g==groups-16'd1;
                            end
                        end
                        if(load_start) lstate<=L_REQ;
                        if(lstate==L_REQ && m_ready && !load_zero) lstate<=L_WAIT;
                        if(lstate==L_WAIT && m_rsp_valid) begin
                            if(!array_weight_we) begin load_data<=response_row; lstate<=L_HAVE; end
                        end
                        if(array_weight_we) begin
                            // Depthwise: every group of a tap reuses that tap's one stored row.
                            load_address<=load_row==ROWS-1 && load_g==groups-1 ? weight_tile_base :
                                dwconv && (load_row!=ROWS-1 || ((load_g+1)%(COLS/ROWS)!=0)) ? load_address : load_address+row_bytes;
                            if(load_row==ROWS-1) begin
                                load_row<=0; lstate<=L_IDLE; bank_ready[load_bank]<=1; load_seq<=load_seq+1'b1;
                                if(load_g!=groups-1) load_g<=load_g+1'b1;
                                else begin
                                    load_g<=0;
                                    if((load_tile+1)*TILE<positions) load_tile<=load_tile+1'b1;
                                    else load_more<=0;
                                end
                            end else begin load_row<=load_row+1'b1; lstate<=!next_load_zero && m_ready ? L_WAIT : L_REQ; end
                        end
                        if(issue_more && !kernel_prepare) case(gather_state)
                            0: begin
                                for(v=0;v<ROWS;v=v+1) gather_addr[v]<=calc_addr[v];
                                gather_mask<=calc_mask;
                                gathered<=0; gather_row<=calc_first;
                                gather_state<=calc_mask==0 ? 3 : (a_valid && a_ready ? 2 : 1);
                            end
                            1: if(a_ready) gather_state<=2;
                            2: if(a_rsp_valid) begin
                                gathered<=returned_vector;
                                gather_mask<=gather_remaining;
                                if(gather_remaining==0)gather_state<=3;
                                else begin gather_row<=gather_next; gather_state<=1; end
                            end
                            default: ;
                        endcase
                        if(issue_fire) begin
                            issues<=issues+1;
                            for(v=0;v<ROWS;v=v+1) gather_addr[v]<=calc_addr[v];
                            gather_mask<=calc_mask;
                            gathered<=0; gather_row<=calc_first;
                            gather_state<=!has_next ? 0 : calc_mask==0 ? 3 : (a_valid && a_ready ? 2 : 1);
                            if(last_group && reserve_slot) slot_busy[issue_slot]<=1;
                            if(!has_next) begin
                                issue_p<=0;has_next_q<=tile_positions>1;
                                prep_row<=0;
                                bank_ready[issue_seq[0]]<=0; issue_seq<=issue_seq+1'b1;
                                if(!last_group) begin
                                    issue_g<=issue_g+1'b1;
                                    kernel_prepare<=1;cursor_base<=tile_base;
                                    cursor_y<=tile_y; cursor_x<=tile_x;
                                    cursor_at_last_x<=tile_x==scan_last_x;
                                end
                                else begin
                                    issue_g<=0;
                                    kernel_c<=0;kernel_x<=0;kernel_y<=0;kernel_offset<=0;kernel_prepare<=1;
                                    cursor_base<=next_base;tile_base<=next_base;
                                    cursor_y<=next_y; cursor_x<=next_x;
                                    cursor_at_last_x<=next_x==scan_last_x;
                                    tile_y<=next_y; tile_x<=next_x;
                                    if((issue_tile+1)*TILE<positions) issue_tile<=issue_tile+1'b1;
                                    else issue_more<=0;
                                end
                            end else begin
                                issue_p<=issue_p+1'b1;cursor_base<=next_base;
                                has_next_q<=(issue_p+5'd1)!=(tile_positions-5'd1);
                                cursor_y<=next_y;cursor_x<=next_x;
                                cursor_at_last_x<=next_x==scan_last_x;
                            end
                        end
                        if(out_valid) begin
                            for(lane=0;lane<COLS;lane=lane+1) begin
                                reduced=$signed(ret_first ? {PSUM_BITS{1'b0}} : psum[psum_index][lane*PSUM_BITS+:PSUM_BITS])+
                                    $signed(out_sum[lane*11+:11]);
                                biased=$signed(reduced)+$signed(bias_cache[lane]);
                                if(!ret_last) psum[psum_index][lane*PSUM_BITS+:PSUM_BITS]<=reduced;
                                else if(!pool_enable || ret_tap_first ||
                                    biased>$signed(results[ret_slot][lane*32+:32]))
                                    results[ret_slot][lane*32+:32]<=biased;
                            end
                            if(ret_last && (!pool_enable || ret_tap_last)) begin
                                slot_ready[ret_slot]<=1;
                                result_addr[ret_slot]<=ret_pos_addr; ret_pos_addr<=ret_pos_addr+chb;
                            end
                        end
                    end else begin
                        case(estate)
                            0: if(element_pos<dst_pixels && !slot_busy[element_slot]) begin
                                slot_busy[element_slot]<=1; element_lane<=0;
                                tap_y<=0; tap_x<=0; tap_offset<=0; add_second<=0;
                                estate<=1;
                            end
                            1: if(a_ready) estate<=2;
                            2: if(a_rsp_valid && evec) begin
                                for(v=0;v<COLS;v=v+1)results[element_slot][v*32+:32]<={{11{lane_next[v][20]}},lane_next[v]};
                                if(!element_done) begin
                                    if(op==ADD) add_second<=1;
                                    else if(tap_x==pkw-8'd1) begin
                                        tap_x<=0; tap_y<=tap_y+1'b1; tap_offset<=tap_offset+tap_row_jump;
                                    end else begin tap_x<=tap_x+1'b1; tap_offset<=tap_offset+tap_col_step; end
                                    estate<=1;
                                end else begin
                                    tap_y<=0; tap_x<=0; tap_offset<=0; add_second<=0; estate<=3;
                                end
                            end else if(a_rsp_valid) begin
                                element_acc<=selected_element;
                                if(!element_done) begin
                                    if(op==ADD) add_second<=1;
                                    else if(tap_x==pkw-8'd1) begin
                                        tap_x<=0; tap_y<=tap_y+1'b1; tap_offset<=tap_offset+tap_row_jump;
                                    end else begin tap_x<=tap_x+1'b1; tap_offset<=tap_offset+tap_col_step; end
                                    estate<=1;
                                end else begin
                                    results[element_slot][element_lane*32+:32]<=selected_element;
                                    tap_y<=0; tap_x<=0; tap_offset<=0; add_second<=0;
                                    if(element_lane==COLS-1 || oct*COLS+element_lane==channels-1) estate<=3;
                                    else begin element_lane<=element_lane+1'b1; estate<=1; end
                                end
                            end
                            3: begin
                                result_addr[element_slot]<=element_addr; element_addr<=element_addr+chb;
                                slot_ready[element_slot]<=1; element_pos<=element_pos+1'b1;
                                element_base<=element_base+(element_x==element_last_x ? element_row_jump : element_col_step);
                                element_base2<=element_base2+(element_x==element_last_x ? element_row_jump : element_col_step);
                                if(element_x==element_last_x) begin element_x<=0; element_y<=element_y+1'b1; end
                                else element_x<=element_x+1'b1;
                                element_slot<=element_slot+1'b1; estate<=0;
                            end
                        endcase
                    end
                    if(drained) begin
                        if(oct==octs-1) state<=NEXT;
                        else begin
                            oct<=oct+1'b1; param_channel<=(oct+1)*COLS; astep<=0;
                            state<=need_params ? PREQ : SETUP;
                        end
                    end
                end
                NEXT: if(pc==op_count-1) begin
                    if(checking) begin validation_done<=1; validation_ok<=1; end
                    else done<=1;
                    state<=IDLE;
                end else begin pc<=pc+1'b1; fetch_word<=0; state<=FREQ; end
                FAILED: begin
                    if(checking) begin validation_done<=1; validation_ok<=0; end
                    else done<=1;
                    state<=IDLE;
                end
                default: state<=IDLE;
            endcase
        end
    end
    // TILE output positions share one weight load: 16 (psum 16xCOLSx18 FF) or 8.
    initial if((COLS!=8) || (TILE!=16 && TILE!=8) || MAX_K<1 || MAX_K>1024 ||
        (ROWS!=8) || MAX_OPS<1 || MAX_OPS>128 || PSUM_BITS<18 || ACT_BYTES<64 || ACT_BYTES>16384)
        $fatal(1,"unsupported program-core geometry");
endmodule

// Sequential tensor provenance check. Entry zero is the input and each
// accepted descriptor writes pc+1, so a scan only visits initialized entries.
module npu_tensor_lookup #(parameter MAX_OPS=128) (
    input wire clk,rst,seed,start,save,
    input wire [7:0] pc,
    input wire input_i32,src_i32,dst_i32,add_op,
    input wire [15:0] input_first,input_end,src,src_end,src2,src2_end,dst,dst_end,
    output reg done,source_known,source2_known
);
    localparam ADDR_BITS=$clog2(MAX_OPS+1);
    (* ram_style = "distributed" *) reg [33:0] records[0:(1<<ADDR_BITS)-1];
    reg [33:0] record;
    reg [7:0] index;
    reg active,have_record;
    wire [15:0] record_base=record[15:0],record_end=record[31:16];
    wire record_valid=record[33],record_i32=record[32];
    wire overlap=record_valid && dst<record_end && record_base<dst_end;
    wire write_enable=seed || save || (active && have_record && overlap);
    wire [ADDR_BITS-1:0] address=seed ? 8'd0 : save ? pc+8'd1 : index;
    wire [33:0] write_record=seed ? {1'b1,input_i32,input_end,input_first} :
        save ? {1'b1,dst_i32,dst_end,dst} : {1'b0,record[32:0]};
    always @(posedge clk) begin
        if(!rst && write_enable) records[address]<=write_record;
        if(active && !have_record) record<=records[address];
    end
    always @(posedge clk) begin
        if(rst) begin
            active<=0; have_record<=0; done<=0; index<=0;
            source_known<=0; source2_known<=0;
        end else begin
            done<=0;
            if(start) begin
                active<=1; have_record<=0; index<=0;
                source_known<=0; source2_known<=!add_op;
            end else if(active) begin
                have_record<=!have_record;
                if(have_record) begin
                    if(record_valid && record_i32==src_i32 && src>=record_base && src_end<=record_end)
                        source_known<=1;
                    if(record_valid && !record_i32 && src2>=record_base && src2_end<=record_end)
                        source2_known<=1;
                    if(index==pc) begin active<=0; done<=1; end
                    else index<=index+1'b1;
                end
            end
        end
    end
endmodule

