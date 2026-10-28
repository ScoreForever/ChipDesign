`timescale 1ns/1ps
// Optional per-descriptor summaries plus optional synchronous waveform RAM.
// ASIC synthesis omits both by default (the summaries are 16x8 32-bit
// counters in flip-flops, about 0.2 mm^2 in TSMC 180); simulation/FPGA keep
// them. Register 0x1a8 reports 0 entries when summaries are absent.
// RAM payload is never reset. Only captured words may be read through the ABI.
module npu_trace_capture #(parameter DEPTH=1024,MAX_OPS=16,
`ifdef NPU_ENABLE_WAVE_TRACE
 parameter WAVE_ENABLE=`NPU_ENABLE_WAVE_TRACE
`elsif SYNTHESIS
`ifdef CHIPDESIGN_FPGA_MEM
 parameter WAVE_ENABLE=1
`else
 parameter WAVE_ENABLE=0
`endif
`else
 parameter WAVE_ENABLE=1
`endif
,
`ifdef NPU_ENABLE_OP_TRACE
 parameter OPS_ENABLE=`NPU_ENABLE_OP_TRACE
`elsif SYNTHESIS
`ifdef CHIPDESIGN_FPGA_MEM
 parameter OPS_ENABLE=1
`else
 parameter OPS_ENABLE=0
`endif
`else
 parameter OPS_ENABLE=1
`endif
)(
 input wire clk,clear,active,
 input wire [127:0] sample,
 input wire [3:0] events,
 input wire [31:0] descriptor,control,offset,
 input wire read_request,input wire [15:0] read_addr,
 output reg read_valid,output reg [31:0] read_data,output reg [4:0] read_error
);
 localparam AW=$clog2(DEPTH);
 reg active_q;reg [127:0] sample_q;reg [3:0] events_q;reg [31:0] descriptor_q;
 wire [31:0] stamp=sample_q[31:0],signals=sample_q[63:32];
 wire [3:0] pc=sample_q[123:120];
 reg previous_active;reg [3:0] previous_pc;reg [31:0] age;
 wire [31:0] current_age=previous_active && previous_pc==pc ? age : 32'd0;
 reg [31:0] op_start[0:MAX_OPS-1],op_end[0:MAX_OPS-1],op_descriptor[0:MAX_OPS-1],op_flags[0:MAX_OPS-1];
 reg [31:0] wait_activation[0:MAX_OPS-1],wait_weight[0:MAX_OPS-1],wait_slot[0:MAX_OPS-1],mac_issues[0:MAX_OPS-1];
 reg [AW:0] count;
 reg started,finished,truncated,saw_active;
 reg [31:0] first_stamp,last_stamp;
 reg [31:0] captured_control,captured_offset;
 wire eligible=WAVE_ENABLE && active_q && control[0] && pc==control[7:4] && current_age>=offset &&
     (started || !control[1] || signals[10]);
 wire capture=eligible && count<DEPTH && !clear;
 wire buffer_read=WAVE_ENABLE && read_request && read_addr>='h1000 && read_addr<'h1000+DEPTH*16 &&
     ((read_addr-'h1000)>>4)<count && read_addr[1:0]==0 && !clear;
 wire [AW-1:0] read_index=(read_addr-'h1000)>>4;
 wire [31:0] waveform_data;
 reg [1:0] read_word;
 reg buffer_q;
 reg [31:0] register_data;
 // The generate condition removes every waveform array from an ASIC netlist,
 // while leaving operator boundaries and passive counters independent of it.
 generate if(WAVE_ENABLE)begin:g_wave
  (* ram_style="block" *) reg [31:0] ram0[0:DEPTH-1],ram1[0:DEPTH-1],ram2[0:DEPTH-1],ram3[0:DEPTH-1];
  reg [31:0] data0,data1,data2,data3;
  // Separate RAM ports have no reset, permitting synchronous BRAM mapping.
  always @(posedge clk)begin
   if(capture)begin
    ram0[count[AW-1:0]]<=sample_q[31:0];ram1[count[AW-1:0]]<=sample_q[63:32];
    ram2[count[AW-1:0]]<=sample_q[95:64];ram3[count[AW-1:0]]<=sample_q[127:96];
   end
   if(buffer_read)begin
    data0<=ram0[read_index];data1<=ram1[read_index];data2<=ram2[read_index];data3<=ram3[read_index];
   end
  end
  assign waveform_data=read_word==0 ? data0 : read_word==1 ? data1 : read_word==2 ? data2 : data3;
 end else begin:g_no_wave
  assign waveform_data=32'd0;
 end endgenerate
 always @* begin
  if(!buffer_q)read_data=register_data;
  else read_data=waveform_data;
 end
 integer i;
 wire [3:0] op_index=read_addr[8:5];
 always @(posedge clk) begin
  if(clear)begin
   active_q<=0;sample_q<=0;events_q<=0;descriptor_q<=0;
   previous_active<=0;previous_pc<=0;age<=0;count<=0;
   started<=0;finished<=0;truncated<=0;saw_active<=0;first_stamp<=0;last_stamp<=0;
   captured_control<=0;captured_offset<=0;
   read_valid<=0;read_error<=0;register_data<=0;buffer_q<=0;read_word<=0;
   for(i=0;i<MAX_OPS;i=i+1)begin
    op_start[i]<=0;op_end[i]<=0;op_descriptor[i]<=0;op_flags[i]<=0;
    wait_activation[i]<=0;wait_weight[i]<=0;wait_slot[i]<=0;mac_issues[i]<=0;
   end
  end else begin
   active_q<=active;sample_q<=sample;events_q<=events;descriptor_q<=descriptor;
   previous_active<=active_q;previous_pc<=pc;
   if(active_q)begin
    if(WAVE_ENABLE && !saw_active)begin captured_control<=control;captured_offset<=offset;end
    saw_active<=1;age<=current_age+1'b1;
    // Never written when disabled: the reset-to-zero arrays are then constant
    // and synthesis removes them together with their read multiplexer.
    if(OPS_ENABLE)begin
     if(events_q[0] && !op_flags[pc][0])begin op_start[pc]<=stamp;op_flags[pc][0]<=1;end
     if(events_q[1] || events_q[3])begin
      op_end[pc]<=stamp+1'b1;op_flags[pc][1]<=events_q[1];op_flags[pc][2]<=events_q[3];
     end
     if(events_q[2])begin op_descriptor[pc]<=descriptor_q;op_flags[pc][3]<=1;end
     if(signals[8])wait_activation[pc]<=wait_activation[pc]+1'b1;
     if(signals[9])wait_weight[pc]<=wait_weight[pc]+1'b1;
     if(signals[10])wait_slot[pc]<=wait_slot[pc]+1'b1;
     if(signals[7])mac_issues[pc]<=mac_issues[pc]+1'b1;
    end
   end else if(saw_active)finished<=1;
   if(capture)begin
    count<=count+1'b1;started<=1;last_stamp<=stamp;
    if(!started)first_stamp<=stamp;
   end else if(eligible && count==DEPTH)truncated<=1;
   read_valid<=read_request;read_error<=0;buffer_q<=buffer_read;read_word<=read_addr[3:2];
   if(read_request)begin
    register_data<=0;
    if(read_addr[1:0]!=0)read_error<=3;
    else if(read_addr>='h200 && read_addr<'h200+MAX_OPS*32)begin
     if(!OPS_ENABLE)read_error<=2;
     else case(read_addr[4:2])
      0:register_data<=op_start[op_index];1:register_data<=op_end[op_index];
      2:register_data<=op_descriptor[op_index];3:register_data<=op_flags[op_index];
      4:register_data<=wait_activation[op_index];5:register_data<=wait_weight[op_index];
      6:register_data<=wait_slot[op_index];7:register_data<=mac_issues[op_index];
     endcase
    end else if(read_addr>='h1000 && read_addr<'h1000+DEPTH*16)begin
     if(!buffer_read)read_error<=2;
    end else case(read_addr)
     'h180:register_data<=32'h00010080;'h184:register_data<=WAVE_ENABLE ? DEPTH : 0;
     'h188:register_data<=control;'h18c:register_data<=offset;
     'h190:register_data<={28'd0,truncated,finished,(count==DEPTH),started};
     'h194:register_data<=count;'h198:register_data<=first_stamp;'h19c:register_data<=last_stamp;
     'h1a0:register_data<=16;'h1a4:register_data<=32;'h1a8:register_data<=OPS_ENABLE ? MAX_OPS : 0;
     'h1ac:register_data<=captured_control;'h1b0:register_data<=captured_offset;
     default:read_error<=2;
    endcase
   end
  end
 end
 initial if(DEPTH!=1024 || MAX_OPS!=16 || (WAVE_ENABLE!=0 && WAVE_ENABLE!=1) || (OPS_ENABLE!=0 && OPS_ENABLE!=1))
  $fatal(1,"unsupported trace geometry/configuration");
endmodule
