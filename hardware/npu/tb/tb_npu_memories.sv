`timescale 1ns/1ps
// ABI 3 packed nibble memory contract, independent 16K-nibble scoreboard.
module tb_npu_memories;
 reg clk=0,rst=1;always #5 clk=~clk;
 reg mw=0,mv=0,mrr=0;reg [15:0] ma=0,mwa=0;reg [31:0] mwd=0;
 wire mwr,mready,mvalid;wire [31:0] md;
 npu_model_store model(.clk(clk),.rst(rst),.write_valid(mw),.write_ready(mwr),
 .write_addr(mwa),.write_data(mwd),.read_request(mv),.read_ready(mready),
 .read_addr(ma),.read_valid(mvalid),.read_response_ready(mrr),.read_data(md));
 reg rv=0,rr=0,wv=0,flush=0;reg [15:0] ra=0,wa=0;reg [31:0] wd=0;reg [7:0] wm=0;
 wire ar,valid,wr,idle;wire [31:0] data;
 npu_activation_store dut(.clk(clk),.rst(rst),.read_valid(rv),.read_ready(ar),.read_addr(ra),
 .rsp_valid(valid),.rsp_ready(rr),.rsp_data(data),.write_valid(wv),.write_ready(wr),
 .write_addr(wa),.write_data(wd),.write_mask(wm),.flush(flush),.idle(idle));
 reg [3:0] gold[0:16383];integer i,j,writes=0,rmw=0;
 reg [31:0] held,want,random_state=32'habcd1234;
 always @(posedge clk)if(!rst)begin
  if(wv && wr)for(j=0;j<8;j=j+1)if(wm[j])gold[(wa+j)&16383]=wd[j*4+:4];
  if(dut.sram_we)writes=writes+1;
  if(dut.start_rmw)rmw=rmw+1;
 end
 task store(input [15:0] address,input [31:0] value,input [7:0] mask);
 begin
  @(negedge clk);wv=1;wa=address;wd=value;wm=mask;
  @(posedge clk);while(!wr)@(posedge clk);
  @(negedge clk);wv=0;
 end endtask
 task drain;
 integer cycles;
 begin
  @(negedge clk);flush=1;cycles=0;
  while(!idle)begin @(negedge clk);cycles=cycles+1;if(cycles>2000)$fatal(1,"flush timeout");end
  @(negedge clk);flush=0;
 end endtask
 task load(input [15:0] address,input integer hold_cycles);
 integer k;
 begin
  @(negedge clk);rv=1;ra=address;rr=0;
  @(posedge clk);while(!ar)@(posedge clk);
  for(k=0;k<8;k=k+1)want[k*4+:4]=gold[(address+k)&16383];
  @(negedge clk);rv=0;while(!valid)@(negedge clk);
  if(data!==want)$fatal(1,"read %h got %h expected %h",address,data,want);
  held=data;
  repeat(hold_cycles)begin @(negedge clk);if(!valid || data!==held || ar || idle)$fatal(1,"held response");end
  rr=1;@(negedge clk);rr=0;
 end endtask
 task model_load(input [15:0] address,input [31:0] expected);
 begin
  @(negedge clk);mv=1;ma=address;mrr=0;
  @(posedge clk);while(!mready)@(posedge clk);
  @(negedge clk);mv=0;while(!mvalid)@(negedge clk);
  repeat(4)begin @(negedge clk);if(md!==expected || !mvalid || mready || mwr)$fatal(1,"model halfword/hold");end
  mrr=1;@(negedge clk);mrr=0;
 end endtask
 integer before_writes;
 initial begin
  repeat(4)@(negedge clk);rst=0;
  @(negedge clk);mw=1;mwa=0;mwd=32'hfedcba98;
  @(posedge clk);while(!mwr)@(posedge clk);
  @(negedge clk);mw=0;
  model_load(0,32'hfedcba98);model_load(2,32'h0000fedc);
  for(i=0;i<16384;i=i+8)store(i,32'h87654321^i,255);
  drain;
  // Isolated low/high nibble updates must preserve their byte neighbour.
  store(64,32'h12345678,255);drain;
  store(65,32'h0000000a,1);drain;load(64,3);
  store(64,32'h0000000b,1);drain;load(64,2);
  // One-word forwarding, cache aliases, held data and same-edge ordering.
  load(64,0);
  @(negedge clk);rv=1;ra=64;rr=0;wv=1;wa=64;wd=32'h01234567;wm=255;
  for(j=0;j<8;j=j+1)want[j*4+:4]=gold[64+j];
  @(posedge clk);if(!ar || !wr)$fatal(1,"simultaneous cache-hit read/write");
  @(negedge clk);rv=0;wv=0;
  if(!valid || data!==want)$fatal(1,"read-before-write snapshot");
  held=data;store(65,32'h0000000c,1);
  repeat(3)begin @(negedge clk);if(data!==held || !valid)$fatal(1,"write mutated held data");end
  rr=1;@(negedge clk);rr=0;drain;load(64,1);
  store(192,32'h76543210,255);drain;load(192,1);load(64,1);
  // Cross-word arbitrary nibble masks, including capacity wrap.
  store(16383,32'hfedcba98,255);load(16380,3);drain;load(16383,2);
  for(i=0;i<500;i=i+1)begin
   random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
   store(random_state[13:0],random_state^32'h56342187,random_state[23:16]);
   if(i%3==0)drain;
   load(random_state[13:0],i%4);
  end
  drain;
  // Full-capacity scan verifies all adjacent nibbles survived every RMW.
  for(i=0;i<16384;i=i+8)load(i,0);
  // Reset cancels an uncommitted partial word while preserving SRAM payload.
  store(1024,32'hcafebabe,255);drain;load(1024,0);
  before_writes=writes;store(1024,32'h00000001,1);
  if(writes!=before_writes || idle)$fatal(1,"reset stimulus not queued");
  rst=1;@(negedge clk);rst=0;gold[1024]=4'he;load(1024,2);
  if(rmw==0)$fatal(1,"no physical nibble RMW observed");
  $display("PASS packed INT4 memories, byte-neighbour RMW, halfword model reads, held snapshots, masks/wrap/full scan/reset rmw=%0d",rmw);
  $finish;
 end
 initial begin #5000000;$fatal(1,"packed memory timeout");end
endmodule
