`timescale 1ns/1ps
module tb_npu_tensor_lookup;
 parameter MAX_OPS=128;
 reg clk=0,rst=1,seed=0,start=0,save=0;
 always #5 clk=~clk;
 reg [7:0] pc=0;
 reg input_i32=0,src_i32=0,dst_i32=0,add_op=0;
 reg [15:0] input_first=0,input_end=512,src=0,src_end=16,src2=0,src2_end=16,dst=0,dst_end=16;
 wire done,known,known2,ref_done,ref_known,ref_known2;
 npu_tensor_lookup #(.MAX_OPS(MAX_OPS)) dut(
  .clk(clk),.rst(rst),.seed(seed),.start(start),.save(save),.pc(pc),
  .input_i32(input_i32),.src_i32(src_i32),.dst_i32(dst_i32),.add_op(add_op),
  .input_first(input_first),.input_end(input_end),.src(src),.src_end(src_end),
  .src2(src2),.src2_end(src2_end),.dst(dst),.dst_end(dst_end),
  .done(done),.source_known(known),.source2_known(known2));
 npu_tensor_lookup_reference #(.MAX_OPS(MAX_OPS)) reference(
  .clk(clk),.rst(rst),.seed(seed),.start(start),.save(save),.pc(pc),
  .input_i32(input_i32),.src_i32(src_i32),.dst_i32(dst_i32),.add_op(add_op),
  .input_first(input_first),.input_end(input_end),.src(src),.src_end(src_end),
  .src2(src2),.src2_end(src2_end),.dst(dst),.dst_end(dst_end),
  .done(ref_done),.source_known(ref_known),.source2_known(ref_known2));
 integer epoch,n,waits,cycles=0,rng_seed=20261110,unused;
 always @(posedge clk) begin
  #1;
  if(!rst && {done,known,known2}!=={ref_done,ref_known,ref_known2})
   $fatal(1,"lookup cycle mismatch max=%0d pc=%0d epoch=%0d cycle=%0d got=%b expected=%b",
          MAX_OPS,pc,epoch,cycles,{done,known,known2},{ref_done,ref_known,ref_known2});
  cycles=cycles+1;
 end
 initial begin
  unused=$urandom(rng_seed);
  repeat(3)@(negedge clk);rst=0;
  for(epoch=0;epoch<16;epoch=epoch+1)begin
   @(negedge clk);seed=1;input_i32=epoch[0];input_first=(epoch%4)*128;input_end=input_first+512;
   @(negedge clk);seed=0;
   for(n=0;n<MAX_OPS;n=n+1)begin
    pc=n;src_i32=$urandom%2;dst_i32=$urandom%2;add_op=$urandom%2;
    // Alternate known input ranges, recently saved ranges and unrelated high addresses.
    case(n%3)
     0: begin src=input_first;src_end=input_end;end
     1: begin src=dst;src_end=dst_end;end
     2: begin src=16'hf000+($urandom%128)*8;src_end=src+8;end
    endcase
    src2=input_first+8;src2_end=input_end-8;
    dst=($urandom%128)*8;dst_end=dst+1+($urandom%128);
    start=1;@(negedge clk);start=0;waits=0;
    while(!done)begin @(negedge clk);waits=waits+1;if(waits>2*(MAX_OPS+1)+4)$fatal(1,"lookup timeout");end
    save=1;@(negedge clk);save=0;repeat(2)@(negedge clk);
   end
   // Abort an in-progress search, then reseed and replay the next epoch.
   if(epoch[0])begin
    pc=MAX_OPS-1;start=1;@(negedge clk);start=0;repeat(3)@(negedge clk);
   end
   @(negedge clk);rst=1;@(negedge clk);rst=0;
  end
  $display("PASS TENSOR_LOOKUP_EQUIVALENCE max_ops=%0d descriptors=%0d cycles=%0d",MAX_OPS,16*MAX_OPS,cycles);
  $finish;
 end
 initial begin #100000000;$fatal(1,"lookup equivalence global timeout");end
endmodule
