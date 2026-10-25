`timescale 1ns/1ps
module tb_npu_memories;
 reg clk=0,rst=1;always #5 clk=~clk;
 reg wv=0,rv=0,rr=0;reg [15:0] wa=0,ra=0;reg [31:0] wd=0;
 wire wr,rready,rvalid;wire [31:0] rd;
 npu_model_store dut(.clk(clk),.rst(rst),.write_valid(wv),.write_ready(wr),.write_addr(wa),.write_data(wd),
 .read_request(rv),.read_ready(rready),.read_addr(ra),.read_valid(rvalid),.read_response_ready(rr),.read_data(rd));
 reg av=0,arr=0,aw=0,flush=0;reg [15:0] aa=0,awa=0;reg [31:0] awd=0;reg [3:0] mask=0;
 wire ar,arv,awr,idle;wire [31:0] ard;
 npu_activation_store #(.BYTES(8192)) act(.clk(clk),.rst(rst),.read_valid(av),.read_ready(ar),.read_addr(aa),
 .rsp_valid(arv),.rsp_ready(arr),.rsp_data(ard),.write_valid(aw),.write_ready(awr),
 .write_addr(awa),.write_data(awd),.write_mask(mask),.flush(flush),.idle(idle));
 reg [7:0] gold[0:8191];integer i,j,physical_writes=0,before_writes,accepted_writes=0;
 reg [31:0] saved,expected,random_state=32'h56c319a7;
 reg [15:0] random_address;reg [3:0] random_mask;
 always @(posedge clk)if(!rst)begin
  if(aw && awr)begin
   for(j=0;j<4;j=j+1)if(mask[j])gold[(awa+j)&8191]=awd[j*8+:8];
   accepted_writes=accepted_writes+1;
  end
  if(act.sram_we)physical_writes=physical_writes+1;
 end
 task model_write(input [15:0] address,input [31:0] value);
 begin
  @(negedge clk);wv=1;wa=address;wd=value;
  @(posedge clk);while(!wr)@(posedge clk);
  @(negedge clk);wv=0;
 end endtask
 task model_read(input [15:0] address,input [31:0] want);
 begin
  @(negedge clk);rv=1;ra=address;rr=0;
  @(posedge clk);while(!rready)@(posedge clk);
  @(negedge clk);rv=0;while(!rvalid)@(negedge clk);
  if(rd!==want)$fatal(1,"model read %h got %h expected %h",address,rd,want);
  saved=rd;
  repeat(5)begin @(negedge clk);if(!rvalid || rd!==saved || wr || rready)$fatal(1,"model response backpressure");end
  rr=1;@(negedge clk);rr=0;
 end endtask
 task write_act(input [15:0] address,input [31:0] value,input [3:0] enables);
 begin
  @(negedge clk);aw=1;awa=address;awd=value;mask=enables;
  @(posedge clk);while(!awr)@(posedge clk);
  @(negedge clk);aw=0;
 end endtask
 task drain;
 integer n;
 begin
  @(negedge clk);flush=1;n=0;
  while(!idle)begin @(negedge clk);n=n+1;if(n>2000)$fatal(1,"activation drain timeout");end
  @(negedge clk);flush=0;
 end endtask
 task read_act(input [15:0] address,input integer hold_cycles);
 reg [31:0] want,held;integer k;
 begin
  @(negedge clk);av=1;aa=address;arr=0;
  @(posedge clk);while(!ar)@(posedge clk);
  for(k=0;k<4;k=k+1)want[k*8+:8]=gold[(address+k)&8191];
  @(negedge clk);av=0;while(!arv)@(negedge clk);
  if(ard!==want)$fatal(1,"activation read %h got %h expected %h",address,ard,want);
  held=ard;
  repeat(hold_cycles)begin
   @(negedge clk);
   if(!arv || ard!==held || ar || idle)$fatal(1,"activation response not held under backpressure");
  end
  arr=1;@(negedge clk);arr=0;
 end endtask
 initial begin
  repeat(3)@(negedge clk);rst=0;
  for(i=0;i<8;i=i+1)model_write(i*4,32'h12340000+i);
  model_read(12,32'h12340003);
  // Reset a held model response and prove the SRAM payload is preserved.
  @(negedge clk);rv=1;ra=4;rr=0;
  @(posedge clk);while(!rready)@(posedge clk);
  @(negedge clk);rv=0;while(!rvalid)@(negedge clk);
  rst=1;@(negedge clk);if(rvalid)$fatal(1,"pending model response survived reset");rst=0;
  model_read(4,32'h12340001);

  // Thirty-two byte stores must coalesce, including a held partial word.
  before_writes=physical_writes;
  for(i=0;i<32;i=i+1)write_act(i,i+10,1);
  drain;
  if(physical_writes-before_writes>16)$fatal(1,"byte write coalescing did not reduce physical writes");
  for(i=0;i<28;i=i+1)read_act(i,i%5);
  for(i=0;i<32;i=i+4)write_act(4096+i,32'hf0e0d000+i,15);
  write_act(4092,32'h80706050,15);
  write_act(8188,32'hf1e2d3c4,15);
  drain;
  for(i=4092;i<4120;i=i+1)read_act(i,i%4);
  read_act(8188,4);read_act(8191,3); // end-of-space wrap for unused read bytes

  // Read the newest byte while its word is still buffered, then fill the
  // remaining lanes. Alias the 64B cache index and return to the original tag.
  write_act(16'h1200,32'h12345678,15);drain;read_act(16'h1200,2);
  write_act(16'h1200,32'h000000a5,1);read_act(16'h1200,1);
  write_act(16'h1201,32'h000000b6,1);write_act(16'h1202,32'h000000c7,1);
  write_act(16'h1203,32'h000000d8,1);
  write_act(16'h1240,32'h76543210,15);drain;
  read_act(16'h1240,2);read_act(16'h1200,2);
  write_act(16'h1200,32'h00ee00dd,5);read_act(16'h1200,4);drain;

  // Both ports may accept on a cache hit. The read is a snapshot before the
  // current write, while a later read must see that accepted write.
  write_act(16'h1500,32'habcdef12,15);drain;read_act(16'h1500,1);
  @(negedge clk);aw=1;awa=16'h1500;awd=32'h456789ab;mask=15;
  av=1;aa=16'h1500;arr=0;
  @(posedge clk);if(!awr || !ar)$fatal(1,"cache-hit simultaneous read/write not accepted");
  @(negedge clk);aw=0;av=0;while(!arv)@(negedge clk);
  if(ard!==32'habcdef12)$fatal(1,"simultaneous read/write did not use read-before-write ordering");
  saved=ard;
  // Even writes to that same word cannot mutate an already held response.
  write_act(16'h1500,32'h01020304,15);
  repeat(4)begin @(negedge clk);if(!arv || ard!==saved)$fatal(1,"concurrent write changed held response");end
  arr=1;@(negedge clk);arr=0;read_act(16'h1500,1);drain;

  // General masks and crossing writes use an atomic queue reservation.
  for(i=32;i<128;i=i+4)write_act(i,32'h98fedcba^i,15);
  for(i=32;i<128;i=i+4)write_act(4096+i,32'h1234abcd^i,15);
  drain;
  write_act(4095,32'h87654321,15);read_act(4094,2);read_act(4095,2);
  write_act(8191,32'hdeadbeef,15);read_act(8190,2);read_act(8191,2);
  for(i=0;i<180;i=i+1)begin
   random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
   random_address=(random_state[9] ? 4096 : 0)+(random_state[6:0]&7'h7b);
   random_mask=random_state[13:10];
   write_act(random_address,random_state^32'hc5a6f709,random_mask);
   read_act(random_address,i%4);
   if(i%11==0)drain;
  end
  drain;

  // Exercise complete 32-bit words and byte masks near the upper capacity.
  for(i=0;i<80;i=i+1)write_act(6144+4*i,32'h81fef001^(32'h01010101*i),15);
  drain;
  for(i=0;i<76;i=i+1)read_act(6144+4*i+(i%4),i%3);

  // Invalidate an in-flight cache response without destroying committed SRAM.
  av=1;aa=16'h1200;arr=0;
  @(posedge clk);while(!ar)@(posedge clk);
  @(negedge clk);av=0;rst=1;
  @(negedge clk);if(arv)$fatal(1,"activation response survived reset");rst=0;
  read_act(16'h1200,3);

  // A reset discards a not-yet-committed partial word; it is not a rollback
  // of physical writes which have already reached the SRAM macro.
  write_act(16'h1a00,32'hcafebabe,15);drain;read_act(16'h1a00,1);
  before_writes=physical_writes;
  write_act(16'h1a00,32'h00000011,1);
  if(idle || physical_writes!=before_writes)$fatal(1,"partial-word cancellation stimulus not pending");
  rst=1;@(negedge clk);rst=0;
  gold[16'h1a00]=8'hbe;read_act(16'h1a00,3);drain;
  if(!idle)$fatal(1,"activation idle did not settle");
  $display("PASS dual SRAM memories: held responses, coalescing/forwarding, alias, upper 8KiB, unaligned/wrap, flush and reset accepted=%0d physical=%0d",accepted_writes,physical_writes);
  $finish;
 end
 initial begin #2000000;$fatal(1,"memory test timeout");end
endmodule
