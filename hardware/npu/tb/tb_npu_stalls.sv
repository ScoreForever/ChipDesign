`timescale 1ns/1ps
// Independent variable-latency memory harness: no production RAM timing assumptions.
module tb_npu_stalls;
 reg clk=0,rst=1,validate=0,run=0;always #5 clk=~clk;
 wire ready,busy,vd,vok,done;wire [4:0] error,error_op;
 wire mv,av,wv;wire [15:0] ma,aa,wa;wire [31:0] wd;wire [3:0] wm;
 reg mr=0,ar=0,wr=0,mrv=0,arv=0;reg [31:0] md,ad;
 integer idle_delay=0,wr_clock=0,write_stalls=0,max_write_streak=0,write_streak=0;
 wire memory_flush;wire memory_idle=idle_delay==0;
 reg [31:0] model[0:2047],cfg[0:6];reg [7:0] act[0:8191],input_data[0:8191],gold[0:8191];
 npu_program_core dut(.clk(clk),.rst(rst),.validate(validate),.run(run),.ready(ready),.busy(busy),
 .model_bytes(cfg[0][15:0]),.op_count(cfg[1][4:0]),.input_i32(cfg[6][0]),.input_base(cfg[2][15:0]),.input_bytes(cfg[3][15:0]),
 .output_base(cfg[4][15:0]),.output_bytes(cfg[5][15:0]),.validation_done(vd),.validation_ok(vok),.done(done),
 .error(error),.error_op(error_op),.cycles(),.issues(),.model_reads(),.act_reads(),.act_writes(),
 .m_valid(mv),.m_ready(mr),.m_addr(ma),.m_rsp_valid(mrv),.m_rsp_ready(),.m_data(md),
 .a_valid(av),.a_ready(ar),.a_addr(aa),.a_rsp_valid(arv),.a_rsp_ready(),.a_data(ad),
 .w_valid(wv),.w_ready(wr),.w_addr(wa),.w_data(wd),.w_mask(wm),
 .memory_idle(memory_idle),.memory_flush(memory_flush));
 reg [31:0] lfsr=32'h120319;
 integer mt=-1,at=-1,i,reads=0,writes=0;reg [15:0] saved_m,saved_a;
 reg executing=0;
 // Independent pin-level scoreboard: count memory events at their actual
 // handshakes, without using the monitor's sampled event registers.
 integer ticks=0,mreq=0,mrsp=0,areq=0,arsp=0,mstall=0,astall=0;
 integer ms=0,as_=0,mmax=0,amax=0,mwait=0,await_=0,observed_writes=0;
 reg mp=0,ap=0;
 always @(posedge clk) if(executing && busy)begin
  ticks=ticks+1;
  if(mv && mr)mreq=mreq+1;
  if(mrv)mrsp=mrsp+1;
  if(av && ar)areq=areq+1;
  if(arv)arsp=arsp+1;
  if(mp && !mrv)mwait=mwait+1;
  if(ap && !arv)await_=await_+1;
  if(mv && mr)mp=1;else if(mrv)mp=0;
  if(av && ar)ap=1;else if(arv)ap=0;
  if(mv && !mr)begin mstall=mstall+1;ms=ms+1;if(ms>mmax)mmax=ms;end else ms=0;
  if(av && !ar)begin astall=astall+1;as_=as_+1;if(as_>amax)amax=as_;end else as_=0;
  if(wv && wr)observed_writes=observed_writes+1;
 end
 reg previously_stalled=0;reg [15:0] held_addr;reg [31:0] held_data;reg [3:0] held_mask;
 integer max_write_fifo=0,max_reserved_outputs=0;
 always @(posedge clk)begin
  if(rst)begin previously_stalled<=0;idle_delay<=0;end
  else begin
   if(dut.write_count>8 || dut.write_count+dut.rq_pending>8)$fatal(1,"write FIFO/quant reservation overflow");
   if(dut.write_count>max_write_fifo)max_write_fifo=dut.write_count;
   if(dut.write_count+dut.rq_pending>max_reserved_outputs)max_reserved_outputs=dut.write_count+dut.rq_pending;
   if(previously_stalled && (!wv || wa!==held_addr || wd!==held_data || wm!==held_mask))
    $fatal(1,"write payload changed under backpressure");
   previously_stalled<=wv && !wr;held_addr<=wa;held_data<=wd;held_mask<=wm;
   if(wv && wr)idle_delay<=2+lfsr[13:11];else if(idle_delay>0)idle_delay<=idle_delay-1;
   if(wv && !wr)begin
    write_stalls=write_stalls+1;write_streak=write_streak+1;
    if(write_streak>max_write_streak)max_write_streak=write_streak;
   end else write_streak=0;
   if(done && !memory_idle)$fatal(1,"done published before physical writes drained");
  end
 end
 always @(negedge clk)begin
  lfsr={lfsr[30:0],lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};
  mr=!rst && mt<0 && !mrv && lfsr[0];ar=!rst && at<0 && !arv && lfsr[3];
  wr_clock=wr_clock+1;
  // Long deterministic stalls fill the write FIFO behind the II=1 Requant
  // pipeline; random gaps cover transitions into and out of those stalls.
  wr=!rst && wr_clock%97>=31 && lfsr[9];
 end
 always @(posedge clk)begin
  mrv<=0;arv<=0;
  if(mv && mr)begin if(ma[1:0]!=0 || ma>=cfg[0])$fatal(1,"model address");saved_m<=ma;mt<=1+lfsr[6:4];end
  else if(mt>0)mt<=mt-1;
  else if(mt==0)begin md<=model[saved_m>>2];mrv<=1;mt<=-1;end
  if(av && ar)begin if(aa>=8192)$fatal(1,"activation address");saved_a<=aa;at<=1+lfsr[10:8];reads<=reads+1;end
  else if(at>0)at<=at-1;
  else if(at==0)begin
   for(i=0;i<4;i=i+1)ad[i*8+:8]<=act[(saved_a+i)&8191];arv<=1;at<=-1;
  end
  if(wv && wr)begin
   if(!executing || wa>=8192)$fatal(1,"write outside run/bounds");
   for(i=0;i<4;i=i+1)if(wm[i])act[wa+i]<=wd[i*8+:8];writes<=writes+1;
  end
 end
 string dir;
 initial begin
  if(!$value$plusargs("DATA=%s",dir))$fatal(1,"DATA required");
  $readmemh({dir,"/config.hex"},cfg);$readmemh({dir,"/model.hex"},model,0,cfg[0]/4-1);
  $readmemh({dir,"/input.hex"},input_data,0,cfg[3]-1);$readmemh({dir,"/gold.hex"},gold,0,cfg[5]-1);
  for(i=0;i<cfg[3];i=i+1)act[cfg[2]+i]=input_data[i];
  repeat(4)@(negedge clk);rst=0;validate=1;@(negedge clk);validate=0;
  wait(vd);if(!vok || error)$fatal(1,"stalled validation error %0d op=%0d",error,error_op);
  @(negedge clk);executing=1;run=1;@(negedge clk);run=0;
  wait(done);repeat(2)@(negedge clk);if(error)$fatal(1,"stalled execution error");
  if(dut.perf_counters[0*32+:32]!==ticks || dut.perf_counters[1*32+:32]!==mreq ||
     dut.perf_counters[2*32+:32]!==mstall || dut.perf_counters[3*32+:32]!==mmax ||
     dut.perf_counters[4*32+:32]!==mrsp || dut.perf_counters[5*32+:32]!==mwait ||
     dut.perf_counters[6*32+:32]!==areq || dut.perf_counters[7*32+:32]!==astall ||
     dut.perf_counters[8*32+:32]!==amax || dut.perf_counters[9*32+:32]!==arsp ||
     dut.perf_counters[10*32+:32]!==await_ || dut.perf_counters[11*32+:32]!==observed_writes ||
     dut.perf_counters[12*32+:32]!==dut.issues)$fatal(1,"performance pin scoreboard mismatch");
  if(mstall==0 || astall==0 || mwait==0 || await_==0 || write_stalls==0 || max_write_streak<8)
   $fatal(1,"stall stimulus did not cover all interfaces and long output backpressure");
  if(dut.perf_counters[17*32+:32]>4 || dut.perf_counters[18*32+:32]>2 ||
     dut.perf_counters[19*32+:32]>32)$fatal(1,"performance occupancy bounds");
  for(i=0;i<cfg[5];i=i+1)if(act[cfg[4]+i]!==gold[i])$fatal(1,"stalled result %0d",i);
  $display("PASS random read/write stalls, stable payload, drain and performance scoreboard reads=%0d writes=%0d mstall=%0d astall=%0d wstall=%0d wmax=%0d fifo=%0d reserved=%0d",reads,writes,mstall,astall,write_stalls,max_write_streak,max_write_fifo,max_reserved_outputs);
  executing=0;rst=1;repeat(2)@(negedge clk);
  if(dut.perf_counters!==0)$fatal(1,"performance reset failed");
  $finish;
 end
 initial begin #100000000;$fatal(1,"stalls test timeout");end
endmodule
