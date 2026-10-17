`timescale 1ns/1ps
// DMI-level SBA regression. The physical JTAG TAP is bypassed deliberately;
// Debug Module, AXI fabric, BootRAM control, SRAM and CPU all remain real.
module tb_picorv32_soc;
    logic clk=0, rst_n=0;
    always #5 clk=~clk;
    my_soc_top #(.INIT_FILE(""), .CPU_BOOT_HOLD(1)) dut (
        .clk_i(clk), .rst_ni(rst_n), .tck_i(1'b0), .tms_i(1'b0), .td_i(1'b0), .td_o());
    dm::dmi_req_t request;
    logic request_valid=0, response_ready=0;
    logic irq16=0, irq17=0;
    logic [31:0] image[0:511];
    logic [31:0] value, ignored;

    task automatic dmi(input logic write_op, input logic [6:0] addr,
                       input logic [31:0] data, output logic [31:0] result);
        @(negedge clk);
        request.addr=addr; request.op=write_op ? dm::DTM_WRITE : dm::DTM_READ;
        request.data=data; request_valid=1; response_ready=0;
        do @(posedge clk); while (!dut.debug_req_ready);
        @(negedge clk); request_valid=0; response_ready=1;
        do @(posedge clk); while (!dut.debug_resp_valid);
        if (dut.debug_resp.resp !== 0) $fatal(1,"FAIL: DMI response");
        result=dut.debug_resp.data;
        @(negedge clk); response_ready=0;
    endtask
    task automatic wait_sba;
        integer polls;
        logic [31:0] sbcs;
        for (polls=0; polls<200; polls++) begin
            dmi(0,7'h38,0,sbcs);
            if (sbcs[22] || sbcs[14:12]) $fatal(1,"FAIL: SBA error %h",sbcs);
            if (!sbcs[21]) return;
        end
        $fatal(1,"TIMEOUT: SBA");
    endtask
    task automatic write_word(input logic [31:0] addr, data);
        dmi(1,7'h38,32'h00040000,ignored);
        dmi(1,7'h39,addr,ignored);
        dmi(1,7'h3c,data,ignored);
        wait_sba();
    endtask
    task automatic read_word(input logic [31:0] addr, output logic [31:0] data);
        dmi(1,7'h38,32'h00140000,ignored);
        dmi(1,7'h39,addr,ignored);
        wait_sba();
        dmi(0,7'h3c,0,data);
    endtask

    // Drop injected peripheral IRQs at acknowledgement, before retirq.
    always @(negedge clk) begin
        if (dut.i_cpu.core.eoi[16]) irq16=0;
        if (dut.i_cpu.core.eoi[17]) irq17=0;
    end
    initial begin
        force dut.debug_req = request;
        force dut.debug_req_valid = request_valid;
        force dut.debug_resp_ready = response_ready;
        force dut.npu_irq = irq16;
        force dut.dma_irq = irq17;
        for (int i=0;i<512;i++) image[i]=32'h00000013;
        $readmemh("hardware/soc/sim/sw/picorv32_isa_test.hex",image);
        repeat(8) @(negedge clk); rst_n=1;
        repeat(8) @(negedge clk);
        dmi(1,7'h10,1,ignored); // dmactive
        read_word(32'h00010038,value);
        if (value !== 1) $fatal(1,"FAIL: CPU must start held, status=%h",value);
        // Load code over exactly the Debug Module's existing system bus path.
        for (int i=0;i<512;i++) write_word(32'h80000000+4*i,image[i]);
        write_word(32'h80001700,0); write_word(32'h80001704,0);
        write_word(32'h800017e0,0);
        read_word(32'h80000000,value);
        if (value !== image[0]) $fatal(1,"FAIL: SBA SRAM readback");
        write_word(32'h0001003c,1);
        // Exercise both peripheral IRQ numbers through the actual CPU handler.
        irq16=1;
        wait(dut.i_mainmem.i_mainmem.memory['h5c1] === 1);
        irq17=1;
        wait(dut.i_mainmem.i_mainmem.memory['h5f8] === 32'hc0dec0de ||
             dut.i_mainmem.i_mainmem.memory['h5f8] === 32'hdeadbeef || dut.cpu_trap);
        if (dut.cpu_trap || dut.i_mainmem.i_mainmem.memory['h5f8] !== 32'hc0dec0de)
            $fatal(1,"FAIL: RV32IMC ISA/IRQ self-check");
        $display("PASS: RV32IMC arithmetic, SB/SH/LB/LBU/LH/LHU, compressed fetch, counters, IRQ16/17");
        write_word(32'h0001003c,0);
        read_word(32'h00010038,value);
        if (value !== 1 || dut.i_cpu.pending) $fatal(1,"FAIL: CPU stop must drain AXI");
        // Replace the program after stopping, and verify restart/trap status.
        write_word(32'h80000000,32'h00100073); // ebreak with IRQ1 masked
        write_word(32'h0001003c,1);
        wait(dut.cpu_trap);
        read_word(32'h00010038,value);
        if (!value[1]) $fatal(1,"FAIL: trap status missing");
        write_word(32'h0001003c,0);
        repeat(8) @(negedge clk);
        read_word(32'h00010038,value);
        if (value !== 1) $fatal(1,"FAIL: reset did not clear trap");
        // Invalid target reads and writes must retire the AXI response,
        // latch a bus fault, and remain recoverable through CPU reset.
        for (int write_access=0; write_access<2; write_access++) begin
            write_word(32'h80000000,32'h600002b7); // lui t0,0x60000 (unmapped)
            write_word(32'h80000004,write_access ? 32'h0002a023 : 32'h0002a303);
            write_word(32'h0001003c,1);
            wait(dut.cpu_trap);
            if (!dut.i_cpu.bus_error) $fatal(1,"FAIL: missing AXI fault status");
            write_word(32'h0001003c,0);
            repeat(8) @(negedge clk);
            read_word(32'h00010038,value);
            if (value !== 1) $fatal(1,"FAIL: AXI fault recovery");
        end
        $display("PASS: AXI read/write error responses latch faults and recover through CPU reset");
        $display("ALL PICORV32 SOC TESTS PASSED");
        $finish;
    end
    initial begin
        repeat(200000) @(posedge clk);
        $fatal(1,"TIMEOUT: PicoRV32 SoC status trap=%b PC=%h",dut.cpu_trap,dut.i_cpu.core.reg_pc);
    end
endmodule
