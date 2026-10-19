`timescale 1ns/1ps
// Gate-level/SDF regression for the CPU-less (no_cpu) post-route netlist.
// Drives ONLY the public JTAG pins: loads a model + one frame, runs one
// inference, reads the four logits and compares against golden.
module tb_nocpu_postlayout;
    reg clk=0, rst_n=0, tck=1, tms=1, tdi=0;
    wire tdo, irq;
    always #10 clk=~clk;   // 50 MHz core clock (20 ns period)

    my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));

    integer ticks=0, i, j, k, polls;
    reg bitout;
    reg [71:0] packet;
    reg [31:0] value;
    reg [7:0] inp[0:319], shifts[0:31];
    reg [63:0] weights[0:255];
    reg [31:0] biases[0:31], mult[0:31], gold[0:3];
    string dir;

    // One TCK half-cycle with a non-harmonic TCK/system-clock relationship.
    task tick(input reg m, input reg d, output reg q);
        begin
            tms=m; tdi=d; #3; tck=0; #9; q=tdo; tck=1; #4; ticks=ticks+1;
        end
    endtask
    task tap_reset;
        integer n;
        begin for(n=0;n<6;n=n+1)tick(1,0,bitout); tick(0,0,bitout); end
    endtask
    task select_ir(input [4:0] instruction);
        integer n;
        begin
            tick(1,0,bitout); tick(1,0,bitout); tick(0,0,bitout); tick(0,0,bitout);
            for(n=0;n<5;n=n+1)tick(n==4,instruction[n],bitout);
            tick(1,0,bitout); tick(0,0,bitout);
        end
    endtask
    task scan(input [71:0] request, input integer bits, output reg [71:0] response);
        integer n;
        reg q;
        begin
            response=0; tick(1,0,bitout); tick(0,0,bitout); tick(0,0,bitout);
            for(n=0;n<bits;n=n+1) begin tick(n==bits-1,request[n],q); response[n]=q; end
            tick(1,0,bitout); tick(0,0,bitout);
        end
    endtask
    // 72-bit exchange: {opcode[7:0], byte_address[31:0], data[31:0]} LSB first.
    task command(input [7:0] op, input [31:0] addr, input [31:0] data,
                 input [4:0] expected, output reg [31:0] result);
        reg [71:0] r;
        integer count;
        begin
            scan({op,addr,data},72,r); count=0;
            r=0;
            while(!r[71] || r[70]) begin
                scan(72'd0,72,r); count=count+1;
                if(count>100) $fatal(1,"JTAG response timeout op %h",op);
            end
            if(r[69] || r[68:64]!==expected || r[63:32]!==addr)
                $fatal(1,"JTAG response %h for op=%h addr=%h",r,op,addr);
            result=r[31:0];
        end
    endtask
    task c(input [7:0] op, input [31:0] addr, input [31:0] data);
        reg [31:0] ignored;
        begin command(op,addr,data,0,ignored); end
    endtask

    initial begin
        if(!$value$plusargs("DATA_DIR=%s",dir)) $fatal(1,"DATA_DIR required");
        $readmemh({dir,"/input.hex"},inp);
        $readmemh({dir,"/weights.hex"},weights);
        $readmemh({dir,"/bias.hex"},biases);
        $readmemh({dir,"/multiplier.hex"},mult);
        $readmemh({dir,"/shift.hex"},shifts);
        $readmemh({dir,"/golden_fc.hex"},gold,0,3);
        $sdf_annotate("my_soc_top_tt_io.sdf", dut, , "postlayout_sdf.log", "MAXIMUM");

        // Asynchronous reset, then JTAG bring-up.
        #1; rst_n=1; #1; rst_n=0; #49; rst_n=1;
        tap_reset;
        scan(0,32,packet);
        if(packet[31:0]!==32'h1) $fatal(1,"IDCODE %h",packet);
        select_ir('h12);

        // Verify controller ABI version.
        command(1,0,0,0,value);
        if(value!==32'h10000) $fatal(1,"version %h",value);

        // Load model.
        c(10,0,0);              // CLEAR_ERROR
        c(3,0,0);               // MODEL_BEGIN
        for(i=0;i<241;i=i+1) begin
            c(2,'h2000+i*8, weights[i][31:0]);
            c(2,'h2004+i*8, weights[i][63:32]);
        end
        for(j=0;j<4;j=j+1) if(j!=2) begin
            for(k=0;k<8;k=k+1) begin
                c(2,'h4000+j*256+k*4, biases[j*8+k]);
                if(j!=3) begin
                    c(2,'h4020+j*256+k*4, mult[j*8+k]);
                    c(2,'h4040+j*256+k*4, {{24{shifts[j*8+k][7]}},shifts[j*8+k]});
                end
            end
            c(4,0,j);           // PARAM_COMMIT layer j
        end
        c(5,0,0);               // MODEL_COMMIT

        // One inference.
        c(6,0,0);               // INPUT_BEGIN
        for(i=0;i<80;i=i+1)
            c(2,'h1000+i*4, {inp[i*4+3],inp[i*4+2],inp[i*4+1],inp[i*4]});
        c(7,0,0);               // INPUT_COMMIT
        c(8,0,0);               // START

        polls=0; value=0;
        while(!value[3]) begin
            command(1,4,0,0,value); polls=polls+1;
            if(value[7] || polls>200) $fatal(1,"JTAG job status %h",value);
        end
        if(!irq) $fatal(1,"JTAG completion notification missing");

        for(i=0;i<4;i=i+1) begin
            command(1,'h100+i*4,0,0,value);
            if(value!==gold[i]) $fatal(1,"JTAG logit %0d got %h expected %h",i,value,gold[i]);
        end
        c(9,0,0);               // ACK

        $display("POSTLAYOUT_PASS: no_cpu JTAG inference OK TCK_edges=%0d", ticks);
        $finish;
    end

    initial begin #200_000_000; $fatal(1,"global JTAG timeout"); end
endmodule
