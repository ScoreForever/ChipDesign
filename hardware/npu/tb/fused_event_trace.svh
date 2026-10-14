// Simulation-only observer: events before NBA, counters/results after NBA.
integer event_fd=0, event_job=-1, event_cycle=0;
reg event_active=0;
string event_path;
initial begin
    if ($value$plusargs("EVENT_TRACE=%s",event_path)) begin
        event_fd=$fopen(event_path,"w");
        if(!event_fd)$fatal(1,"cannot open event trace");
        $fdisplay(event_fd,"job,cycle,start,busy,phase,load,lb,lr,la,lw,issue,ib,itag,ia,ibase,retire,rb,rtag,psum,rsum,rq,qa,qv,qm,qs,qout,oa,ov,write,wa,wv,done,total,c1,c2,fc,weights,issues,retires,peak,valid,logits");
    end
end
always @(posedge clk) begin
    if(rst) event_active=0;
    else if(event_fd) begin
        if(start_valid && start_ready) begin
            event_job=event_job+1;event_cycle=0;event_active=1;
        end else if(event_active) event_cycle=event_cycle+1;
        if(event_active) begin
            $fwrite(event_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h,%0d,%0d,%h,%h,%0d,%0d,%0d,%h,%h,%h,%0d,%0d,%h,%h,%h,%0d,%0d,%h,%0d,%0d,%h,",
                event_job,event_cycle,start_valid && start_ready,busy,dut.phase,
                dut.load_active,dut.load_bank,dut.load_row_now,dut.load_addr,dut.load_data,
                dut.issue_fire,dut.issue_seq[0],dut.issue_tag,dut.issue_act,dut.act_read_base,
                dut.out_valid,dut.out_bank,dut.out_tag,dut.out_psum,dut.ret_sum,
                dut.rd_fire,dut.rd_addr,dut.win_buf[dut.rd_win][dut.rd_lane*32 +: 32],
                dut.rd_mult[dut.rd_lane*32 +: 32],dut.rd_shift[dut.rd_lane*6 +: 6],
                dut.rq_out_valid,dut.rq_out_addr,dut.rq_out_data,
                dut.act_we,dut.act_waddr,dut.act_wdata);
            #1;
            $fdisplay(event_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%h",
                done,dut.perf_total_cycles,dut.perf_layer_cycles[31:0],
                dut.perf_layer_cycles[95:64],dut.perf_layer_cycles[191:160],
                dut.perf_weight_rows,dut.perf_matrix_issues,dut.perf_matrix_retires,
                dut.perf_peak_inflight,dut.perf_valid,logits);
            if(done)event_active=0;
        end
    end
end
final begin
    if(event_fd)$fclose(event_fd);
end
