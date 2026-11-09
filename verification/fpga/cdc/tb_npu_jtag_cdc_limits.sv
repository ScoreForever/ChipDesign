`timescale 1ns/1ps
// CDC limit bench for the JTAG mailbox (npu_jtag_transport).
//
// One parameterised run = one operating point. Knobs (plusargs, all optional):
//   +SYS_NS=<real>      system clock period                       (default 20)
//   +TCK_LO=<real> +TCK_HI=<real>  TCK half-period range; each transaction
//                       draws a rate from it                      (default 37/37)
//   +JITTER=1           redraw the TCK half period on every edge (duty/jitter)
//   +META=<pct>         per synchronizer capture, probability that the first
//                       flop resolves one cycle late (metastability model)
//   +RESET=<pct>        per transaction, probability of an asynchronous reset
//                       at a random instant with a random (possibly sub-cycle) width
//   +BP=<cycles>        max random destination backpressure / reply latency
//   +BOUND_NS=<real>    bundled-data skew bound the constraints promise (default 20)
//   +N=<count> +SEED=<n>
//
// Always-on checks (fail with $fatal): bundled payload stable while owned,
// exactly-once delivery, untorn request, correct reply/error after polling,
// no stale command or reply after reset, recovery after every reset.
// Measured (printed in the RESULT line): minimum bundled-data margin in each
// direction (time from the last payload change to the receiving capture edge),
// host polls needed for a reply, metastability injections and resets applied.
// A margin below BOUND_NS is an operating-envelope violation, not a logic bug.
module tb_npu_jtag_cdc_limits;
  real sys_ns=20, tck_lo=37, tck_hi=37, bound_ns=20, half;
  integer n_tx=200, seed=1, meta_pct=0, reset_pct=0, bp=8, jitter=0;
  reg clk=0, rst_n=0, tck=0, tms=1, tdi=0;
  always begin #(sys_ns/2.0) clk=~clk; end

  wire tdo, valid, rsp_ready;
  wire [7:0] op;
  wire [31:0] addr, data;
  reg ready=0, rsp_valid=0;
  reg [31:0] rsp_data=0;
  reg [4:0] rsp_error=0;
  npu_jtag_transport dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),
    .tms_i(tms),.td_i(tdi),.td_o(tdo),.cmd_valid_o(valid),.cmd_ready_i(ready),
    .cmd_op_o(op),.cmd_addr_o(addr),.cmd_data_o(data),.rsp_valid_i(rsp_valid),
    .rsp_ready_o(rsp_ready),.rsp_error_i(rsp_error),.rsp_data_i(rsp_data));

  function automatic real draw(input real lo, input real hi);
    draw = lo + (hi-lo)*($urandom%10001)/10000.0;
  endfunction

  // ---------------- metastability model: late resolution of first flops -------------
  integer meta_req=0, meta_ack=0, meta_rst=0;
  reg prev_req0, prev_ack0, prev_srst0, prev_trst0;
  always @(posedge clk) begin
    prev_req0 = dut.req_sync[0]; prev_srst0 = dut.sys_reset_sync[0];
    #0.001;
    if(meta_pct && dut.req_sync[0]!==prev_req0 && ($urandom%100)<meta_pct) begin
      dut.req_sync[0] = prev_req0; meta_req=meta_req+1; end
    if(meta_pct && dut.sys_reset_sync[0]!==prev_srst0 && ($urandom%100)<meta_pct) begin
      dut.sys_reset_sync[0] = prev_srst0; meta_rst=meta_rst+1; end
  end
  always @(posedge tck) begin
    prev_ack0 = dut.ack_sync[0]; prev_trst0 = dut.tck_reset_sync[0];
    #0.001;
    if(meta_pct && dut.ack_sync[0]!==prev_ack0 && ($urandom%100)<meta_pct) begin
      dut.ack_sync[0] = prev_ack0; meta_ack=meta_ack+1; end
    if(meta_pct && dut.tck_reset_sync[0]!==prev_trst0 && ($urandom%100)<meta_pct) begin
      dut.tck_reset_sync[0] = prev_trst0; meta_rst=meta_rst+1; end
  end

  // ---------------- bundled-data margin and stability -------------------------------
  real t_req_change=-1e9, t_rsp_change=-1e9, min_req_margin=1e9, min_rsp_margin=1e9, m;
  reg [71:0] last_req; reg [36:0] last_rsp;
  always @(dut.req_payload) t_req_change=$realtime;
  always @(dut.rsp_mailbox_data or dut.rsp_mailbox_error) t_rsp_change=$realtime;
  // Request: system domain captures req_payload into cmd_* on this edge.
  always @(posedge clk) if(rst_n && dut.sys_rst_n && dut.req_sync[1]!=dut.req_seen && !dut.cmd_valid_o && !dut.waiting_reply) begin
    m=$realtime-t_req_change; if(m<min_req_margin) min_req_margin=m; end
  // Response: TCK domain captures the mailbox into reply_* on this edge.
  always @(posedge tck) if(rst_n && dut.tck_rst_n && dut.pending && dut.ack_sync[1]==dut.req_toggle) begin
    m=$realtime-t_rsp_change; if(m<min_rsp_margin) min_rsp_margin=m; end
  // Ownership: the request payload is frozen while pending; the response mailbox
  // is frozen from publication until the next accepted command.
  reg req_owned=0, rsp_owned=0;
  always @(negedge tck) begin
    if(!rst_n || !dut.tck_rst_n) req_owned=0;
    else begin
      if(req_owned && dut.pending && dut.req_payload!==last_req) $fatal(1,"CDC request payload changed while owned");
      req_owned=dut.pending; last_req=dut.req_payload;
    end
  end
  always @(negedge clk) begin
    if(!rst_n || !dut.sys_rst_n) rsp_owned=0;
    else begin
      if(rsp_owned && !dut.waiting_reply && {dut.rsp_mailbox_error,dut.rsp_mailbox_data}!==last_rsp)
        $fatal(1,"CDC response mailbox changed without a new transaction");
      rsp_owned=!dut.waiting_reply; last_rsp={dut.rsp_mailbox_error,dut.rsp_mailbox_data};
    end
  end

  // ---------------- destination (system side) ----------------------------------------
  integer accepted=0; reg [71:0] got;
  reg dest_reset=0;
  // Random backpressure before_n accepting and random reply latency; a reset
  // restarts this process (disable), like the real controller's reset.
  always begin : dest_proc
    @(negedge clk);
    if(rst_n && valid && !dest_reset) begin
      repeat($urandom%(bp+1)) @(negedge clk);
      got={op,addr,data};
      ready=1; @(negedge clk); ready=0; accepted=accepted+1;
      repeat($urandom%(bp+1)) @(negedge clk);
      rsp_data=got[31:0]^got[63:32]^32'h5a5a0f0f; rsp_error=got[68:64];
      rsp_valid=1; wait(rsp_ready); @(negedge clk); rsp_valid=0;
    end
  end

  // ---------------- host (TCK side) ----------------------------------------------------
  integer polls=0, max_polls=0, total_polls=0, resets=0, n, i;
  task tick(input reg mm, input reg d);
    begin
      if(jitter) half=draw(tck_lo,tck_hi);
      tms=mm; tdi=d; #(half); tck=1; if(jitter) half=draw(tck_lo,tck_hi); #(half); tck=0; #0.01;
    end
  endtask
  task select_ir;
    begin
      repeat(6) tick(1,0); tick(0,0);
      tick(1,0); tick(1,0); tick(0,0); tick(0,0);
      for(i=0;i<5;i=i+1) tick(i==4,(5'h12>>i)&1);
      tick(1,0); tick(0,0);
    end
  endtask
  task scan(input reg [71:0] tx, output reg [71:0] rx);
    begin
      tick(1,0); tick(0,0); tick(0,0);
      for(i=0;i<72;i=i+1) begin rx[i]=tdo; tick(i==71,tx[i]); end
      tick(1,0); tick(0,0);
    end
  endtask
  // Asynchronous reset at a random instant with a random width (may be shorter
  // than either clock period). Both domains must come out clean.
  task automatic reset_pulse(input real width);
    begin
      rst_n=0; disable dest_proc; dest_reset=1; ready=0; rsp_valid=0; #(width); rst_n=1;
      repeat(4) @(negedge clk); dest_reset=0;
    end
  endtask

  reg [71:0] packet, resp;
  reg [31:0] want_data;
  reg hit_reset;
  integer before_n;
  initial begin
    if($value$plusargs("SYS_NS=%f",sys_ns)) ;
    if($value$plusargs("TCK_LO=%f",tck_lo)) ;
    if(!$value$plusargs("TCK_HI=%f",tck_hi)) tck_hi=tck_lo;
    if($value$plusargs("JITTER=%d",jitter)) ;
    if($value$plusargs("META=%d",meta_pct)) ;
    if($value$plusargs("RESET=%d",reset_pct)) ;
    if($value$plusargs("BP=%d",bp)) ;
    if($value$plusargs("BOUND_NS=%f",bound_ns)) ;
    if($value$plusargs("N=%d",n_tx)) ;
    if($value$plusargs("SEED=%d",seed)) ;
    i=$urandom(seed);
    half=tck_lo;
    #1; rst_n=1; #1; rst_n=0; #(3*sys_ns+3*tck_hi); rst_n=1; #(4*sys_ns);
    select_ir();
    for(n=0;n<n_tx;n=n+1) begin
      if(!jitter) half=draw(tck_lo,tck_hi);
      packet[71:64]=8'h20|($urandom%16); packet[63:32]=$urandom; packet[31:0]=$urandom;
      before_n=accepted; hit_reset=0;
      fork
        begin
          if(reset_pct && ($urandom%100)<reset_pct) begin
            #(draw(0, 200*(sys_ns+2*tck_hi)));
            hit_reset=1; resets=resets+1; reset_pulse(draw(0.2, 2*sys_ns));
          end
        end
        begin
          scan(packet,resp);
          polls=0;
          // Poll the status capture until the reply is visible (have_reply).
          do begin scan(72'd0,resp); polls=polls+1; end
          while(!resp[71] && polls<2000 && !hit_reset);
        end
      join
      if(hit_reset) begin
        // A reset may cancel the transaction at any point: at most one delivery,
        // then no stale command/reply, and the TAP must be reselected.
        if(accepted>before_n+1) $fatal(1,"CDC duplicate delivery across reset");
        repeat(8) @(negedge clk);
        if(dut.pending || dut.waiting_reply || valid) begin
          // Only legal if a brand-new transaction was not started: none was.
          $fatal(1,"CDC state survived reset: pending=%0d waiting=%0d valid=%0d",dut.pending,dut.waiting_reply,valid);
        end
        select_ir();
      end else begin
      if(polls>=2000) $fatal(1,"CDC reply never became visible (tx %0d)",n);
      if(accepted!=before_n+1) $fatal(1,"CDC lost or duplicated command (tx %0d: %0d)",n,accepted-before_n);
      if(got!==packet) $fatal(1,"CDC torn/incorrect request got=%h want=%h",got,packet);
      want_data=packet[31:0]^packet[63:32]^32'h5a5a0f0f;
      if(resp[31:0]!==want_data || resp[68:64]!==packet[68:64] || resp[63:32]!==packet[63:32])
        $fatal(1,"CDC wrong reply tx %0d resp=%h",n,resp);
      if(polls>max_polls) max_polls=polls;
      total_polls=total_polls+polls;
      end
    end
    $display("RESULT sys_ns=%0.2f tck_lo=%0.2f tck_hi=%0.2f jitter=%0d meta=%0d reset=%0d n=%0d resets=%0d meta_req=%0d meta_ack=%0d meta_rst=%0d min_req_margin_ns=%0.3f min_rsp_margin_ns=%0.3f max_polls=%0d mean_polls=%0.2f bound_ns=%0.1f envelope=%s",
      sys_ns,tck_lo,tck_hi,jitter,meta_pct,reset_pct,n_tx,resets,meta_req,meta_ack,meta_rst,
      min_req_margin,min_rsp_margin,max_polls,1.0*total_polls/(n_tx-resets>0?n_tx-resets:1),bound_ns,
      (min_req_margin>=bound_ns && min_rsp_margin>=bound_ns) ? "inside" : "OUTSIDE");
    $display("PASS NPU_JTAG_CDC_LIMITS");
    $finish;
  end
  initial begin #2e9; $fatal(1,"CDC global timeout"); end
endmodule
