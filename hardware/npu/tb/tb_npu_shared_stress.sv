`timescale 1ns/1ps
// Constrained-random soak of npu_shared_store (both 8 KiB macros, model side
// port, activation cache / write queue / RMW) against an independent nibble
// scoreboard. Model reads, activation reads (any nibble alignment, random
// response backpressure) and masked activation writes (cross-word, cross-bank)
// are issued concurrently; model-image rewrites and flushes happen between
// traffic phases. Every read is checked against the scoreboard snapshot at its
// acceptance; every requester must make progress within MAX_WAIT cycles.
//   +N=<cycles per phase> +PHASES=<n> +SEED=<n> +MODEL_BYTES=<64..8192, pow2>
//   +MR=<pct> +AR=<pct> +AW=<pct> +RR=<pct rsp_ready>   request probabilities
//   +OVERLAP=1  keep traffic running while the model image is rewritten (dirty window)
//   +WLO=<nibble>   lowest write address (WLO=16384 aims every write at the image macro)
//   +SIDE_MAX=<cycles>  bound on a side (model) read blocked by the main port; the
//     store promises a side read that waited one cycle claims the macros next.
// Build with the same defines as the product (CHIPDESIGN_FPGA_MEM, or
// NPU_USE_RA1SHD_MODEL + NPU_USE_RA1SHD_ACTIVATION + the macro model).
module tb_npu_shared_stress;
 // Liveness assumption (system level, not guaranteed by the store alone): an activation
 // read miss loses the port to write drains while the write queue is urgent (>= depth-2).
 // In the NPU the writer is the core, which stops producing once its reads stall, so the
 // queue drains and the read proceeds. An independent producer that keeps the queue
 // urgent while side reads take the same macro (e.g. +WLO=16384 +AW=50 +MR=70) starves
 // reads by construction; such oversubscribed points are characterisation, not gates.
 localparam MAX_WAIT=96;
 reg clk=0,rst=1; always #5 clk=~clk;
 integer n_cycles=20000,phases=4,seed=1,mr_pct=60,ar_pct=50,aw_pct=40,rr_pct=70,mb_arg=8192;
 reg [15:0] model_bytes=8192;
 reg mw=0;reg [15:0] mwa=0;reg [31:0] mwd=0;wire mwr;
 reg mrq=0;reg [15:0] mra=0;wire mrr,mrv;wire [31:0] mrd;
 reg arq=0;reg [15:0] ara=0;wire arr,arv;reg rr=0;wire [31:0] ard;
 reg awq=0;reg [15:0] awa=0;reg [31:0] awd=0;reg [7:0] awm=0;wire awr;
 reg flush=0;wire idle;
 npu_shared_store dut(.clk(clk),.rst(rst),.model_bytes(model_bytes),
  .model_write(mw),.model_write_ready(mwr),.model_write_addr(mwa),.model_write_data(mwd),
  .model_read(mrq),.model_read_ready(mrr),.model_read_addr(mra),.model_rsp_valid(mrv),.model_rsp_data(mrd),
  .read_valid(arq),.read_ready(arr),.read_addr(ara),.rsp_valid(arv),.rsp_ready(rr),.rsp_data(ard),
  .write_valid(awq),.write_ready(awr),.write_addr(awa),.write_data(awd),.write_mask(awm),
  .flush(flush),.idle(idle));

 reg [3:0] gold[0:32767];                       // nibble scoreboard of the whole 16 KiB
 wire [16:0] model_base={1'b0,16'd16384-model_bytes}<<1;   // nibble address of the image
 integer i,k,ph,cyc;
 function [31:0] gold_word(input [16:0] nib);   // 8 nibbles starting at nib
  integer j; begin for(j=0;j<8;j=j+1)gold_word[j*4+:4]=gold[(nib+j)&32767]; end endfunction

 // Expected model responses, in order (the port has no response backpressure).
 reg [31:0] mexp[0:63],mnew;integer mh=0,mt=0,mcount=0;
 // One activation read in flight; expected value snapshotted at acceptance.
 reg [31:0] aexp;reg apend=0;reg [15:0] aaddr;
 reg rewriting=0;   // image rewrite in progress: reads may stall (host never runs while loading)
 integer overlap=0,side_max=2,wlo=0,sblk=0,sblk_max=0;
 integer mwait=0,await_=0,wwait=0,cov_model=0,cov_act=0,cov_write=0,cov_held=0,cov_both=0,cov_blocked=0;
 reg mdone=0,adone=0,wdone=0;     // a handshake completed at the last rising edge
 always @(posedge clk) if(!rst) begin
  // ---- scoreboard updates on accepted handshakes (pre-edge values) ----
  // Contract: a read accepted on the same edge as a write sees the pre-write
  // data (read-before-write), so snapshot reads before applying writes.
  if(arq && arr)begin aexp=gold_word(ara);aaddr=ara;end
  // Model reads follow the same contract against image writes on the same edge.
  if(mrq && mrr)mnew=mra[1] ? {16'd0,gold_word(model_base+((mra&16'hfffc)<<1))>>16}
                            : gold_word(model_base+((mra&16'hfffc)<<1));
  if(awq && awr)for(k=0;k<8;k=k+1)if(awm[k])gold[(awa+k)&32767]=awd[k*4+:4];
  if(mw && mwr)for(k=0;k<8;k=k+1)gold[(model_base+(mwa<<1)+k)&32767]=mwd[k*4+:4];
  if(mrv)begin
   if(mcount==0)$fatal(1,"STRESS model response without request");
   if(mrd!==mexp[mh])$fatal(1,"STRESS model read got %h expected %h",mrd,mexp[mh]);
   mh=(mh+1)%64;mcount=mcount-1;
  end
  if(arv && rr)begin
   if(!apend)$fatal(1,"STRESS activation response without request");
   if(ard!==aexp)$fatal(1,"STRESS activation read nibble %0d (word %0d+%0d) got %h expected %h t=%0t",aaddr,aaddr>>3,aaddr%8,ard,aexp,$time);
   apend=0;
  end
  if(mrq && mrr)begin
   mexp[mt]=mnew;
   mt=(mt+1)%64;mcount=mcount+1;cov_model=cov_model+1;mdone=1;
  end
  if(arq && arr)begin apend=1;cov_act=cov_act+1;adone=1;end
  if(awq && awr)wdone=1;
  if(arv && !rr)cov_held=cov_held+1;
`ifndef STRESS_NO_SIDE_PORT
  if(dut.i_store.side_fire && dut.i_store.sram_en)cov_both=cov_both+1;
  if(dut.i_store.side_valid && !dut.i_store.side_ready)cov_blocked=cov_blocked+1;
  sblk=dut.i_store.side_valid && !dut.i_store.side_ready ? sblk+1 : 0;
  if(sblk>sblk_max)sblk_max=sblk;
  if(sblk>side_max)$fatal(1,"STRESS side read blocked %0d cycles (bound %0d)",sblk,side_max);
`endif
  if(awq && awr)cov_write=cov_write+1;
  // ---- liveness: no requester waits unboundedly ----
  mwait=mrq && !mrr && !rewriting ? mwait+1 : 0; await_=arq && !arr && !rewriting ? await_+1 : 0;
  wwait=awq && !awr && !rewriting ? wwait+1 : 0;
  if(mwait>MAX_WAIT || await_>MAX_WAIT || wwait>MAX_WAIT)
   $fatal(1,"STRESS starvation model=%0d act=%0d write=%0d (store count=%0d urgent=%0d drain=%0d read_miss=%0d)",
    mwait,await_,wwait,dut.i_store.count,dut.i_store.urgent,dut.i_store.drain,dut.i_store.read_miss);
 end

 // ---- traffic generators: change requests only on negedge, hold until accepted ----
 reg traffic=0;
 integer act_nibbles,hot=16384;     // hot-spot nibble (default: macro seam)
 always @(negedge clk) if(!rst) begin
  act_nibbles=model_base;                      // activation space is below the image
  if(!mrq || mdone) begin mdone=0;
   mrq=traffic && ($urandom%100)<mr_pct;
   mra=($urandom%model_bytes)&16'hfffe;       // halfword aligned, both halves
   if(rewriting && ($urandom%2))mra=mwa+(($urandom%2)<<1);  // read-after-write of the image word just written
  end
  if(!arq || adone) begin adone=0;
   arq=traffic && !apend && ($urandom%100)<ar_pct;
   ara=$urandom%(act_nibbles-8);              // any nibble alignment, may straddle words and banks
   if(($urandom%8)==0)ara=(hot-4+($urandom%8))%(act_nibbles-8); // around the bank seam
  end
  rr=($urandom%100)<rr_pct;
  if(!awq || wdone) begin wdone=0;
   awq=traffic && ($urandom%100)<aw_pct;
   awa=wlo+$urandom%(act_nibbles-8-wlo);
   if(($urandom%8)==0)awa=(hot-4+($urandom%8))%(act_nibbles-8);
   awd=$urandom;awm=$urandom;if(awm==0)awm=8'h01;
  end
 end

 task automatic write_model_image(input integer fraction);   // rewrite 1/fraction of the image
  integer w;
  begin
   for(w=0;w<model_bytes/4;w=w+1)if(($urandom%fraction)==0)begin
    @(negedge clk);mw=1;mwa=w*4;mwd=$urandom;
    @(posedge clk);while(!mwr)@(posedge clk);
    @(negedge clk);mw=0;
   end
  end
 endtask
 task automatic quiesce;
  integer c;
  begin
   @(negedge clk);traffic=0;
   c=0;while(mrq || arq || awq || apend || mcount!=0)begin @(negedge clk);c=c+1;if(c>2000)$fatal(1,"STRESS quiesce timeout");end
   @(negedge clk);flush=1;c=0;
   while(!idle)begin @(negedge clk);c=c+1;if(c>2000)$fatal(1,"STRESS flush timeout");end
   @(negedge clk);flush=0;
  end
 endtask

 initial begin
  if($value$plusargs("N=%d",n_cycles));
  if($value$plusargs("PHASES=%d",phases));
  if($value$plusargs("SEED=%d",seed));
  if($value$plusargs("MODEL_BYTES=%d",mb_arg));
  if($value$plusargs("MR=%d",mr_pct));
  if($value$plusargs("AR=%d",ar_pct));
  if($value$plusargs("AW=%d",aw_pct));
  if($value$plusargs("RR=%d",rr_pct));
  if($value$plusargs("HOT=%d",hot));
  if($value$plusargs("WLO=%d",wlo));
  if($value$plusargs("OVERLAP=%d",overlap));
  if($value$plusargs("SIDE_MAX=%d",side_max));
  i=$urandom(seed);model_bytes=mb_arg;
  for(i=0;i<32768;i=i+1)gold[i]=4'h0;
  repeat(4)@(negedge clk);rst=0;repeat(4)@(negedge clk);
  // Initialise activation space through the write port, then the model image.
  for(i=0;i<model_base;i=i+8)begin
   @(negedge clk);awq=1;awa=i;awd=$urandom;awm=8'hff;
   @(posedge clk);while(!awr)@(posedge clk);
   @(negedge clk);awq=0;
  end
  write_model_image(1);
  quiesce();
  for(ph=0;ph<phases;ph=ph+1)begin
   @(negedge clk);traffic=1;
   for(cyc=0;cyc<n_cycles;cyc=cyc+1)@(negedge clk);
   if(overlap)begin                           // rewrite under live model/activation traffic
    @(negedge clk);traffic=1;rewriting=1;write_model_image(16);
    repeat(64)@(negedge clk);rewriting=0;quiesce();
   end else begin
    quiesce();
    write_model_image(16);                     // image rewrite between runs (dirty path)
    quiesce();
   end
  end
  $display("COVER stress model_reads=%0d act_reads=%0d act_writes=%0d held_rsp=%0d both_macros=%0d side_blocked=%0d side_block_max=%0d",
   cov_model,cov_act,cov_write,cov_held,cov_both,cov_blocked,sblk_max);
  $display("PASS NPU_SHARED_STRESS model_bytes=%0d phases=%0d cycles=%0d",model_bytes,phases,n_cycles);
  $finish;
 end
 initial begin #1e9;$fatal(1,"STRESS global timeout");end
endmodule
