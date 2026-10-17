`timescale 1ns/1ps
// PicoRV32 native valid/ready -> single-beat AXI4. The bridge is reset by
// the SoC reset, independently of the CPU run control, so a CPU restart
// cannot abandon a partially accepted AW/W or an outstanding response.
module picorv32_soc #(
    parameter type axi_req_t = logic,
    parameter type axi_rsp_t = logic,
    parameter logic [31:0] RESET_PC = 32'h0001_0000,
    parameter logic [31:0] IRQ_PC = 32'h8000_0010
) (
    input logic clk_i, rst_ni, run_i,
    input logic [31:0] irq_i,
    output logic stopped_o, trap_o,
    output axi_req_t axi_req_o,
    input axi_rsp_t axi_rsp_i,
    // Passive native-port observation, also used by the workload probes.
    output wire mem_valid, mem_ready, mem_instr,
    output wire [31:0] mem_addr, mem_wdata, mem_rdata,
    output wire [3:0] mem_wstrb
);
    logic core_reset_n, core_trap, bus_error;
    logic pending, write_q, aw_pending, w_pending, ar_pending;
    logic [31:0] addr_q, data_q;
    logic [3:0] strb_q;
    wire read_done = axi_req_o.r_ready && axi_rsp_i.r_valid;
    wire write_done = axi_req_o.b_ready && axi_rsp_i.b_valid;
    wire complete = read_done || write_done;

    assign mem_ready = complete && core_reset_n;
    assign mem_rdata = axi_rsp_i.r.data;
    assign trap_o = core_trap || bus_error;
    assign stopped_o = !core_reset_n && !pending;

    picorv32 #(
        .ENABLE_COUNTERS(1), .ENABLE_COUNTERS64(1),
        .ENABLE_REGS_16_31(1), .ENABLE_REGS_DUALPORT(0),
        .COMPRESSED_ISA(1), .ENABLE_MUL(1), .ENABLE_FAST_MUL(0),
        .ENABLE_DIV(1), .ENABLE_IRQ(1), .ENABLE_IRQ_QREGS(1),
        .ENABLE_IRQ_TIMER(0), .LATCHED_IRQ(32'hfffc_ffff),
        .PROGADDR_RESET(RESET_PC), .PROGADDR_IRQ(IRQ_PC),
        .STACKADDR(32'h8000_1fe0)
    ) core (
        .clk(clk_i), .resetn(core_reset_n), .trap(core_trap),
        .mem_valid(mem_valid), .mem_ready(mem_ready), .mem_instr(mem_instr),
        .mem_addr(mem_addr), .mem_wdata(mem_wdata), .mem_wstrb(mem_wstrb),
        .mem_rdata(mem_rdata), .irq(irq_i), .eoi(),
        .mem_la_read(), .mem_la_write(), .mem_la_addr(),
        .mem_la_wdata(), .mem_la_wstrb(),
        .pcpi_valid(), .pcpi_insn(), .pcpi_rs1(), .pcpi_rs2(),
        .pcpi_wr(1'b0), .pcpi_rd(32'b0), .pcpi_wait(1'b0), .pcpi_ready(1'b0),
        .trace_valid(), .trace_data()
    );

    always_comb begin
        axi_req_o = '0;
        axi_req_o.aw.addr = addr_q;
        axi_req_o.aw.size = 3'd2;
        axi_req_o.aw.burst = axi_pkg::BURST_INCR;
        axi_req_o.aw_valid = aw_pending;
        axi_req_o.w.data = data_q;
        axi_req_o.w.strb = strb_q;
        axi_req_o.w.last = 1'b1;
        axi_req_o.w_valid = w_pending;
        axi_req_o.b_ready = pending && write_q && !aw_pending && !w_pending;
        axi_req_o.ar.addr = addr_q;
        axi_req_o.ar.size = 3'd2;
        axi_req_o.ar.burst = axi_pkg::BURST_INCR;
        axi_req_o.ar_valid = ar_pending;
        axi_req_o.r_ready = pending && !write_q && !ar_pending;
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            core_reset_n <= 0;
            pending <= 0; write_q <= 0;
            aw_pending <= 0; w_pending <= 0; ar_pending <= 0;
            addr_q <= 0; data_q <= 0; strb_q <= 0;
            bus_error <= 0;
        end else begin
            if (!run_i || bus_error)
                core_reset_n <= 0;
            else if (!pending)
                core_reset_n <= 1;
            if (!run_i && !pending) bus_error <= 0;

            if (!pending && core_reset_n && run_i && mem_valid) begin
                pending <= 1;
                write_q <= |mem_wstrb;
                // PicoRV32 already supplies word-aligned addresses and
                // shifted byte strobes/data for SB/SH as well as SW.
                addr_q <= {mem_addr[31:2], 2'b00};
                data_q <= mem_wdata;
                strb_q <= mem_wstrb;
                aw_pending <= |mem_wstrb;
                w_pending <= |mem_wstrb;
                ar_pending <= !(|mem_wstrb);
            end
            if (axi_req_o.aw_valid && axi_rsp_i.aw_ready) aw_pending <= 0;
            if (axi_req_o.w_valid && axi_rsp_i.w_ready) w_pending <= 0;
            if (axi_req_o.ar_valid && axi_rsp_i.ar_ready) ar_pending <= 0;
            if (complete) begin
                pending <= 0;
                if ((read_done && axi_rsp_i.r.resp != axi_pkg::RESP_OKAY) ||
                    (write_done && axi_rsp_i.b.resp != axi_pkg::RESP_OKAY))
                    bus_error <= 1;
            end
        end
    end
endmodule
