`timescale 1ns/1ps
// The same command trace exercises native ready/valid and physical JTAG pins.
module tb_npu_program;
 reg clk=0,rst_n=0; always #5 clk=~clk;
 wire irq;
 reg [7:0] op; reg [31:0] addr,data,mask,expected,value;
 reg [4:0] expected_error;
 integer fd,rc,commands=0,waits=0,bursts=0;
`ifdef JTAG_TEST
`define PROGRAM_CORE dut.i_control.i_npu
`else
`define PROGRAM_CORE dut.i_npu
`endif
 always @(posedge clk)if(rst_n && !`PROGRAM_CORE.rst && `PROGRAM_CORE.state==`PROGRAM_CORE.EXECUTE &&
  `PROGRAM_CORE.mac_op && !`PROGRAM_CORE.kernel_prepare && `PROGRAM_CORE.issue_more &&
  `PROGRAM_CORE.gather_state==0 && `PROGRAM_CORE.issue_p!==0)
  $fatal(1,"initial activation gather must be position zero");
 always @(posedge clk)if(rst_n && !`PROGRAM_CORE.rst &&
  `PROGRAM_CORE.issue_fire && `PROGRAM_CORE.has_next &&
  `PROGRAM_CORE.peek_p!==(`PROGRAM_CORE.issue_p+5'd1))
  $fatal(1,"shadow request cursor lost alignment");
`undef PROGRAM_CORE

 string trace_path;
 reg [63:0] events[0:524287];integer event_count=0,event_index=0;
 string event_path;
`ifdef JTAG_TEST
 reg tck=1,tms=1,tdi=0;wire tdo;
 my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));
 wire event_valid=dut.i_control.aw && dut.i_control.i_npu.w_ready;
 wire [15:0] event_addr=dut.i_control.wa;
 wire [31:0] event_data=dut.i_control.wd;
 wire [7:0] event_mask=dut.i_control.wm;
 reg dummy;integer ticks=0;
 // TCK period in ns (+TCK_NS, default 16 for fast regression; 1000 = 1 MHz).
 real tck_ns=16;
 task tick(input reg m,input reg d,output reg q);
 begin tms=m;tdi=d;#(tck_ns*3/16);tck=0;#(tck_ns*9/16);q=tdo;tck=1;#(tck_ns*4/16);ticks=ticks+1;end endtask
 reg [4:0] cur_ir=5'h12;
 task ir_select(input [4:0] code);
 integer k;
 begin
  if(cur_ir!==code)begin
   tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
   for(k=0;k<5;k=k+1)tick(k==4,code[k],dummy);
   tick(1,0,dummy);tick(0,0,dummy);cur_ir=code;
  end
 end endtask
 task scan32(input [31:0] request,output reg [31:0] response);
 integer n;reg q;
 begin
  response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(n=0;n<32;n=n+1)begin tick(n==31,request[n],q);response[n]=q;end
  tick(1,0,dummy);tick(0,0,dummy);
 end endtask
 // Burst reads return the previous word; compare it one scan later.
 reg have_prev=0;reg [31:0] prev_expected,prev_mask,burst_reply;
 task scan(input [71:0] request,output reg [71:0] response);
 integer n;reg q;
 begin
  response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(n=0;n<72;n=n+1)begin tick(n==71,request[n],q);response[n]=q;end
  tick(1,0,dummy);tick(0,0,dummy);
 end endtask
 task exchange;
 reg [71:0] reply;integer n;
 begin
  ir_select(5'h12);
  scan({op,addr,data},reply);
  if(reply[69])$fatal(1,"JTAG burst fault before cmd=%0d",commands);
  if(have_prev && (reply[31:0]&prev_mask)!==(prev_expected&prev_mask))
   $fatal(1,"burst read tail got=%h expected=%h",reply[31:0],prev_expected);
  have_prev=0;n=0;reply=0;
  while(!reply[71] || reply[70])begin
   scan(0,reply);n=n+1;if(n>100)$fatal(1,"JTAG command timeout cmd=%0d op=%h addr=%h tap=%h ir=%h reply=%h pending=%h cv=%h cr=%h controller=%h",commands,op,addr,dut.i_host.tap,dut.i_host.ir,reply,dut.i_host.pending,dut.cv,dut.cr,dut.i_control.i_controller.state);
  end
  if(reply[69] || reply[68:64]!==expected_error || reply[63:32]!==addr)
   $fatal(1,"JTAG cmd=%0d op=%h addr=%h response=%h expected_error=%0d",commands,op,addr,reply,expected_error);
  value=reply[31:0];
 end endtask
`else
 reg cv=0,rr=0;wire cr,rv;wire [4:0] err;wire [31:0] rd;
`ifdef COLS4
 localparam C=4;
`else
 localparam C=8;
`endif
 npu_control_subsystem #(.COLS(C)) dut(.clk_i(clk),.rst_ni(rst_n),.cmd_valid_i(cv),.cmd_ready_o(cr),
  .cmd_op_i(op),.cmd_addr_i(addr),.cmd_data_i(data),.rsp_valid_o(rv),.rsp_ready_i(rr),
  .rsp_error_o(err),.rsp_data_o(rd),.irq_o(irq));
 wire event_valid=dut.aw && dut.i_npu.w_ready;wire [15:0] event_addr=dut.wa;
 wire [31:0] event_data=dut.wd;wire [7:0] event_mask=dut.wm;
 task exchange;
 reg [31:0] saved;reg [4:0] saved_error;
 begin
  @(negedge clk);cv=1;while(!cr)@(negedge clk);
  @(negedge clk);cv=0;while(!rv)@(negedge clk);
  saved=rd;saved_error=err;
  // Response backpressure must retain the response and never reexecute it.
  repeat(commands%4)begin @(negedge clk);if(!rv || rd!==saved || err!==saved_error)$fatal(1,"response changed under backpressure");end
  value=rd;
  if(err!==expected_error)$fatal(1,"cmd=%0d op=%h addr=%h error=%0d expected=%0d",commands,op,addr,err,expected_error);
  rr=1;@(negedge clk);rr=0;
 end endtask
`endif
 wire [31:0] event_byte_mask;
 genvar em;generate for(em=0;em<8;em=em+1)begin:mask_bits
 assign event_byte_mask[em*4+:4]={4{event_mask[em]}};end endgenerate
 always @(posedge clk)if(event_valid && event_count!=0)begin
  if(event_index>=event_count || event_addr!==events[event_index][63:48] ||
     event_mask!==events[event_index][39:32] ||
     (event_data&event_byte_mask)!==(events[event_index][31:0]&event_byte_mask))
   $fatal(1,"write event %0d addr=%h mask=%h data=%h expected=%h",event_index,event_addr,event_mask,event_data,events[event_index]);
  event_index=event_index+1;
 end
 initial begin
  if($value$plusargs("EVENTS=%s",event_path))begin
   if(!$value$plusargs("EVENT_COUNT=%d",event_count))$fatal(1,"event count required");
   $readmemh(event_path,events,0,event_count-1);
  end
  if(!$value$plusargs("TRACE=%s",trace_path))$fatal(1,"TRACE required");
`ifdef JTAG_TEST
  if($value$plusargs("TCK_NS=%f",tck_ns))$display("TCK period %0.1f ns",tck_ns);
`endif
  fd=$fopen(trace_path,"r");if(!fd)$fatal(1,"trace missing");
  // An explicit assertion edge initializes the stopped TCK domain too.
  #1;rst_n=1;#1;rst_n=0;
  repeat(5)@(negedge clk);rst_n=1;repeat(5)@(negedge clk);
`ifdef JTAG_TEST
  repeat(6)tick(1,0,dummy);tick(0,0,dummy);
  tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
  for(rc=0;rc<5;rc=rc+1)tick(rc==4,(5'h12>>rc)&1,dummy);
  tick(1,0,dummy);tick(0,0,dummy);
`endif
  while(!$feof(fd))begin
   rc=$fscanf(fd,"%h %h %h %h %h %h\n",op,addr,data,expected_error,mask,expected);
   if(rc!=6)$fatal(1,"malformed trace");
   if(op=='hf0)begin waits=0;while(!irq)begin @(negedge clk);waits=waits+1;if(waits>2000000)$fatal(1,"job timeout");end end
   else if(op=='hf1)repeat(data)@(negedge clk);
   // Phase marker for the end-to-end timing breakdown.
`ifdef JTAG_TEST
   else if(op=='hf2)$display("PHASE %0d time_ns=%0d ticks=%0d",data,$time,ticks);
`else
   else if(op=='hf2)$display("PHASE %0d time_ns=%0d ticks=0",data,$time);
`endif
`ifdef JTAG_TEST
   else if(op=='h80)begin ir_select(5'h13);scan32(data,burst_reply);bursts=bursts+1;end
   else if(op=='h81)begin
    // Four Run-Test/Idle clocks let the read cross back through the TCK
    // synchronizer before the next Capture-DR (host: runtest 4).
    // The fast default TCK (16 ns) is barely slower than the 10 ns system
    // clock, so the system-side latency also counts: idle 8 clocks there.
    ir_select(5'h14);
    if(dut.i_host.pending)$fatal(1,"burst read captured before the previous read completed");
    scan32(0,burst_reply);repeat(tck_ns<100 ? 8 : 4)tick(0,0,dummy);bursts=bursts+1;
    if(have_prev && (burst_reply&prev_mask)!==(prev_expected&prev_mask))
     $fatal(1,"burst read got=%h expected=%h",burst_reply,prev_expected);
    have_prev=1;prev_expected=expected;prev_mask=mask;
   end
`else
   else if(op=='h80 || op=='h81)begin
    op=op=='h80 ? 2 : 1;exchange;bursts=bursts+1;
    if((value&mask)!==(expected&mask))$fatal(1,"burst cmd=%0d addr=%h got=%h expected=%h",commands,addr,value,expected);
   end
`endif
   else begin
    exchange;
    if(op==1 && (addr=='h38 || addr=='h40))$display("METRIC reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h100 && addr<'h160)$display("PERF reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h200 && addr<'h1200)$display("OPTRACE reg=%h value=%0d",addr,value);
    if(op==1 && expected_error==0 && addr>='h190 && addr<='h19c)$display("TRACEHDR reg=%h value=%0d",addr,value);
    if((value&mask)!==(expected&mask))$fatal(1,"cmd=%0d op=%h addr=%h got=%h expected=%h mask=%h",commands,op,addr,value,expected,mask);
    commands=commands+1;
   end
  end
  $fclose(fd);
  if(event_index!=event_count)$fatal(1,"missing write events %0d/%0d",event_index,event_count);
`ifdef JTAG_TEST
  // Flush a trailing burst read and its sticky fault through IR=12h.
  if(have_prev)begin op=1;addr=4;data=0;expected_error=0;exchange;end
  $display("JTAG ticks=%0d tck_ns=%0.1f bursts=%0d sim_ns=%0t",ticks,tck_ns,bursts,$time);
`endif
  chk.report();
  $display("PASS program commands=%0d",commands);$finish;
 end
 npu_protocol_checker chk();
 initial begin #1000000000;$fatal(1,"global timeout");end
endmodule

// Always-on protocol assertions and functional-coverage counters for every
// program simulation (regression, limits, fuzz, packaged models; native and
// JTAG; behavioral, FPGA_MEM and RA1SHD memories). Any violated invariant
// stops the run with $fatal; coverage bins are printed as COVER lines on PASS.
module npu_protocol_checker;
`ifdef JTAG_TEST
`define CHK_SUB tb_npu_program.dut.i_control
`else
`define CHK_SUB tb_npu_program.dut
`endif
`define CHK_CORE `CHK_SUB.i_npu
`define CHK_SH `CHK_SUB.i_activation
`define CHK_ST `CHK_SUB.i_activation.i_store
 wire clk=`CHK_CORE.clk, rst=`CHK_CORE.rst;
 wire running=`CHK_SUB.running;
 integer mo=0, max_mo=0;
 integer run_cycles=0, array_active=0, array_issue=0, pe_clocks=0;
 reg [3:0] prev_state=0;
 wire [16:0] model_base={1'b0,16'd16384-`CHK_CORE.model_bytes}<<1;   // nibble address
 // Coverage bins: name -> hit count.
 localparam NB=32;
 integer cov[0:NB-1];
 integer k;
 initial for(k=0;k<NB;k=k+1)cov[k]=0;
 task automatic hit(input integer i); cov[i]=cov[i]+1; endtask
 always @(posedge clk) if(!rst) begin
  // --- X propagation on control after reset ---
  if(^{`CHK_CORE.state,`CHK_CORE.m_valid,`CHK_CORE.a_valid,`CHK_CORE.w_valid}===1'bx)
   $fatal(1,"CHK X on core control state=%b m=%b a=%b w=%b",`CHK_CORE.state,`CHK_CORE.m_valid,`CHK_CORE.a_valid,`CHK_CORE.w_valid);
  if(`CHK_CORE.m_valid && ^`CHK_CORE.m_addr===1'bx)$fatal(1,"CHK X on model read address");
  if(`CHK_CORE.a_valid && running && ^`CHK_CORE.a_addr===1'bx)$fatal(1,"CHK X on activation read address");
  if(`CHK_CORE.w_valid && (^`CHK_CORE.w_addr===1'bx || ^`CHK_CORE.w_mask===1'bx))$fatal(1,"CHK X on write address/mask");
  if(`CHK_CORE.m_rsp_valid && ^`CHK_CORE.m_data===1'bx)$fatal(1,"CHK X in model response data");
  // --- model read protocol at the core interface: in order, bounded, drained ---
  if(`CHK_CORE.m_valid && `CHK_CORE.m_ready) begin
   if(`CHK_CORE.m_addr>=`CHK_CORE.model_bytes || `CHK_CORE.m_addr[0])
    $fatal(1,"CHK model read address %h outside image of %0d bytes",`CHK_CORE.m_addr,`CHK_CORE.model_bytes);
  end
  if(`CHK_CORE.m_rsp_valid && mo==0 && !(`CHK_CORE.m_valid && `CHK_CORE.m_ready))
   $fatal(1,"CHK model response without an outstanding request");
  // Reads of the finished descriptor must be drained before the first cycle
  // after EXECUTE (that cycle may already issue the next descriptor's read).
  if(prev_state==`CHK_CORE.EXECUTE && `CHK_CORE.state!=`CHK_CORE.EXECUTE &&
     `CHK_CORE.state!=`CHK_CORE.FAILED && `CHK_CORE.state!=`CHK_CORE.IDLE && mo!=0)
   $fatal(1,"CHK descriptor finished with %0d model reads outstanding",mo);
  mo = mo + (`CHK_CORE.m_valid && `CHK_CORE.m_ready) - `CHK_CORE.m_rsp_valid;
  if(mo<0)$fatal(1,"CHK model response count exceeded requests");
  if(mo>3)$fatal(1,"CHK more than three model reads outstanding (%0d)",mo);
  if(mo>max_mo)max_mo=mo;
  if(mo==2)hit(0); if(mo==3)hit(1);
  // --- weight-load queue invariants (A2) ---
  if({1'b0,`CHK_CORE.l_out}+{1'b0,`CHK_CORE.lq_count}>3)$fatal(1,"CHK weight queue over-subscribed");
  if(`CHK_CORE.state==`CHK_CORE.EXECUTE && `CHK_CORE.lstate==`CHK_CORE.L_IDLE &&
     (`CHK_CORE.l_out!=0 || `CHK_CORE.lq_count!=0))$fatal(1,"CHK weight reads left over after a bank load");
  if(`CHK_CORE.lq_count==3)hit(2);
  if(`CHK_CORE.lq_push && `CHK_CORE.lq_pop)hit(3);
  if(`CHK_CORE.array_weight_we && `CHK_CORE.dw_hold)hit(4);
  if(`CHK_CORE.array_weight_we && `CHK_CORE.load_zero)hit(5);
  if(`CHK_CORE.array_weight_we && `CHK_CORE.inflight[`CHK_CORE.load_bank]==0 && `CHK_CORE.lq_count!=0)hit(6);
  // --- memory map: activation traffic never touches the model image ---
  if(running && `CHK_CORE.a_valid && `CHK_CORE.a_ready && {1'b0,`CHK_CORE.a_addr}>=model_base)
   $fatal(1,"CHK activation read %h inside model image (base %h)",`CHK_CORE.a_addr,model_base);
  if(running && `CHK_CORE.w_valid && `CHK_CORE.w_ready && {1'b0,`CHK_CORE.w_addr}>=model_base)
   $fatal(1,"CHK activation write %h inside model image (base %h)",`CHK_CORE.w_addr,model_base);
  // --- dual-macro side port (A) ---
  if(`CHK_ST.side_fire && `CHK_ST.sram_en && `CHK_ST.main_bank==`CHK_ST.side_bank)
   $fatal(1,"CHK side and main port access the same macro in one cycle");
  if(`CHK_ST.side_fire && `CHK_ST.sram_en)hit(7);              // both macros busy at once
  if(`CHK_ST.side_valid && !`CHK_ST.side_ready)hit(8);          // side blocked by bank conflict
  if(`CHK_ST.side_claim)hit(9);                                 // side claims the macros after waiting
  if(`CHK_SH.model_dirty && `CHK_SH.model_read)hit(10);
  // --- queue/slot bounds ---
  if(`CHK_ST.count>8)$fatal(1,"CHK store write queue overflow");
  if(`CHK_CORE.write_count>8)$fatal(1,"CHK core output FIFO overflow");
  if(`CHK_ST.count==8)hit(11);
  if(`CHK_ST.count>=6)hit(12);
  if(`CHK_ST.state==`CHK_ST.S_HELD)hit(13);
  if(`CHK_ST.state==`CHK_ST.S_RMW)hit(14);
  if(`CHK_ST.state==`CHK_ST.S_FIRST)hit(15);
  if(`CHK_CORE.write_count==8)hit(16);
  if(`CHK_CORE.slot_busy==4'hf)hit(17);
  if(running && `CHK_CORE.a_valid && !`CHK_CORE.a_ready)hit(18);
  if(running && `CHK_CORE.w_valid && !`CHK_CORE.w_ready)hit(19);
  // --- descriptor mix seen at EXECUTE entry ---
  if(prev_state!=`CHK_CORE.EXECUTE && `CHK_CORE.state==`CHK_CORE.EXECUTE) begin
   case(`CHK_CORE.op)
    `CHK_CORE.CONV: hit(`CHK_CORE.dwconv ? 21 : (`CHK_CORE.rowpad ? 22 : 20));
    `CHK_CORE.FC: hit(23);
    `CHK_CORE.POOL: hit(24);
    `CHK_CORE.RELU: hit(25);
    `CHK_CORE.REQUANT: hit(26);
    `CHK_CORE.AVGPOOL: hit(27);
    `CHK_CORE.ADD: hit(28);
   endcase
   if(`CHK_CORE.mac_op && `CHK_CORE.pool_enable)hit(29);
   if(`CHK_CORE.mac_op && `CHK_CORE.narrow)hit(30);
  end
  if(`CHK_CORE.state==`CHK_CORE.FAILED && prev_state!=`CHK_CORE.FAILED)hit(31);
  // --- power proxy (R1): array register clock-enable duty while the NPU runs ---
  if(running)begin
   run_cycles=run_cycles+1;
   if(`CHK_CORE.i_array.active)array_active=array_active+1;
   // PE register clocks this cycle: diagonal d of the 8x8 array holds min(d,14-d)+1 PEs.
   for(k=0;k<15;k=k+1)if(`CHK_CORE.i_array.wave[k])pe_clocks=pe_clocks+((k<7?k:14-k)+1);
   if(`CHK_CORE.i_array.in_valid)array_issue=array_issue+1;
  end
  prev_state<=`CHK_CORE.state;
 end else mo=0;
 function [8*24-1:0] bin_name(input integer i);
  case(i)
   0: bin_name="model_outstanding_2";
   1: bin_name="model_outstanding_3";
   2: bin_name="weight_queue_full";
   3: bin_name="weight_queue_push_pop";
   4: bin_name="dw_row_from_hold";
   5: bin_name="zero_pad_row";
   6: bin_name="row_from_queue";
   7: bin_name="both_macros_busy";
   8: bin_name="side_port_blocked";
   9: bin_name="side_port_claim";
   10: bin_name="model_read_while_dirty";
   11: bin_name="store_queue_full";
   12: bin_name="store_queue_urgent";
   13: bin_name="store_held_response";
   14: bin_name="store_rmw";
   15: bin_name="store_two_word_read";
   16: bin_name="core_fifo_full";
   17: bin_name="result_slots_full";
   18: bin_name="act_read_backpressure";
   19: bin_name="act_write_backpressure";
   20: bin_name="op_conv";
   21: bin_name="op_depthwise";
   22: bin_name="op_conv_rowpad";
   23: bin_name="op_fc";
   24: bin_name="op_maxpool";
   25: bin_name="op_relu";
   26: bin_name="op_requant";
   27: bin_name="op_avgpool";
   28: bin_name="op_add";
   29: bin_name="fused_pool";
   30: bin_name="narrow_tile";
   31: bin_name="descriptor_failed";
   default: bin_name="unknown";
  endcase
 endfunction
 task report;
  begin
   $display("COVER max_model_outstanding %0d",max_mo);
   $display("POWER run_cycles=%0d array_active=%0d array_issue=%0d pe_clocks=%0d pe_clocks_ungated=%0d",
    run_cycles,array_active,array_issue,pe_clocks,64*run_cycles);
   for(k=0;k<NB;k=k+1)$display("COVER %0s %0d",bin_name(k),cov[k]);
  end
 endtask
`undef CHK_CORE
`undef CHK_SH
`undef CHK_ST
`undef CHK_SUB
endmodule
