// Read-only simulation observer. Samples the pre-edge state like on-chip trace.
module animation_capture;
  integer fd;
  string path;
  initial begin
    if (!$value$plusargs("CAPTURE=%s",path)) $fatal(1,"CAPTURE required");
    fd=$fopen(path,"w");
    $fdisplay(fd,"cycle,pc,state,mv,mr,mrsp,ma,av,ar,arsp,aa,wv,wr,wa,wd,wm,issue,bank_ready,slot_busy,slot_ready,inflight0,inflight1,write_count,rq_pending,flush,idle,actq,sram_en,sram_we,sram_addr,in_act,out_valid,out_sum0,post_fire,post_raw,rq_valid,rq_data");
  end
  always @(posedge tb_npu_program.clk) begin
    if(tb_npu_program.dut.i_npu.trace_active) begin
      $fdisplay(fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
        tb_npu_program.dut.i_npu.cycles,tb_npu_program.dut.i_npu.pc,tb_npu_program.dut.i_npu.state,
        tb_npu_program.dut.mv,tb_npu_program.dut.mr,tb_npu_program.dut.mrv,tb_npu_program.dut.ma,
        tb_npu_program.dut.av,tb_npu_program.dut.arr,tb_npu_program.dut.arv,tb_npu_program.dut.aa,
        tb_npu_program.dut.aw,tb_npu_program.dut.i_npu.w_ready,tb_npu_program.dut.wa,tb_npu_program.dut.wd,tb_npu_program.dut.wm,
        tb_npu_program.dut.i_npu.issue_fire,tb_npu_program.dut.i_npu.bank_ready,
        tb_npu_program.dut.i_npu.slot_busy,tb_npu_program.dut.i_npu.slot_ready,
        tb_npu_program.dut.i_npu.inflight[0],tb_npu_program.dut.i_npu.inflight[1],
        tb_npu_program.dut.i_npu.write_count,tb_npu_program.dut.i_npu.rq_pending,
        tb_npu_program.dut.core_flush,tb_npu_program.dut.act_idle,tb_npu_program.dut.i_activation.count,
        tb_npu_program.dut.i_activation.sram_en,tb_npu_program.dut.i_activation.sram_we,tb_npu_program.dut.i_activation.sram_addr,
        tb_npu_program.dut.i_npu.i_array.in_act,tb_npu_program.dut.i_npu.out_valid,
        $signed(tb_npu_program.dut.i_npu.out_sum[17:0]),tb_npu_program.dut.i_npu.post_fire,
        $signed(tb_npu_program.dut.i_npu.post_raw),tb_npu_program.dut.i_npu.rq_valid,$signed(tb_npu_program.dut.i_npu.rq_data));
    end
  end
endmodule
