`timescale 1ns/1ps
// Passive probes only. The existing workload top retains all firmware checks.
module kws_access_profile_tb;
  kws_workload_tb workload();
  wire clk = workload.clk_i;
  wire reset_n = workload.dut.ndmreset_n;
  longint unsigned cycle = 0;
  always @(posedge clk) cycle <= cycle + 1;
  initial $display("ACCESS_META schema=2 period_ns=20 sample=posedge_pre_nba");

  kws_access_obi_probe #(.PORT("I")) instruction_probe (
    clk, reset_n, cycle, workload.dut.instr_req, workload.dut.instr_gnt,
    workload.dut.instr_rvalid, 1'b0, workload.dut.instr_addr, 32'b0,
    workload.dut.instr_rdata);
  kws_access_obi_probe #(.PORT("D")) data_probe (
    clk, reset_n, cycle, workload.dut.data_req, workload.dut.data_gnt,
    workload.dut.data_valid, workload.dut.data_we, workload.dut.data_addr,
    workload.dut.data_wdata, workload.dut.data_rdata);

  for (genvar i=0; i<3; i++) begin : ingress_ports
    kws_access_axi_probe #(.PORT((i==0 ? "S0" : i==1 ? "S1" : "S2"))) ingress (
      clk, reset_n, cycle, workload.dut.slave[i]);
  end
  for (genvar i=0; i<4; i++) begin : egress_ports
    kws_access_axi_probe #(.PORT((i==0 ? "M0" : i==1 ? "M1" : i==2 ? "M2" : "M3"))) egress (
      clk, reset_n, cycle, workload.dut.master[i]);
  end

  // ID acceptance is a pipeline observation, not an architectural retire.
  always @(posedge clk) if (reset_n) begin
    if (workload.dut.i_cpu.core.dbg_next)
      $display("ACCESS_ID cycle=%0d time_ps=%0t pc=%08h insn=%08h", cycle, $time,
        workload.dut.i_cpu.core.dbg_insn_addr, workload.dut.i_cpu.core.dbg_insn_opcode);
    if (workload.dut.boot_req)
      $display("ACCESS_MEM cycle=%0d port=M0 we=%0d addr=%08h data=%08h", cycle,
        workload.dut.boot_we, workload.dut.boot_addr, workload.dut.boot_rdata);
    if (workload.dut.sram_req)
      $display("ACCESS_MEM cycle=%0d port=M1 we=%0d addr=%08h data=%08h", cycle,
        workload.dut.sram_we, workload.dut.sram_addr, workload.dut.sram_rdata);
    if (workload.dut.npu_req)
      $display("ACCESS_MEM cycle=%0d port=M3 we=%0d addr=%08h data=%08h", cycle,
        workload.dut.npu_we, workload.dut.npu_addr, workload.dut.npu_rdata);
    // The subsystem wires these directly to the production MMIO wrapper.
    if (workload.dut.i_npu_subsystem.i_npu_core.req_i)
      $display("ACCESS_WRAPPER cycle=%0d we=%0d addr=%08h data=%08h", cycle,
        workload.dut.i_npu_subsystem.i_npu_core.we_i,
        workload.dut.i_npu_subsystem.i_npu_core.addr_i,
        workload.dut.i_npu_subsystem.i_npu_core.rdata_o);
  end
endmodule

module kws_access_obi_probe #(parameter string PORT="I") (
  input logic clk, reset_n,
  input longint unsigned cycle,
  input logic req, gnt, rvalid, we,
  input logic [31:0] addr, wdata, rdata
);
  longint unsigned issued=0, responses=0;
  bit pending=0;
  always @(posedge clk) begin
    if (!reset_n) begin
      issued=0; responses=0; pending=0;
    end else begin
      if (req && !pending) begin
        $display("ACCESS_OBI cycle=%0d port=%s seq=%0d event=REQ we=%0d addr=%08h data=%08h",
          cycle, PORT, issued, we, addr, wdata);
        pending=1;
      end
      if (req && gnt) begin
        $display("ACCESS_OBI cycle=%0d port=%s seq=%0d event=GNT we=%0d addr=%08h data=%08h",
          cycle, PORT, issued, we, addr, wdata);
        issued++; pending=0;
      end
      if (rvalid) begin
        $display("ACCESS_OBI cycle=%0d port=%s seq=%0d event=RESP data=%08h",
          cycle, PORT, responses, rdata);
        responses++;
      end
    end
  end
endmodule

module kws_access_axi_probe #(parameter string PORT="S0") (
  input logic clk, reset_n,
  input longint unsigned cycle,
  AXI_BUS bus
);
  longint unsigned seq[5];
  bit pending[5];
  // V = first sampled VALID, H = sampled VALID && READY. Both may share a cycle.
  task automatic observe(input int ch, input string name, input logic valid,
    input logic ready, input logic [31:0] addr, data, input int id,
    input int response, input int length, input bit last);
    if (valid) begin
      if (!pending[ch]) begin
        $display("ACCESS_AXI cycle=%0d port=%s channel=%s seq=%0d event=V addr=%08h data=%08h id=%0d resp=%0d len=%0d last=%0d",
          cycle, PORT, name, seq[ch], addr, data, id, response, length, last);
        pending[ch]=1;
      end
      if (ready) begin
        $display("ACCESS_AXI cycle=%0d port=%s channel=%s seq=%0d event=H addr=%08h data=%08h id=%0d resp=%0d len=%0d last=%0d",
          cycle, PORT, name, seq[ch], addr, data, id, response, length, last);
        seq[ch]++; pending[ch]=0;
      end
    end
  endtask
  always @(posedge clk) begin
    if (!reset_n) begin
      for (int j=0; j<5; j++) begin seq[j]=0; pending[j]=0; end
    end else begin
      observe(0,"AW",bus.aw_valid,bus.aw_ready,bus.aw_addr,0,bus.aw_id,0,bus.aw_len,1);
      observe(1,"W", bus.w_valid, bus.w_ready,0,bus.w_data,0,0,0,bus.w_last);
      observe(2,"B", bus.b_valid, bus.b_ready,0,0,bus.b_id,bus.b_resp,0,1);
      observe(3,"AR",bus.ar_valid,bus.ar_ready,bus.ar_addr,0,bus.ar_id,0,bus.ar_len,1);
      observe(4,"R", bus.r_valid, bus.r_ready,0,bus.r_data,bus.r_id,bus.r_resp,0,bus.r_last);
    end
  end
endmodule
