`timescale 1ns/1ps
// ABI 2 descriptor sequencer and shared five-operator backend.
// No model geometry, layer index or output class count is hardwired.
// Model RAM is immutable while validating/running. All memory addresses are bytes.
module npu_program_core #(
    parameter ACT_BYTES=8192, MODEL_BYTES=8192, COLS=8, TILE=16,
    parameter MAX_OPS=16, MAX_K=1024, PSUM_BITS=26
) (
    input wire clk,rst, input wire validate,run,
    input wire [15:0] model_bytes,input_base,input_bytes,output_base,output_bytes,
    input wire [4:0] op_count,
    input wire input_i32,
    output wire ready, output wire busy,
    output reg validation_done,validation_ok,done,
    output reg [4:0] error,error_op,
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
    output wire [31:0] w_data, output wire [3:0] w_mask,
    output wire memory_flush, input wire memory_idle
);
    localparam IDLE=0,FREQ=1,FWAIT=2,BASIC=3,CHECK=4,PREQ=5,PWAIT=6,
        SETUP=7,EXECUTE=8,NEXT=9,FAILED=10,INIT_A=11,INIT_B=12,DERIVE=14;
    localparam CONV=1,FC=2,POOL=3,RELU=4,REQUANT=5;
    localparam L_IDLE=0,L_REQ=1,L_WAIT=2,L_HAVE=3;
    reg [3:0] state;
    reg checking;
    reg [4:0] pc;
    reg [3:0] fetch_word;
    reg [31:0] d[0:15];
    // Full opcode is checked before execution. Cache the hot one-bit modes
    // rather than repeating wide opcode comparisons on memory address paths.
    wire [2:0] op=d[0][2:0];
    reg q_enable,relu_enable,pool_enable,mac_op;
    wire src32=d[0][11],dst32=d[0][12];
    wire [15:0] src=d[1][15:0],dst=d[2][15:0];
    wire [15:0] ih=d[3][15:0],iw=d[3][31:16];
    wire [15:0] ic=d[4][15:0],oc=d[4][31:16];
    wire [7:0] kh=d[5][7:0],kw=d[5][15:8],sh=d[5][23:16],sw=d[5][31:24];
    wire [7:0] pt=d[6][7:0],pl=d[6][15:8],pb=d[6][23:16],pr=d[6][31:24];
    wire [15:0] weights=d[7][15:0],params=d[8][15:0];
    wire signed [7:0] clamp_min=d[9][7:0],clamp_max=d[9][15:8];
    reg [15:0] oh,ow,dh,dw,k_count,groups,octs,row_stride;
    reg [31:0] src_span,dst_span,weight_span,param_span;
    reg [15:0] positions;
    reg [15:0] src_pixels,dst_pixels;
    reg [15:0] known_base[0:MAX_OPS],known_size[0:MAX_OPS];
    reg [MAX_OPS:0] known_valid,known_i32;
    reg [15:0] param_channel;
    reg [1:0] param_word;
    reg [31:0] bias_cache[0:COLS-1],mult_cache[0:COLS-1];
    reg signed [5:0] shift_cache[0:COLS-1];
    reg [15:0] oct;
    // Only checked channel counts (<=256) reach the execution path.
    wire [8:0] channels=mac_op ? oc[8:0] : ic[8:0];
    wire need_params=mac_op || op==REQUANT;
    wire last_param=(param_word==2 &&
        (checking ? param_channel==channels-1 :
         (param_channel==channels-1 || (param_channel%COLS)==COLS-1)));
    wire param_bad=q_enable && ((param_word==1 && m_data[31]) ||
        (param_word==2 && ($signed(m_data)<-31 || $signed(m_data)>31)));
    wire [15:0] next_param_channel=param_word==2 ? param_channel+1'b1 : param_channel;
    wire [1:0] next_param_word=param_word==2 ? 2'd0 : param_word+1'b1;
    assign ready=state==IDLE && !rst;
    assign busy=state!=IDLE;
    assign m_rsp_ready=1'b1;
    assign a_rsp_ready=1'b1;

    // Validation uses bounded dimensions. Derived quantities are latched once
    // per descriptor; execution never recomputes tensor-size multiplications.
    integer xoh,xow,xdh,xdw,xk,xsrc,xdst,xweight,xparam,j;
    reg basic_bad,shape_bad,range_bad,source_known;
    always @* begin
        basic_bad=d[0][31:13]!=0 || d[0][7:0]<CONV || d[0][7:0]>REQUANT ||
            d[1][31:16]!=0 || d[2][31:16]!=0 || d[7][31:16]!=0 || d[8][31:16]!=0 ||
            ih==0 || ih>255 || iw==0 || iw>255 || ic==0 || ic>MAX_K ||
            oc==0 || oc>256 || src[1:0]!=0 || dst[1:0]!=0 ||
            d[9][31:16]!=0 || d[11]!=0 || d[12]!=0 || d[13]!=0 || d[14]!=0 || d[15]!=0;
        if(mac_op) begin
            basic_bad=basic_bad || src32 || (q_enable==dst32) ||
                (kh!=1 && kh!=3) || kh!=kw || (sh!=1 && sh!=2) || (sw!=1 && sw!=2) ||
                pt>=kh || pb>=kh || pl>=kw || pr>=kw || weights[2:0]!=0 || params[1:0]!=0;
            if(op==FC) basic_bad=basic_bad || ih!=1 || iw!=1 || kh!=1 || sh!=1 || sw!=1 || pool_enable;
        end else begin
            basic_bad=basic_bad || oc!=ic || d[0][10:8]!=0 || d[5]!=0 || d[6]!=0 || weights!=0 ||
                (op!=REQUANT && params!=0) ||
                (op==REQUANT ? (dst32 || params[1:0]!=0) : src32!=dst32);
        end
        if(q_enable) basic_bad=basic_bad || clamp_min>clamp_max || (relu_enable && clamp_max<0);
        else basic_bad=basic_bad || d[9]!=0;
        xoh=ih; xow=iw; xk=ic;
        if(mac_op && sh!=0 && sw!=0) begin
            xoh=(($signed({1'b0,ih})+$signed({1'b0,pt})+$signed({1'b0,pb})-$signed({1'b0,kh})) >>> (sh==2))+1;
            xow=(($signed({1'b0,iw})+$signed({1'b0,pl})+$signed({1'b0,pr})-$signed({1'b0,kw})) >>> (sw==2))+1;
            xk=kh==3 ? ic*9 : ic;
        end
        xdh=pool_enable ? xoh/2 : xoh;
        xdw=pool_enable ? xow/2 : xow;
        xsrc=src_span;
        xdst=dst_span;
        xweight=weight_span;
        xparam=param_span;
        shape_bad=xoh<1 || xoh>255 || xow<1 || xow>255 || xdh<1 || xdw<1 ||
            (mac_op && ($signed({1'b0,ih})+$signed({1'b0,pt})+$signed({1'b0,pb})<$signed({1'b0,kh}) ||
                         $signed({1'b0,iw})+$signed({1'b0,pl})+$signed({1'b0,pr})<$signed({1'b0,kw}) || xk>MAX_K)) ||
            d[10]!={xdw[15:0],xdh[15:0]};
        range_bad=xsrc<1 || xsrc>ACT_BYTES || xdst<1 || xdst>ACT_BYTES ||
            $signed({1'b0,src})+xsrc>ACT_BYTES || $signed({1'b0,dst})+xdst>ACT_BYTES ||
            (mac_op && ($signed({1'b0,weights})+xweight>model_bytes || weights<op_count*64)) ||
            (need_params && ($signed({1'b0,params})+xparam>model_bytes || params<op_count*64)) ||
            (($signed({1'b0,src})<$signed({1'b0,dst})+xdst && $signed({1'b0,dst})<$signed({1'b0,src})+xsrc) &&
             !(op==RELU && src==dst && xsrc==xdst));
        source_known=0;
        for(j=0;j<=MAX_OPS;j=j+1)
            if(known_valid[j] && known_i32[j]==src32 && src>=known_base[j] &&
               $signed({1'b0,src})+xsrc<=$signed({1'b0,known_base[j]})+$signed({1'b0,known_size[j]})) source_known=1;
    end

    // Current output-channel tile caches are the only live parameter vectors.
    // Weight prefetch fills the idle array bank while the other bank issues.
    reg [1:0] lstate;
    reg [15:0] load_seq,issue_seq,load_g,issue_g,load_tile,issue_tile;
    reg [1:0] load_row;
    reg load_half,load_more,issue_more;
    reg [31:0] load_low;
    reg [COLS*8-1:0] load_data;
    reg [1:0] bank_ready;
    reg [4:0] inflight[0:1];
    wire load_bank=load_seq[0];
    // ABI 2.1: on an 8-column array, an output-channel tile with at most four
    // real channels (always the last tile) stores one 32-bit word per weight
    // row instead of two; the upper four lanes load as zero.
    wire [15:0] tile_channels=channels-oct*COLS;
    wire narrow=COLS==8 && tile_channels<=4;
    wire [15:0] row_bytes=narrow ? 16'd4 : COLS;
    wire load_return=lstate==L_WAIT && m_rsp_valid && (COLS==4 || load_half || narrow);
    wire array_weight_we=state==EXECUTE && mac_op && (lstate==L_HAVE || load_return) && inflight[load_bank]==0;
    wire [COLS*8-1:0] response_row=COLS==8 ? (narrow ? {{(COLS*8-32){1'b0}},m_data} : {m_data,load_low}) : m_data;
    wire [COLS*8-1:0] array_weights=load_return ? response_row : load_data;
    reg [15:0] weight_tile_base,load_address;
    wire load_start=load_more && lstate==L_IDLE && !bank_ready[load_bank] &&
        (load_seq==issue_seq || load_seq==issue_seq+1'b1);

    // At most four completed vectors are resident. Reserve their slots before
    // issuing the final K group, including outputs still inside the MAC pipe.
    reg [COLS*PSUM_BITS-1:0] psum[0:TILE-1];
    reg [COLS*32-1:0] results[0:3];
    reg [15:0] result_pos[0:3];
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
    reg [3:0] write_mask[0:WRITE_DEPTH-1];
    reg [2:0] write_head,write_tail;
    reg [3:0] write_count;
    wire write_fire=w_valid && w_ready;
    wire post_credit=({1'b0,write_count}+rq_pending<WRITE_DEPTH) || write_fire;
    wire post_fire=state==EXECUTE && slot_ready[read_slot] && post_credit;
    wire [15:0] post_channel=oct*COLS+read_lane;
    wire post_last=read_lane==COLS-1 || post_channel==channels-1;
    wire [31:0] post_raw=results[read_slot][read_lane*32+:32];
    wire signed [31:0] post_value=relu_enable && !q_enable && $signed(post_raw)<0 ? 32'd0 : post_raw;
    wire [15:0] post_addr=dst+(result_pos[read_slot]*channels+post_channel)*(dst32 ? 4 : 1);
    wire rq_valid; wire [7:0] rq_data; wire [15:0] rq_addr;
    wire signed [7:0] effective_min=relu_enable && clamp_min<0 ? 8'sd0 : clamp_min;
    requant_lane #(.TAG_WIDTH(16)) i_requant (
        .clk(clk),.rst(rst),.in_valid(post_fire && q_enable),.in_value(post_raw),
        .in_multiplier(mult_cache[read_lane]),.in_shift(shift_cache[read_lane]),
        .activation_min(effective_min),.activation_max(clamp_max),.in_tag(post_addr),
        .out_valid(rq_valid),.out_data(rq_data),.out_tag(rq_addr));
    wire write_push=state==EXECUTE && (rq_valid || (post_fire && !q_enable));
    assign w_valid=state==EXECUTE && write_count!=0;
    assign w_addr=write_addr[write_head];
    assign w_data=write_data[write_head];
    assign w_mask=write_mask[write_head];

    // Activation gather. A four-byte consecutive group uses one synchronous
    // read. Boundary/nonconsecutive groups use scalar reads with zero masks.
    reg [4:0] issue_p;
    reg [1:0] gather_state,gather_row;
    reg [31:0] gathered;
    reg [15:0] gather_addr[0:3];
    reg [3:0] gather_mask;
    reg gather_contiguous;
    reg [1:0] gather_first;
    wire gather_return=gather_state==2 && a_rsp_valid && (gather_contiguous || gather_row==3);
    wire gather_full=gather_state==3 || gather_return;
    reg [31:0] returned_vector;
    integer gv;
    always @* begin
        returned_vector={a_data[7:0],gathered[23:0]};
        if(gather_contiguous) for(gv=0;gv<4;gv=gv+1)
            returned_vector[gv*8+:8]=gather_mask[gv] ? (a_data>>((gv-gather_first)*8)) : 0;
    end
    wire [31:0] remaining=positions-issue_tile*TILE;
    reg [4:0] tile_positions;
    reg last_group;
    wire [1:0] issue_slot=pool_enable ? issue_p[3:2] : issue_p[1:0];
    wire reserve_slot=!pool_enable || issue_p[1:0]==0;
    wire slot_available=!last_group || !reserve_slot || !slot_busy[issue_slot];
    wire issue_can_fire=mac_op && issue_more && gather_full && bank_ready[issue_seq[0]] && slot_available;
    wire issue_fire=state==EXECUTE && issue_can_fire;
    // First and last flags plus position (six bits total).
    wire [5:0] in_tag={issue_g==0,last_group,issue_p[3:0]};
    wire out_valid,out_bank; wire [5:0] out_tag; wire [COLS*18-1:0] out_sum;
    npu_mac_array #(.ARRAY_ROWS(4),.ARRAY_COLS(COLS),.ACC_WIDTH(18),.TAG_WIDTH(6)) i_array (
        .clk(clk),.rst(rst),.weight_we(array_weight_we),.weight_bank(load_bank),
        .weight_row(load_row),.weight_data(array_weights),.in_valid(issue_fire),
        .in_bank(issue_seq[0]),.in_act(gather_return ? returned_vector : gathered),.in_tag(in_tag),
        .out_valid(out_valid),.out_bank(out_bank),.out_tag(out_tag),.out_psum(out_sum));
    wire [3:0] ret_p=out_tag[3:0];
    wire ret_last=out_tag[4],ret_first=out_tag[5];
    wire [1:0] ret_slot=pool_enable ? ret_p[3:2] : ret_p[1:0];
    // Declare the delayed tile tag before its use for Genus 19 compatibility.
    localparam LAT=4+COLS-1;
    reg [15:0] tile_pipe[0:LAT-1];
    wire [15:0] ret_tile=tile_pipe[LAT-1];
    reg signed [PSUM_BITS-1:0] reduced;
    reg signed [31:0] biased;
    integer lane;
    // Layer setup computes strides; group setup computes four kernel offsets.
    // The issue path contains only bounded additions/comparisons, no multipliers.
    reg [7:0] cursor_y,cursor_x,tile_y,tile_x;
    reg [7:0] scan_last_x,element_last_x;
    reg [10:0] kernel_c;
    reg [1:0] kernel_y,kernel_x;
    reg [15:0] kernel_offset,k_offsets[0:3],cursor_base,tile_base;
    reg [2:0] k_ys[0:3],k_xs[0:3];reg [3:0] k_valid;
    reg kernel_prepare;
    reg [15:0] x_step,y_step,origin,scan_jump,pool_scan_jump,kernel_row_jump;
    reg [31:0] scan_product,pool_scan_product;
    reg [10:0] prep_kc;
    reg [2:0] prep_kx,prep_ky;
    reg [15:0] prep_offset,prep_offsets[0:3];
    reg [2:0] prep_ys[0:3],prep_xs[0:3];reg [3:0] prep_valid;
    integer kr;
    always @* begin
        prep_kc=kernel_c;prep_kx=kernel_x;prep_ky=kernel_y;prep_offset=kernel_offset;
        prep_valid=0;
        for(kr=0;kr<4;kr=kr+1)begin
            prep_offsets[kr]=prep_offset;prep_ys[kr]=prep_ky;prep_xs[kr]=prep_kx;
            prep_valid[kr]=issue_g*4+kr<k_count;
            if(prep_kc==ic-16'd1)begin
                prep_kc=0;
                if(prep_kx==kw-8'd1)begin
                    prep_kx=0;prep_ky=prep_ky+3'd1;prep_offset=prep_offset+kernel_row_jump;
                end else begin prep_kx=prep_kx+3'd1;prep_offset=prep_offset+16'd1;end
            end else begin prep_kc=prep_kc+11'd1;prep_offset=prep_offset+16'd1;end
            if(ic==1 && kh==3)begin
                prep_offsets[kr]=kernel_offset+kr;prep_ys[kr]=issue_g;prep_xs[kr]=kr;
                prep_valid[kr]=kr<3;
            end
        end
        if(ic==1 && kh==3)prep_offset=kernel_offset+row_stride;
    end
    reg signed [9:0] cy,cx,y,x;
    reg [4:0] calc_p;
    reg [8:0] calc_y,calc_x,next_y,next_x;
    reg [15:0] calc_base,next_base;
    integer r;
    reg [15:0] calc_addr[0:3];reg [3:0] calc_mask;reg calc_contiguous;
    reg [1:0] calc_first;
    wire has_next=issue_p!=tile_positions-5'd1; // group changes perform bounded offset setup
    always @* begin
        next_y=cursor_y;next_x=cursor_x;next_base=cursor_base;
        if(!pool_enable || issue_p[1:0]==3)begin
            if(cursor_x==scan_last_x)begin next_x=0;next_y=cursor_y+1;end
            else next_x=cursor_x+1;
        end
        if(!pool_enable) next_base=cursor_base+(cursor_x==scan_last_x ? scan_jump : x_step);
        else case(issue_p[1:0])
            0,2:next_base=cursor_base+x_step;
            1:next_base=cursor_base+y_step-x_step;
            3:next_base=cursor_base+(cursor_x==scan_last_x ? pool_scan_jump : x_step-y_step);
        endcase
        calc_p=issue_p;calc_y=cursor_y;calc_x=cursor_x;calc_base=cursor_base;
        if(issue_can_fire && has_next)begin calc_p=issue_p+1;calc_y=next_y;calc_x=next_x;calc_base=next_base;end
        if(pool_enable)begin cy=calc_y*2+calc_p[1];cx=calc_x*2+calc_p[0];end
        else begin cy=calc_y;cx=calc_x;end
        calc_mask=0;
        for(r=0;r<4;r=r+1)begin
            y=(sh[1] ? cy<<<1 : cy)+$signed({1'b0,k_ys[r]})-$signed({1'b0,pt[1:0]});
            x=(sw[1] ? cx<<<1 : cx)+$signed({1'b0,k_xs[r]})-$signed({1'b0,pl[1:0]});
            calc_addr[r]=calc_base+k_offsets[r];
            calc_mask[r]=k_valid[r] && !y[9] && y<$signed({2'b0,ih[7:0]}) &&
                !x[9] && x<$signed({2'b0,iw[7:0]});
        end
        calc_first=0;
        for(r=3;r>=0;r=r-1)if(calc_mask[r])calc_first=r;
        calc_contiguous=1;
        for(r=0;r<4;r=r+1)if(calc_mask[r] && k_offsets[r]!=k_offsets[calc_first]+(r[2:0]-{1'b0,calc_first}))calc_contiguous=0;
    end

    // Standalone operators feed the same four result slots and postprocessor.
    reg [2:0] estate;
    reg [15:0] element_pos;
    reg [$clog2(COLS)-1:0] element_lane;
    reg [1:0] element_tap,element_slot;
    reg [COLS*32-1:0] element_vector;
    reg [7:0] element_y,element_x;
    reg [15:0] element_base,element_col_step,element_row_step,element_row_jump;
    reg [31:0] element_scan_product;
    reg signed [31:0] element_max;
    wire signed [31:0] element_value=src32 ? $signed(a_data) : $signed({{24{a_data[7]}},a_data[7:0]});
    wire signed [31:0] selected_element=(op==POOL && element_tap!=0 && element_max>element_value) ?
        element_max : element_value;
    reg [15:0] eaddr;
    always @* begin
        eaddr=element_base+(element_lane<<(src32 ? 2 : 0));
        if(op==POOL)eaddr=eaddr+(element_tap[1] ? row_stride : 16'd0)+
            (element_tap[0] ? (ic<<(src32 ? 2 : 0)) : 16'd0);
    end

    always @* begin
        m_valid=0; m_addr=0; a_valid=0;
        // The address is meaningful only on a request handshake. Do not add
        // state/valid gating to the data path merely to drive idle addresses 0.
        // A noncontiguous initial request is issued only when mask[0] is set,
        // in which case calc_first is already zero. Otherwise no request uses
        // this address. Select the first valid byte directly so the expensive
        // contiguous test does not feed the activation BRAM address mux.
        a_addr=mac_op ? (gather_state==1 ? gather_addr[gather_row] :
            calc_addr[calc_first]) : eaddr;
        if(state==FREQ) begin m_valid=1; m_addr=pc*64+fetch_word*4; end
        // The model store accepts one read per cycle: request the next
        // descriptor/parameter word in the cycle the previous one returns.
        else if(state==FWAIT && m_rsp_valid && fetch_word!=15) begin
            m_valid=1; m_addr=pc*64+(fetch_word+1'b1)*4;
        end
        else if(state==PREQ) begin m_valid=1; m_addr=params+param_channel*12+param_word*4; end
        else if(state==PWAIT && m_rsp_valid && !last_param && !param_bad) begin
            m_valid=1; m_addr=params+next_param_channel*12+next_param_word*4;
        end
        else if(state==EXECUTE && mac_op && lstate==L_REQ) begin
            m_valid=1; m_addr=load_address+load_half*4;
        end else if(state==EXECUTE && mac_op && lstate==L_WAIT && m_rsp_valid && COLS==8 && !load_half && !narrow) begin
            m_valid=1; m_addr=load_address+4;
        end else if(state==EXECUTE && mac_op && array_weight_we && load_row!=3) begin
            m_valid=1; m_addr=load_address+row_bytes;
        end
        if(state==EXECUTE) begin
            if(mac_op && !kernel_prepare && issue_more && (gather_state==0 || (issue_fire && has_next))) begin
                a_valid=calc_contiguous ? |calc_mask : calc_mask[0];
            end else if(mac_op && !kernel_prepare && gather_state==1) begin
                a_valid=gather_mask[gather_row];
            end else if(!mac_op && estate==1) begin a_valid=1; end
        end
    end

    task automatic begin_tile;
        begin
            load_seq<=0; issue_seq<=0; load_g<=0; issue_g<=0; load_tile<=0; issue_tile<=0;
            load_row<=0; load_half<=0; lstate<=L_IDLE; load_more<=1; issue_more<=1;
            bank_ready<=0; inflight[0]<=0; inflight[1]<=0; issue_p<=0;
            gather_state<=0; gather_row<=0; slot_busy<=0; slot_ready<=0;
            read_slot<=0; read_lane<=0; rq_pending<=0;
            write_head<=0;write_tail<=0;write_count<=0;
            estate<=0; element_pos<=0; element_lane<=0; element_tap<=0; element_slot<=0;
            cursor_y<=0; cursor_x<=0; tile_y<=0; tile_x<=0;
            tile_positions<=TILE;last_group<=0;
            kernel_c<=0; kernel_x<=0; kernel_y<=0;kernel_offset<=0;kernel_prepare<=1; element_y<=0; element_x<=0;
        end
    endtask
    task automatic fail(input [4:0] code);
        begin error<=code; error_op<=pc; state<=FAILED; end
    endtask
    wire compute_drained=write_count==0 && (mac_op ? (!issue_more && inflight[0]==0 && inflight[1]==0 &&
        slot_busy==0 && rq_pending==0 && !rq_valid && lstate==L_IDLE && !load_more) :
        (element_pos==dh*dw && estate==0 && slot_busy==0 && rq_pending==0 && !rq_valid));
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
        (state==FREQ || state==FWAIT ? 1'b0 : q_enable),
        (state==FREQ || state==FWAIT ? 3'b0 : op),pc[3:0],state,
        (write_fire ? w_mask : 4'b0),(write_fire ? w_addr : 16'b0),
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
            q_enable<=0;relu_enable<=0;pool_enable<=0;mac_op<=0;
            validation_done<=0; validation_ok<=0; done<=0; error<=0; error_op<=0;
            cycles<=0; issues<=0; model_reads<=0; act_reads<=0; act_writes<=0;
            known_valid<=0; known_i32<=0; oct<=0; param_channel<=0; param_word<=0;
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
                    known_valid<=1; known_i32<=input_i32;
                    known_base[0]<=input_base; known_size[0]<=input_bytes;
                    if(op_count==0 || op_count>MAX_OPS || model_bytes>MODEL_BYTES ||
                       model_bytes<op_count*64 || input_bytes==0 || output_bytes==0 ||
                       input_base[1:0]!=0 || output_base[1:0]!=0 ||
                       {1'b0,input_base}+{1'b0,input_bytes}>ACT_BYTES ||
                       {1'b0,output_base}+{1'b0,output_bytes}>ACT_BYTES) begin
                        error<=1; state<=FAILED;
                    end
                end
                FREQ: if(m_ready) state<=FWAIT;
                FWAIT: if(m_rsp_valid) begin
                    d[fetch_word]<=m_data;
                    if(fetch_word==15) begin
                        mac_op<=d[0][7:0]==CONV || d[0][7:0]==FC;
                        q_enable<=d[0][8] || d[0][7:0]==REQUANT;
                        relu_enable<=d[0][9] || d[0][7:0]==RELU;
                        pool_enable<=d[0][10] || d[0][7:0]==POOL;
                        state<=BASIC;
                    end
                    else begin fetch_word<=fetch_word+1'b1; state<=m_ready ? FWAIT : FREQ; end
                end
                BASIC: if(basic_bad) fail(2); else begin
                    oh<=xoh;ow<=xow;dh<=xdh;dw<=xdw;k_count<=xk;
                    groups<=(xk+3)/4;octs<=(channels+COLS-1)/COLS;
                    src_pixels<=ih*iw;dst_pixels<=xdh*xdw;state<=DERIVE;
                end
                DERIVE:begin
                    src_span<=src_pixels*ic*(src32 ? 4 : 1);
                    dst_span<=dst_pixels*channels*(dst32 ? 4 : 1);
                    weight_span<=((oc+COLS-1)/COLS)*groups*4*COLS-(COLS==8 && oc%8!=0 && oc%8<=4 ? groups*16 : 0);param_span<=channels*12;
                    row_stride<=iw*ic*(src32 ? 4 : 1);
                    positions<=dst_pixels*(pool_enable ? 4 : 1);state<=CHECK;
                end
                CHECK: begin
                    if(shape_bad) fail(3);
                    else if(range_bad) fail(4);
                    else if(!source_known) fail(5);
                    else if(pc==op_count-1 && (dst!=output_base || xdst!=output_bytes)) fail(3);
                    else begin
                        for(v=0;v<=MAX_OPS;v=v+1)
                            if(known_valid[v] && $signed({1'b0,dst})<$signed({1'b0,known_base[v]})+$signed({1'b0,known_size[v]}) &&
                               $signed({1'b0,known_base[v]})<$signed({1'b0,dst})+xdst) known_valid[v]<=0;
                        known_base[pc+1]<=dst; known_size[pc+1]<=xdst;
                        known_valid[pc+1]<=1; known_i32[pc+1]<=dst32;
                        oct<=0; param_channel<=0; param_word<=0;
                        if(need_params) state<=PREQ;
                        else state<=checking ? NEXT : SETUP;
                    end
                end
                PREQ: if(m_ready) state<=PWAIT;
                PWAIT: if(m_rsp_valid) begin
                    if(q_enable && ((param_word==1 && m_data[31]) ||
                        (param_word==2 && ($signed(m_data)<-31 || $signed(m_data)>31)))) fail(6);
                    else begin
                        if(!checking) case(param_word)
                            0:bias_cache[param_channel%COLS]<=m_data;
                            1:mult_cache[param_channel%COLS]<=m_data;
                            2:shift_cache[param_channel%COLS]<=m_data[5:0];
                        endcase
                        if(last_param) state<=checking ? NEXT : SETUP;
                        else begin
                            param_word<=next_param_word; param_channel<=next_param_channel;
                            state<=m_ready ? PWAIT : PREQ;
                        end
                    end
                end
                SETUP: begin
                    begin_tile;state<=INIT_A;
                    x_step<=sw==2 ? ic<<1 : ic;y_step<=sh==2 ? row_stride<<1 : row_stride;
                    element_col_step<=ic<<((src32 ? 2 : 0)+(op==POOL ? 1 : 0));
                    element_row_step<=row_stride<<(op==POOL);
                    weight_tile_base<=weights+oct*groups*4*COLS;
                    load_address<=weights+oct*groups*4*COLS;
                end
                INIT_A:begin
                    scan_last_x<=(pool_enable ? dw : ow)-1; element_last_x<=dw-1;
                    origin<=src-(pt==0 ? 16'd0 : pt==1 ? row_stride : row_stride<<1)-
                                (pl==0 ? 16'd0 : pl==1 ? ic : ic<<1);
                    scan_product<=(ow-1)*x_step;pool_scan_product<=(dw*2-1)*x_step;
                    element_scan_product<=(dw-1)*element_col_step;state<=INIT_B;
                end
                INIT_B:begin
                    cursor_base<=origin;tile_base<=origin;scan_jump<=y_step-scan_product;
                    pool_scan_jump<=y_step-pool_scan_product;
                    kernel_row_jump<=row_stride-(kw==3 ? ic*3 : ic)+1;
                    element_base<=src+((oct*COLS)<<(src32 ? 2 : 0));
                    element_row_jump<=element_row_step-element_scan_product;state<=EXECUTE;
                end
                EXECUTE: begin
                    write_count<=write_count+write_push-write_fire;
                    if(write_fire)write_head<=write_head+1'b1;
                    if(write_push)begin
                        write_addr[write_tail]<=q_enable ? rq_addr : post_addr;
                        write_data[write_tail]<=q_enable ? {24'd0,rq_data} : post_value;
                        write_mask[write_tail]<=dst32 ? 4'b1111 : 4'b0001;
                        write_tail<=write_tail+1'b1;
                    end
                    inflight[0]<=inflight[0]+(issue_fire && !issue_seq[0])-(out_valid && !out_bank);
                    inflight[1]<=inflight[1]+(issue_fire && issue_seq[0])-(out_valid && out_bank);
                    rq_pending<=rq_pending+(post_fire && q_enable)-rq_valid;
                    if(post_fire) begin
                        if(post_last) begin
                            read_lane<=0; slot_ready[read_slot]<=0; slot_busy[read_slot]<=0;
                            read_slot<=read_slot+1'b1;
                        end else read_lane<=read_lane+1'b1;
                    end
                    if(mac_op) begin
                        if(kernel_prepare)begin
                            for(v=0;v<4;v=v+1)begin
                                k_offsets[v]<=prep_offsets[v];k_ys[v]<=prep_ys[v];k_xs[v]<=prep_xs[v];
                            end
                            k_valid<=prep_valid;kernel_prepare<=0;
                            tile_positions<=remaining<TILE ? remaining[4:0] : TILE;
                            last_group<=issue_g==groups-16'd1;
                        end
                        if(load_start) lstate<=L_REQ;
                        if(lstate==L_REQ && m_ready) lstate<=L_WAIT;
                        if(lstate==L_WAIT && m_rsp_valid) begin
                            if(COLS==8 && !load_half && !narrow) begin load_low<=m_data; load_half<=1; lstate<=m_ready ? L_WAIT : L_REQ; end
                            else if(!array_weight_we) begin load_data<=response_row; lstate<=L_HAVE; end
                        end
                        if(array_weight_we) begin
                            load_half<=0;
                            load_address<=load_row==3 && load_g==groups-1 ? weight_tile_base : load_address+row_bytes;
                            if(load_row==3) begin
                                load_row<=0; lstate<=L_IDLE; bank_ready[load_bank]<=1; load_seq<=load_seq+1'b1;
                                if(load_g!=groups-1) load_g<=load_g+1'b1;
                                else begin
                                    load_g<=0;
                                    if((load_tile+1)*TILE<positions) load_tile<=load_tile+1'b1;
                                    else load_more<=0;
                                end
                            end else begin load_row<=load_row+1'b1; lstate<=m_ready ? L_WAIT : L_REQ; end
                        end
                        if(issue_more && !kernel_prepare) case(gather_state)
                            0: begin
                                for(v=0;v<4;v=v+1) gather_addr[v]<=calc_addr[v];
                                gather_mask<=calc_mask; gather_contiguous<=calc_contiguous;
                                gather_first<=calc_first;
                                gathered<=0; gather_row<=0;
                                gather_state<=calc_mask==0 ? 3 : (a_valid && a_ready ? 2 : 1);
                            end
                            1: if(!gather_mask[gather_row]) begin
                                if(gather_row==3) gather_state<=3;
                                else gather_row<=gather_row+1'b1;
                            end else if(a_ready) gather_state<=2;
                            2: if(a_rsp_valid) begin
                                if(gather_contiguous) begin gathered<=returned_vector; gather_state<=3; end
                                else begin
                                    gathered[gather_row*8+:8]<=a_data[7:0];
                                    if(gather_row==3) gather_state<=3;
                                    else begin gather_row<=gather_row+1'b1; gather_state<=1; end
                                end
                            end
                            default: ;
                        endcase
                        if(issue_fire) begin
                            issues<=issues+1;
                            for(v=0;v<4;v=v+1) gather_addr[v]<=calc_addr[v];
                            gather_mask<=calc_mask; gather_contiguous<=calc_contiguous; gather_first<=calc_first;
                            gathered<=0; gather_row<=0;
                            gather_state<=!has_next ? 0 : calc_mask==0 ? 3 : (a_valid && a_ready ? 2 : 1);
                            if(last_group && reserve_slot) slot_busy[issue_slot]<=1;
                            if(issue_p==tile_positions-1) begin
                                issue_p<=0; bank_ready[issue_seq[0]]<=0; issue_seq<=issue_seq+1'b1;
                                if(!last_group) begin
                                    issue_g<=issue_g+1'b1;
                                    kernel_c<=prep_kc;kernel_x<=prep_kx;kernel_y<=prep_ky;kernel_offset<=prep_offset;
                                    kernel_prepare<=1;cursor_base<=tile_base;
                                    cursor_y<=tile_y; cursor_x<=tile_x;
                                end
                                else begin
                                    issue_g<=0;
                                    kernel_c<=0;kernel_x<=0;kernel_y<=0;kernel_offset<=0;kernel_prepare<=1;
                                    cursor_base<=next_base;tile_base<=next_base;
                                    cursor_y<=next_y; cursor_x<=next_x; tile_y<=next_y; tile_x<=next_x;
                                    if((issue_tile+1)*TILE<positions) issue_tile<=issue_tile+1'b1;
                                    else issue_more<=0;
                                end
                            end else begin issue_p<=issue_p+1'b1;cursor_base<=next_base; cursor_y<=next_y; cursor_x<=next_x; end
                        end
                        if(out_valid) begin
                            for(lane=0;lane<COLS;lane=lane+1) begin
                                reduced=$signed(ret_first ? {PSUM_BITS{1'b0}} : psum[ret_p][lane*PSUM_BITS+:PSUM_BITS])+
                                    $signed(out_sum[lane*18+:18]);
                                biased=$signed(reduced)+$signed(bias_cache[lane]);
                                if(!ret_last) psum[ret_p][lane*PSUM_BITS+:PSUM_BITS]<=reduced;
                                else if(!pool_enable || ret_p[1:0]==0 ||
                                    biased>$signed(results[ret_slot][lane*32+:32]))
                                    results[ret_slot][lane*32+:32]<=biased;
                            end
                            if(ret_last && (!pool_enable || ret_p[1:0]==3)) begin
                                slot_ready[ret_slot]<=1;
                                // Final group cannot leave this tile until all
                                // its issues are sent; capture its tile in tags below.
                                result_pos[ret_slot]<=pool_enable ? (ret_tile*TILE+ret_p)/4 : ret_tile*TILE+ret_p;
                            end
                        end
                    end else begin
                        case(estate)
                            0: if(element_pos<dh*dw && !slot_busy[element_slot]) begin
                                slot_busy[element_slot]<=1; element_lane<=0; element_tap<=0;
                                element_vector<=0; estate<=1;
                            end
                            1: if(a_ready) estate<=2;
                            2: if(a_rsp_valid) begin
                                element_max<=selected_element;
                                if(op==POOL && element_tap!=3) begin element_tap<=element_tap+1'b1; estate<=1; end
                                else begin
                                    element_vector[element_lane*32+:32]<=selected_element;
                                    element_tap<=0;
                                    if(element_lane==COLS-1 || oct*COLS+element_lane==channels-1) estate<=3;
                                    else begin element_lane<=element_lane+1'b1; estate<=1; end
                                end
                            end
                            3: begin
                                results[element_slot]<=element_vector; result_pos[element_slot]<=element_pos;
                                slot_ready[element_slot]<=1; element_pos<=element_pos+1'b1;
                                element_base<=element_base+(element_x==element_last_x ? element_row_jump : element_col_step);
                                if(element_x==element_last_x) begin element_x<=0; element_y<=element_y+1'b1; end
                                else element_x<=element_x+1'b1;
                                element_slot<=element_slot+1'b1; estate<=0;
                            end
                        endcase
                    end
                    if(drained) begin
                        if(oct==octs-1) state<=NEXT;
                        else begin
                            oct<=oct+1'b1; param_channel<=(oct+1)*COLS; param_word<=0;
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
    // Tile tag is delayed independently because the shared array tag holds
    // only hot first/last/position fields.
    integer t;
    always @(posedge clk) begin
        tile_pipe[0]<=issue_tile;
        for(t=1;t<LAT;t=t+1) tile_pipe[t]<=tile_pipe[t-1];
    end
    initial if((COLS!=4 && COLS!=8) || TILE!=16 || MAX_K>1024 || MAX_OPS>16 || PSUM_BITS<26)
        $fatal(1,"unsupported program-core geometry");
endmodule

