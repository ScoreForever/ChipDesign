`timescale 1ns/1ps
// One synchronous 32-bit 1RW SRAM, 64-byte direct-mapped read cache and a
// bounded nibble-combining write queue. No full-tensor standard-cell array.
//
// Reads observe previously accepted writes. Simultaneous read/write acceptance
// uses read-before-write ordering. Eight-nibble reads and masked writes can cross
// a word; addresses wrap at capacity for unused tail nibbles.
// Addresses/masks are in nibbles; BYTES/CACHE_BYTES are physical byte sizes.
//
// flush commits partial combining words. Owners wait for idle before done or
// ownership changes. Reset cancels queue/cache/responses, retaining SRAM data.
//
// SIDE_PORT adds an uncached word read port for an immutable region (the
// model image). It bypasses the cache, write queue and forwarding, so callers
// must guarantee no queued write targets what it reads. It uses a macro bank
// only in a cycle the main port does not access that bank; after one waiting
// cycle the main port is limited to cache hits so the side port cannot starve.
module npu_activation_store #(
    parameter BYTES=8192, CACHE_BYTES=64, WRITE_DEPTH=8, SIDE_PORT=0
) (
    input wire clk,rst,
    input wire read_valid, output wire read_ready, input wire [15:0] read_addr,
    output wire rsp_valid, input wire rsp_ready, output wire [31:0] rsp_data,
    input wire write_valid, output wire write_ready, input wire [15:0] write_addr,
    input wire [31:0] write_data, input wire [7:0] write_mask,
    input wire flush, output wire idle,
    input wire side_valid, output wire side_ready, input wire [15:0] side_addr,
    output wire side_rsp_valid, output wire [31:0] side_rsp_data
);
    localparam AW=$clog2(BYTES/4), CW=CACHE_BYTES/4, CI=$clog2(CW);
    localparam CT=AW>CI ? AW-CI : 1, QW=$clog2(WRITE_DEPTH);
    localparam S_IDLE=0,S_CACHED=1,S_FIRST=2,S_MEMORY=3,S_HELD=4,S_RMW=5;
    reg [2:0] state;
    reg [AW-1:0] q_addr[0:WRITE_DEPTH-1];
    reg [31:0] q_data[0:WRITE_DEPTH-1];
    reg [7:0] q_mask[0:WRITE_DEPTH-1];
    reg [QW-1:0] head,tail;
    reg [QW:0] count;
    wire [QW-1:0] last=tail-1'b1;

    reg [31:0] cache_data[0:CW-1];
    reg [CT-1:0] cache_tag[0:CW-1];
    reg [CW-1:0] cache_valid;
    wire [AW-1:0] read_word0=read_addr[AW+2:3];
    wire [AW-1:0] read_word1=read_word0+1'b1;
    wire hit0=cache_valid[read_word0[CI-1:0]] &&
        cache_tag[read_word0[CI-1:0]]==(read_word0>>CI);
    wire hit1=cache_valid[read_word1[CI-1:0]] &&
        cache_tag[read_word1[CI-1:0]]==(read_word1>>CI);
    wire need_second=read_addr[2:0]!=0;
    wire all_hit=hit0 && (!need_second || hit1);

    // Normalize eight-nibble masks to at most two aligned word writes.
    wire [63:0] shifted_data={32'd0,write_data}<<(write_addr[2:0]*4);
    wire [15:0] shifted_mask={8'd0,write_mask}<<write_addr[2:0];
    wire first_low=|shifted_mask[7:0];
    wire [AW-1:0] write_word0=write_addr[AW+2:3]+(!first_low);
    wire [AW-1:0] write_word1=write_addr[AW+2:3]+1'b1;
    wire [31:0] write_value0=first_low ? shifted_data[31:0] : shifted_data[63:32];
    wire [7:0] write_mask0=first_low ? shifted_mask[7:0] : shifted_mask[15:8];
    wire two_writes=first_low && |shifted_mask[15:8];
    wire same_tail=count!=0 && q_addr[last]==write_word0;
    wire response_slot=state==S_IDLE ||
        ((state==S_CACHED || state==S_HELD || state==S_MEMORY) && rsp_ready);
    // A pending multiword snapshot excludes new writes until it is complete.
    wire memory_locked=state==S_FIRST || state==S_RMW || (state==S_MEMORY && !rsp_ready);
    wire partial_byte=(q_mask[head][0]^q_mask[head][1]) || (q_mask[head][2]^q_mask[head][3]) ||
        (q_mask[head][4]^q_mask[head][5]) || (q_mask[head][6]^q_mask[head][7]);
    wire flushable=count!=0 && (flush || count>1 || q_mask[head]==8'hff ||
        (write_valid && write_mask0!=0 && !same_tail));
    // Read misses stall the compute array; queued writes do not. A waiting
    // miss therefore gets the single SRAM port first, and new writes wait one
    // cycle, unless the queue is nearly full or a flush must drain it. Reads
    // still observe queued data through forwarding. A write is never accepted
    // in the cycle a miss issues, so the cache line it fills cannot go stale.
    // Writes cannot starve: every miss holds the port for its response cycle,
    // and the producer stops reading once its output FIFO fills.
    // A side read that already waited one cycle claims the macros next.
    reg side_wait;
    wire side_claim=SIDE_PORT && side_valid && side_wait;
    wire read_miss=!rst && read_valid && !all_hit && response_slot;
    wire urgent=flush || count>=WRITE_DEPTH-2;
    wire defer=read_miss && !urgent;
    wire drain=!rst && !memory_locked && flushable && !defer && !side_claim && (!partial_byte || response_slot);
    wire start_rmw=drain && partial_byte;
    wire pop=(drain && !partial_byte) || (!rst && state==S_RMW);
    wire merge=same_tail && !(pop && count==1) && write_mask0!=0;
    wire [1:0] pushes=(write_mask0!=0 && !merge)+{1'b0,two_writes};
    wire [QW+1:0] available=WRITE_DEPTH-count+pop;
    assign write_ready=!rst && !memory_locked && available>=pushes && !defer && !start_rmw;
    wire wf=write_valid && write_ready;
    assign read_ready=!rst && !start_rmw && response_slot && (all_hit || (!drain && !wf && !side_claim));
    wire rf=read_valid && read_ready;
    wire physical_read=rf && !all_hit;

    function automatic [31:0] overlay(
        input [31:0] original,input [31:0] forwarded,input [7:0] mask
    );
        integer b;
        begin
            overlay=original;
            for(b=0;b<8;b=b+1)
                if(mask[b])overlay[b*4+:4]=forwarded[b*4+:4];
        end
    endfunction
    // Compare physical queue slots in parallel. Logical age wraps at head:
    // entries below head are newer than entries at/above it. Select the last
    // matching nibble in that order without rotating every data word or
    // cascading WRITE_DEPTH overlays on the late read-address path.
    wire [7:0] forward_mask0,forward_mask1;
    wire [31:0] forward_data0,forward_data1;
    wire [WRITE_DEPTH-1:0] before_head,match0,match1;
    genvar fs,fl,fb;
    generate
        for(fs=0;fs<WRITE_DEPTH;fs=fs+1)begin:g_forward_match
            wire [QW-1:0] age=fs-head;
            assign before_head[fs]=fs<head;
            assign match0[fs]=(age<count) && q_addr[fs]==read_word0;
            assign match1[fs]=(age<count) && q_addr[fs]==read_word1;
        end
        for(fl=0;fl<8;fl=fl+1)begin:g_forward_lane
            wire [WRITE_DEPTH-1:0] hits0,hits1,low0,low1,winner0,winner1;
            assign low0=hits0 & before_head;
            assign low1=hits1 & before_head;
            assign forward_mask0[fl]=|hits0;
            assign forward_mask1[fl]=|hits1;
            for(fs=0;fs<WRITE_DEPTH;fs=fs+1)begin:g_priority
                assign hits0[fs]=match0[fs] && q_mask[fs][fl];
                assign hits1[fs]=match1[fs] && q_mask[fs][fl];
                assign winner0[fs]=hits0[fs] && (before_head[fs] ?
                    !(|(low0>>(fs+1))) : (!(|low0) && !(|(hits0>>(fs+1)))));
                assign winner1[fs]=hits1[fs] && (before_head[fs] ?
                    !(|(low1>>(fs+1))) : (!(|low1) && !(|(hits1>>(fs+1)))));
            end
            for(fb=0;fb<4;fb=fb+1)begin:g_bit
                wire [WRITE_DEPTH-1:0] terms0,terms1;
                for(fs=0;fs<WRITE_DEPTH;fs=fs+1)begin:g_data
                    assign terms0[fs]=winner0[fs] && q_data[fs][fl*4+fb];
                    assign terms1[fs]=winner1[fs] && q_data[fs][fl*4+fb];
                end
                assign forward_data0[fl*4+fb]=|terms0;
                assign forward_data1[fl*4+fb]=|terms1;
            end
        end
    endgenerate
    wire [31:0] cached_word0=overlay(cache_data[read_word0[CI-1:0]],forward_data0,forward_mask0);
    wire [31:0] cached_word1=overlay(cache_data[read_word1[CI-1:0]],forward_data1,forward_mask1);
    wire [63:0] cached_pair={cached_word1,cached_word0};

    reg [AW-1:0] saved_word0,saved_word1;
    reg [7:0] saved_mask0,saved_mask1;
    reg [31:0] saved_data0,saved_data1;
    reg [31:0] low_word,high_word,held_data;
    reg [2:0] rotation;
    reg memory_high;

    // Physical-port signal names are stable verification observation points.
    wire sram_we=pop;
    wire sram_en=!rst && (pop || state==S_FIRST || physical_read || start_rmw);
    wire [AW-1:0] sram_addr=(pop || start_rmw) ? q_addr[head] :
        state==S_FIRST ? saved_word1 : (hit0 ? read_word1 : read_word0);
    wire [31:0] sram_q;
    wire [31:0] sram_wdata=state==S_RMW ? overlay(sram_q,q_data[head],q_mask[head]) : q_data[head];
    wire [3:0] sram_wmask={|q_mask[head][7:6],|q_mask[head][5:4],|q_mask[head][3:2],|q_mask[head][1:0]};
    // Side port: one 8 KiB macro per bank; bank exclusivity models the macros
    // even when the behavioral array below could serve both ports.
    localparam NB=BYTES>8192 ? BYTES/8192 : 1;
    wire [AW-1:0] side_word=side_addr[AW+2:3];
    wire main_bank=NB>1 ? sram_addr[AW-1] : 1'b0;
    wire side_bank=NB>1 ? side_word[AW-1] : 1'b0;
    assign side_ready=SIDE_PORT && !rst && !(sram_en && main_bank==side_bank);
    wire side_fire=side_valid && side_ready;
    reg side_rsp_q,side_bank_q;
    always @(posedge clk)begin
        side_rsp_q<=!rst && side_fire;
        side_wait<=!rst && SIDE_PORT && side_valid && !side_ready;
        if(side_fire)side_bank_q<=side_bank;
    end
    assign side_rsp_valid=side_rsp_q;
`ifdef NPU_USE_RA1SHD_ACTIVATION
    wire [31:0] bank_q[0:BYTES/8192-1];
    reg bank_select;
    always @(posedge clk)if(sram_en && !sram_we)bank_select<=sram_addr>>11;
    genvar bank;
    generate for(bank=0;bank<BYTES/8192;bank=bank+1)begin:g_bank
     wire main_here=sram_en && (sram_addr>>11)==bank;
     RA1SHD_2048x32M8 i_sram(
      .CLK(clk),.CEN(~(main_here || (side_fire && side_bank==bank))),.OEN(1'b0),
      .WEN(main_here && sram_we ? ~sram_wmask : 4'hf),.A(main_here ? sram_addr[10:0] : side_word[10:0]),
      .D(sram_wdata),.Q(bank_q[bank]));
    end endgenerate
    assign sram_q=bank_q[BYTES>8192 ? bank_select : 0];
    assign side_rsp_data=bank_q[BYTES>8192 ? side_bank_q : 0];
`else
    reg [31:0] mem[0:BYTES/4-1];
    reg [31:0] memory_q;
    integer mb;
    always @(posedge clk)if(sram_en)begin
        if(sram_we)begin
            for(mb=0;mb<4;mb=mb+1)
                if(sram_wmask[mb])mem[sram_addr][mb*8+:8]<=sram_wdata[mb*8+:8];
        end else memory_q<=mem[sram_addr];
    end
    assign sram_q=memory_q;
    // Second read port of the same array (true dual-port BRAM on FPGA). The
    // bank check above keeps it off any bank the main port reads or writes.
    reg [31:0] side_q;
    always @(posedge clk)if(SIDE_PORT && side_fire)side_q<=mem[side_word];
    assign side_rsp_data=side_q;
`endif
    wire [31:0] memory_word=overlay(sram_q,
        memory_high ? saved_data1 : saved_data0,
        memory_high ? saved_mask1 : saved_mask0);
    wire [63:0] memory_pair=memory_high ? {memory_word,low_word} : {high_word,memory_word};
    assign rsp_valid=!rst && (state==S_CACHED || state==S_MEMORY || state==S_HELD);
    assign rsp_data=state==S_MEMORY ? memory_pair>>(rotation*4) : held_data;
    assign idle=!rst && state==S_IDLE && count==0;

    wire [QW-1:0] second_slot=tail+(!merge && write_mask0!=0);
    wire cache_fill=state==S_FIRST || state==S_MEMORY;
    wire [AW-1:0] fill_word=state==S_FIRST ? saved_word0 :
        (memory_high ? saved_word1 : saved_word0);
    // Called only on the clock edge. A returning read can fill the same cache
    // index as a new store: merge only for the same tag, otherwise preserve the
    // new fill. Stores to unrelated tags do not evict useful input cache lines.
    task automatic update_cached_word(
        input [AW-1:0] address,input [31:0] value,input [7:0] mask
    );
        begin
            if(cache_fill && fill_word[CI-1:0]==address[CI-1:0])begin
                if(fill_word==address)begin
                    cache_valid[address[CI-1:0]]<=1;
                    cache_tag[address[CI-1:0]]<=address>>CI;
                    cache_data[address[CI-1:0]]<=overlay(memory_word,value,mask);
                end
            end else if(cache_valid[address[CI-1:0]] &&
                        cache_tag[address[CI-1:0]]==(address>>CI))begin
                cache_data[address[CI-1:0]]<=overlay(cache_data[address[CI-1:0]],value,mask);
            end
        end
    endtask
    always @(posedge clk)begin
        if(rst)begin
            state<=S_IDLE;head<=0;tail<=0;count<=0;cache_valid<=0;
            saved_word0<=0;saved_word1<=0;saved_mask0<=0;saved_mask1<=0;
            saved_data0<=0;saved_data1<=0;low_word<=0;high_word<=0;
            held_data<=0;rotation<=0;memory_high<=0;
        end else begin
            if(state==S_CACHED || state==S_HELD)begin
                if(rsp_ready)state<=S_IDLE;
            end
            if(state==S_FIRST)begin
                low_word<=memory_word;memory_high<=1;state<=S_MEMORY;
                cache_valid[saved_word0[CI-1:0]]<=1;
                cache_tag[saved_word0[CI-1:0]]<=saved_word0>>CI;
                cache_data[saved_word0[CI-1:0]]<=memory_word;
            end
            if(state==S_MEMORY)begin
                cache_valid[(memory_high ? saved_word1 : saved_word0)&(CW-1)]<=1;
                cache_tag[(memory_high ? saved_word1 : saved_word0)&(CW-1)]<=
                    (memory_high ? saved_word1 : saved_word0)>>CI;
                cache_data[(memory_high ? saved_word1 : saved_word0)&(CW-1)]<=memory_word;
                if(rsp_ready)state<=S_IDLE;
                else begin held_data<=rsp_data;state<=S_HELD;end
            end

            if(state==S_RMW)state<=S_IDLE;
            if(pop)head<=head+1'b1;
            if(wf || pop)count<=count+(wf ? pushes : 0)-pop;
            if(wf)begin
                tail<=tail+pushes;
                if(write_mask0!=0)begin
                    if(merge)begin
                        q_data[last]<=overlay(q_data[last],write_value0,write_mask0);
                        q_mask[last]<=q_mask[last]|write_mask0;
                    end else begin
                        q_addr[tail]<=write_word0;q_data[tail]<=write_value0;q_mask[tail]<=write_mask0;
                    end
                    update_cached_word(write_word0,write_value0,write_mask0);
                end
                if(two_writes)begin
                    q_addr[second_slot]<=write_word1;
                    q_data[second_slot]<=shifted_data[63:32];
                    q_mask[second_slot]<=shifted_mask[15:8];
                    update_cached_word(write_word1,shifted_data[63:32],shifted_mask[15:8]);
                end
            end

            // The physical macro has byte enables: an isolated nibble needs
            // a read/modify/write. Lock both interfaces until its commit so
            // the head and the SRAM snapshot stay coherent.
            if(start_rmw)state<=S_RMW;
            if(rf)begin
                rotation<=read_addr[2:0];
                if(all_hit)begin held_data<=cached_pair>>(read_addr[2:0]*4);state<=S_CACHED;end
                else begin
                    saved_word0<=read_word0;saved_word1<=read_word1;
                    saved_mask0<=forward_mask0;saved_mask1<=forward_mask1;
                    saved_data0<=forward_data0;saved_data1<=forward_data1;
                    low_word<=cached_word0;high_word<=cached_word1;
                    memory_high<=hit0;
                    state<=(!hit0 && need_second && !hit1) ? S_FIRST : S_MEMORY;
                end
            end
        end
    end
    initial begin
        if(BYTES<64 || BYTES>32768 || (BYTES&(BYTES-1))!=0 ||
           CACHE_BYTES<8 || CACHE_BYTES>BYTES || (CACHE_BYTES&(CACHE_BYTES-1))!=0 ||
           WRITE_DEPTH<2 || WRITE_DEPTH>16 || (WRITE_DEPTH&(WRITE_DEPTH-1))!=0)
            $fatal(1,"unsupported activation SRAM geometry");
`ifdef NPU_USE_RA1SHD_ACTIVATION
        if(BYTES!=8192 && BYTES!=16384)$fatal(1,"activation macro banking supports 8192 or 16384 bytes");
`endif
    end
endmodule
