`timescale 1ns/1ps
// Transaction equivalence of the SRAM-backed provenance lookup against the
// frozen flip-flop reference: every search returns the same known flags, and
// every saved/invalidated record matches, under random read/write stalls.
module tb_npu_tensor_lookup;
 parameter MAX_OPS=128;
 reg clk=0,rst=1,seed=0,start=0,save=0;
 always #5 clk=~clk;
 reg [7:0] pc=0;
 reg input_i32=0,src_i32=0,dst_i32=0,add_op=0;
 reg [15:0] input_first=0,input_end=512,src=0,src_end=16,src2=0,src2_end=16,dst=0,dst_end=16;
 wire done,known,known2,busy,ref_done,ref_known,ref_known2;
 wire rd_valid,wr_valid;wire [7:0] rd_index,wr_index;wire [31:0] wr_data;
 reg rd_ready=0,wr_ready=0,rd_rsp_valid=0;reg [31:0] rd_data=0;
 npu_tensor_lookup #(.MAX_OPS(MAX_OPS)) dut(
  .clk(clk),.rst(rst),.seed(seed),.start(start),.save(save),.no_write(1'b0),.pc(pc),
  .input_i32(input_i32),.src_i32(src_i32),.dst_i32(dst_i32),.add_op(add_op),
  .input_first(input_first),.input_end(input_end),.src(src),.src_end(src_end),
  .src2(src2),.src2_end(src2_end),.dst(dst),.dst_end(dst_end),
  .done(done),.source_known(known),.source2_known(known2),.busy(busy),
  .rd_valid(rd_valid),.rd_ready(rd_ready),.rd_index(rd_index),.rd_rsp_valid(rd_rsp_valid),.rd_data(rd_data),
  .wr_valid(wr_valid),.wr_ready(wr_ready),.wr_index(wr_index),.wr_data(wr_data));
 npu_tensor_lookup_reference #(.MAX_OPS(MAX_OPS)) reference(
  .clk(clk),.rst(rst),.seed(seed),.start(start),.save(save),.pc(pc),
  .input_i32(input_i32),.src_i32(src_i32),.dst_i32(dst_i32),.add_op(add_op),
  .input_first(input_first),.input_end(input_end),.src(src),.src_end(src_end),
  .src2(src2),.src2_end(src2_end),.dst(dst),.dst_end(dst_end),
  .done(ref_done),.source_known(ref_known),.source2_known(ref_known2));

 // Variable-latency word memory: reads observe every accepted write.
 reg [31:0] mem[0:255];
 integer latency=-1,rng_seed=20261116,unused,reads=0,writes=0,rd_stalls=0,wr_stalls=0;
 reg [7:0] read_at;
 always @(negedge clk) begin
  rd_ready=!rst && latency<0 && !rd_rsp_valid && ($urandom%4!=0);
  wr_ready=!rst && ($urandom%3!=0);
 end
 always @(posedge clk) begin
  rd_rsp_valid<=0;
  if(rst) latency<=-1;
  else begin
   if(rd_valid && !rd_ready) rd_stalls=rd_stalls+1;
   if(wr_valid && !wr_ready) wr_stalls=wr_stalls+1;
   if(rd_valid && rd_ready) begin
    if(rd_index>MAX_OPS) $fatal(1,"record read %0d beyond MAX_OPS",rd_index);
    read_at<=rd_index; latency<=$urandom%4; reads=reads+1;
   end else if(latency>0) latency<=latency-1;
   else if(latency==0) begin rd_data<=mem[read_at]; rd_rsp_valid<=1; latency<=-1; end
   if(wr_valid && wr_ready) begin
    if(wr_index>MAX_OPS) $fatal(1,"record write %0d beyond MAX_OPS",wr_index);
    mem[wr_index]<=wr_data; writes=writes+1;
   end
  end
 end

 integer epoch,n,waits,searches=0,known_hits=0,known2_hits=0;
 // Decoded memory record must equal the reference record after every search/save.
 task automatic compare_records(input integer upto);
  integer r; reg [33:0] e; reg [31:0] g;
  begin
   for(r=0;r<=upto;r=r+1) begin
    e=reference.records[r]; g=mem[r];
    if(g[31]!==e[33] || (e[33] && (g[30]!==e[32] || {1'b0,g[14:0]}!==e[15:0] || {1'b0,g[29:15]}+16'd1!==e[31:16])))
     $fatal(1,"record %0d mismatch got=%h expected=%h epoch=%0d pc=%0d",r,g,e,epoch,pc);
   end
  end
 endtask

 reg got_done,got_ref;reg [1:0] got_flags,ref_flags;
 task automatic wait_idle;
  begin waits=0; while(busy) begin @(negedge clk); waits=waits+1; if(waits>64) $fatal(1,"lookup write never drained"); end end
 endtask
 initial begin
  unused=$urandom(rng_seed);
  for(n=0;n<256;n=n+1) mem[n]=32'hdeadbeef;  // stale SRAM contents must never be trusted
  repeat(3)@(negedge clk);rst=0;
  for(epoch=0;epoch<16;epoch=epoch+1)begin
   @(negedge clk);seed=1;input_i32=epoch[0];
   // Cover the top of the 15-bit nibble space, including an end of exactly 32768.
   input_first=epoch==15 ? 16'd32768-16'd512 : (epoch%4)*128+(epoch>=8 ? 16'd20000 : 16'd0);
   input_end=input_first+512;
   @(negedge clk);seed=0;
   for(n=0;n<MAX_OPS;n=n+1)begin
    pc=n;src_i32=$urandom%2;dst_i32=$urandom%2;add_op=$urandom%2;
    case(n%3)
     0: begin src=input_first;src_end=input_end;end
     1: begin src=dst;src_end=dst_end;end
     2: begin src=16'hf000+($urandom%128)*8;src_end=src+8;end
    endcase
    src2=input_first+8;src2_end=input_end-8;
    dst=epoch>=8 ? 16'd20000+($urandom%1536)*8 : ($urandom%128)*8;
    dst_end=dst+1+($urandom%128);
    if(n%17==16) dst_end=16'd32768;
    // The first search of an epoch starts while the seed record write may still wait.
    start=1;@(negedge clk);start=0;waits=0;got_done=0;got_ref=0;
    while(!(got_done && got_ref))begin
     @(posedge clk);#1;
     if(done)begin got_done=1;got_flags={known,known2};end
     if(ref_done)begin got_ref=1;ref_flags={ref_known,ref_known2};end
     waits=waits+1;if(waits>12*(MAX_OPS+1)+64)$fatal(1,"lookup timeout");
    end
    @(negedge clk);
    if(got_flags!==ref_flags)
     $fatal(1,"lookup result mismatch max=%0d epoch=%0d pc=%0d got=%b expected=%b",MAX_OPS,epoch,pc,got_flags,ref_flags);
    searches=searches+1;known_hits=known_hits+got_flags[1];known2_hits=known2_hits+(add_op && got_flags[0]);
    if(busy)$fatal(1,"done raised with a record write still pending");
    compare_records(n);
    save=1;@(negedge clk);save=0;
    wait_idle;compare_records(n+1);
   end
   // Abort an in-progress search, then reset and reseed the next epoch.
   if(epoch[0])begin
    pc=MAX_OPS-1;start=1;@(negedge clk);start=0;repeat(3)@(negedge clk);
   end
   @(negedge clk);rst=1;@(negedge clk);rst=0;
   if(busy || wr_valid || rd_valid)$fatal(1,"reset did not cancel the lookup");
  end
  if(rd_stalls==0 || wr_stalls==0 || known_hits==0 || known_hits==searches || known2_hits==0)
   $fatal(1,"stimulus coverage: rd_stalls=%0d wr_stalls=%0d known=%0d/%0d known2=%0d",
          rd_stalls,wr_stalls,known_hits,searches,known2_hits);
  $display("PASS TENSOR_LOOKUP_EQUIVALENCE max_ops=%0d searches=%0d reads=%0d writes=%0d rd_stalls=%0d wr_stalls=%0d known=%0d",
           MAX_OPS,searches,reads,writes,rd_stalls,wr_stalls,known_hits);
  $finish;
 end
 initial begin #400000000;$fatal(1,"lookup equivalence global timeout");end
endmodule
