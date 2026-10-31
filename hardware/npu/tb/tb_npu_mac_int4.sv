`timescale 1ns/1ps
// Exhaust every signed INT4 activation/weight pair, both weight banks and
// all output columns. In particular, 4*(-8)*(-8)=256 requires 10 signed bits.
module tb_npu_mac_int4;
`ifdef COLS4
 localparam COLS=4;
`else
 localparam COLS=8;
`endif
 reg clk=0,rst=1,we=0,wb=0,iv=0,ib=0;always #5 clk=~clk;
 reg [1:0] row;reg [COLS*4-1:0] weights;reg [15:0] acts,tag;
 wire ov,ob;wire [15:0] ot;wire [COLS*10-1:0] sums;
 npu_mac_array #(.ARRAY_COLS(COLS),.ACC_WIDTH(10),.TAG_WIDTH(16)) dut(
 .clk(clk),.rst(rst),.weight_we(we),.weight_bank(wb),.weight_row(row),.weight_data(weights),
 .in_valid(iv),.in_bank(ib),.in_act(acts),.in_tag(tag),
 .out_valid(ov),.out_bank(ob),.out_tag(ot),.out_psum(sums));
 integer a,w,w1,r,c,lane,issued=0,checked=0;integer expected[0:511];reg banks[0:511];
 always @(posedge clk)if(ov)begin
  if(ot!==checked[15:0] || ob!==banks[checked])$fatal(1,"INT4 tag/bank alignment");
  for(lane=0;lane<COLS;lane=lane+1)
   if($signed(sums[lane*10+:10])!==expected[checked])
    $fatal(1,"INT4 MAC pair %0d lane %0d got=%0d expected=%0d",checked,lane,$signed(sums[lane*10+:10]),expected[checked]);
  checked=checked+1;
 end
 initial begin
  repeat(3)@(negedge clk);rst=0;
  for(a=-8;a<=7;a=a+1)for(w=-8;w<=7;w=w+1)begin
   w1=w==7 ? -8 : w+1;
   for(r=0;r<4;r=r+1)begin
    @(negedge clk);we=1;row=r;wb=0;
    for(c=0;c<COLS;c=c+1)weights[c*4+:4]=w[3:0];
   end
   for(r=0;r<4;r=r+1)begin
    @(negedge clk);row=r;wb=1;
    for(c=0;c<COLS;c=c+1)weights[c*4+:4]=w1[3:0];
   end
   @(negedge clk);we=0;iv=1;ib=0;tag=issued;acts={4{a[3:0]}};
   expected[issued]=4*a*w;banks[issued]=0;issued=issued+1;
   @(negedge clk);ib=1;tag=issued;
   expected[issued]=4*a*w1;banks[issued]=1;issued=issued+1;
   @(negedge clk);iv=0;
   repeat(4+COLS+1)@(negedge clk);
  end
  if(checked!=512)$fatal(1,"missing INT4 MAC results %0d",checked);
  $display("PASS INT4 MAC exhaustive pairs and bank tags cols=%0d outputs=%0d",COLS,checked);$finish;
 end
 initial begin #2000000;$fatal(1,"INT4 MAC timeout");end
endmodule
