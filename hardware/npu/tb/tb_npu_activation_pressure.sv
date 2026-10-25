`timescale 1ns/1ps
// Deterministic concurrent request/response pressure, independent byte-array
// scoreboard and read-before-write snapshots. Includes arbitrary masks and
// cross-word writes, an actually full 8-entry queue, held responses, flush,
// and a final scan of all 8 KiB. Run with behavioral RAM and the course macro.
module tb_npu_activation_pressure;
reg clk=0,rst=1;always #5 clk=~clk;
reg rv=0,rr=0,wv=0,flush=0;reg [15:0] ra=0,wa=0;reg [31:0] wd=0;reg [3:0] wm=0;
wire rready,rspv,wready,idle;wire [31:0] data;
npu_activation_store dut(.clk(clk),.rst(rst),.read_valid(rv),.read_ready(rready),.read_addr(ra),
.rsp_valid(rspv),.rsp_ready(rr),.rsp_data(data),.write_valid(wv),.write_ready(wready),
.write_addr(wa),.write_data(wd),.write_mask(wm),.flush(flush),.idle(idle));
reg [7:0] gold[0:8191];reg [31:0] expected;reg ep=0,wl=0,rl=0;
integer i,j,reads=0,writes=0,ws=0,rs=0,peak=0;
always @(posedge clk)begin
 wl=wv&&wready&&!rst;rl=rv&&rready&&!rst;
 if(!rst)begin
  if(dut.count>8)$fatal(1,"queue overflow");
  if(dut.sram_we && (!dut.sram_en || dut.physical_read || dut.state==2))
   $fatal(1,"physical SRAM port used for read and write together");
  if(dut.count>peak)peak=dut.count;
  if(rspv)begin
   if(!ep || data!==expected)$fatal(1,"response mismatch reads=%0d got=%h expected=%h",reads,data,expected);
   if(rr)ep=0;
  end
  if(rl)begin
   if(ep)$fatal(1,"more than one response");
   for(j=0;j<4;j=j+1)expected[j*8+:8]=gold[(ra+j)&8191];
   ep=1;reads=reads+1;
  end
  if(wl)begin
   for(j=0;j<4;j=j+1)if(wm[j])gold[(wa+j)&8191]=wd[j*8+:8];
   writes=writes+1;
  end
  if(wv&&!wready)ws=ws+1;
  if(rv&&!rready)rs=rs+1;
 end
end
task write_word(input integer address);
begin
 @(negedge clk);wv=1;wa=address;wd=32'h01020304^address;wm=15;
 @(posedge clk);while(!wready)@(posedge clk);
 @(negedge clk);wv=0;
end endtask
task read_word(input integer address);
begin
 @(negedge clk);rv=1;ra=address;rr=1;
 @(posedge clk);while(!rready)@(posedge clk);
 @(negedge clk);rv=0;
 while(ep)@(negedge clk);
end endtask
reg [31:0] random_state=32'h89abcdef;
initial begin
 for(i=0;i<8192;i=i+1)gold[i]=0;
 repeat(4)@(negedge clk);rst=0;
 for(i=0;i<8192;i=i+4)write_word(i);
 @(negedge clk);flush=1;while(!idle)@(negedge clk);flush=0;
 for(i=0;i<20000;i=i+1)begin
  @(negedge clk);
  random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
  if(!wv || wl)begin
   wv=random_state[1] || random_state[6];
   wa=(random_state[8] ? {3'd0,random_state[12:0]} : {9'd0,random_state[6:0]});
   wd=random_state^32'h12345678;wm=random_state[19:16];
  end
  if(!rv || rl)begin
   rv=random_state[2] || random_state[9];
   ra=(random_state[7] ? {3'd0,random_state[25:13]} : {9'd0,random_state[6:0]});
  end
  rr=random_state[4] || random_state[5];flush=&random_state[15:13];
 end
 @(negedge clk);rr=1;flush=1;
 while(wv || rv)begin
  @(posedge clk);@(negedge clk);if(wl)wv=0;if(rl)rv=0;
 end
 while(!idle)@(negedge clk);
 flush=0;
 for(i=0;i<8192;i=i+4)read_word(i);
 @(negedge clk);flush=1;while(!idle)@(negedge clk);
 if(ws==0 || rs==0 || peak!=8 || writes<15000 || reads<2500)$fatal(1,"pressure coverage not reached");
 $display("PASS high pressure random SRAM reads=%0d writes=%0d write_stalls=%0d read_stalls=%0d peak=%0d",reads,writes,ws,rs,peak);
 $finish;
end
initial begin #20000000;$fatal(1,"timeout");end
endmodule
