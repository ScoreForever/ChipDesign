`timescale 1ns/1ps
// Exhaust every signed INT4 and unsigned u4 activation against every INT4
// weight, both weight banks and all output columns. 8*(-8)*(-8)=512 and
// 8*15*(-8)=-960 both require 11 signed bits.
module tb_npu_mac_int4;
`ifdef COLS4
 localparam COLS=4;
`else
 localparam COLS=8;
`endif
 reg clk=0,rst=1,we=0,wb=0,iv=0,ib=0,un=0;always #5 clk=~clk;
 reg [2:0] row;reg [COLS*4-1:0] weights;reg [31:0] acts;reg [15:0] tag;
 wire ov,ob;wire [15:0] ot;wire [COLS*11-1:0] sums;
 npu_mac_array #(.ARRAY_ROWS(8),.ARRAY_COLS(COLS),.ACC_WIDTH(11),.TAG_WIDTH(16)) dut(
 .clk(clk),.rst(rst),.weight_we(we),.weight_bank(wb),.weight_row(row),.weight_data(weights),
 .in_valid(iv),.in_bank(ib),.in_act(acts),.in_unsigned(un),.in_tag(tag),
 .out_valid(ov),.out_bank(ob),.out_tag(ot),.out_psum(sums));
 integer a,w,w1,r,c,lane,mode,issued=0,checked=0;integer expected[0:1023];reg banks[0:1023];
 always @(posedge clk)if(ov)begin
  if(ot!==checked[15:0] || ob!==banks[checked])$fatal(1,"INT4 tag/bank alignment");
  for(lane=0;lane<COLS;lane=lane+1)
   if($signed(sums[lane*11+:11])!==expected[checked])
    $fatal(1,"INT4 MAC pair %0d lane %0d got=%0d expected=%0d",checked,lane,$signed(sums[lane*11+:11]),expected[checked]);
  checked=checked+1;
 end
 initial begin
  repeat(3)@(negedge clk);rst=0;
  for(mode=0;mode<2;mode=mode+1)for(a=(mode ? 0 : -8);a<=(mode ? 15 : 7);a=a+1)for(w=-8;w<=7;w=w+1)begin
   w1=w==7 ? -8 : w+1;
   for(r=0;r<8;r=r+1)begin
    @(negedge clk);we=1;row=r;wb=0;
    for(c=0;c<COLS;c=c+1)weights[c*4+:4]=w[3:0];
   end
   for(r=0;r<8;r=r+1)begin
    @(negedge clk);row=r;wb=1;
    for(c=0;c<COLS;c=c+1)weights[c*4+:4]=w1[3:0];
   end
   @(negedge clk);we=0;iv=1;ib=0;un=mode;tag=issued;acts={8{a[3:0]}};
   expected[issued]=8*a*w;banks[issued]=0;issued=issued+1;
   @(negedge clk);ib=1;tag=issued;
   expected[issued]=8*a*w1;banks[issued]=1;issued=issued+1;
   @(negedge clk);iv=0;
   repeat(8+COLS+1)@(negedge clk);
  end
  if(checked!=1024)$fatal(1,"missing INT4 MAC results %0d",checked);
  $display("PASS INT4/u4 MAC exhaustive pairs and bank tags cols=%0d outputs=%0d",COLS,checked);$finish;
 end
 initial begin #5000000;$fatal(1,"INT4 MAC timeout");end
endmodule
