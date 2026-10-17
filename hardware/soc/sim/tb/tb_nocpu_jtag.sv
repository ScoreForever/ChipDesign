`timescale 1ns/1ps
// End-to-end test uses ONLY public JTAG pins for model/input/result traffic.
module tb_nocpu_jtag;
    reg clk=0,rst_n=0,tck=1,tms=1,tdi=0;
    wire tdo,irq;
    always #5 clk=~clk;
    my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));
    integer ticks=0,commands=0,i,j,k,polls;
    reg bitout;
    reg [71:0] packet;
    reg [31:0] value;
    reg [7:0] inp[0:319], shifts[0:31];
    reg [63:0] weights[0:255];
    reg [31:0] biases[0:31], mult[0:31],gold[0:3];
    string dir;
    task tick(input reg m,input reg d,output reg q);
        begin
            tms=m;tdi=d;#3;tck=0;
            // Deliberately non-harmonic, changing TCK/system-clock relationship.
            #(ticks%3==0?13:9);q=tdo;tck=1;#4;
            if($test$plusargs("TRACE") && ticks<50)$display("tick=%0d tap=%h ir=%h dr=%h q=%b",ticks,dut.i_host.tap,dut.i_host.ir,dut.i_host.dr,q);
            ticks=ticks+1;
        end
    endtask
    task tap_reset;
        integer n;
        begin for(n=0;n<6;n=n+1)tick(1,0,bitout);tick(0,0,bitout);end
    endtask
    task select_ir(input [4:0] instruction);
        integer n;
        begin
            tick(1,0,bitout);tick(1,0,bitout);tick(0,0,bitout);tick(0,0,bitout);
            for(n=0;n<5;n=n+1)tick(n==4,instruction[n],bitout);
            tick(1,0,bitout);tick(0,0,bitout);
        end
    endtask
    task scan(input [71:0] request,input integer bits,output reg [71:0] response);
        integer n;
        reg q;
        begin
            response=0;tick(1,0,bitout);tick(0,0,bitout);tick(0,0,bitout);
            for(n=0;n<bits;n=n+1)begin tick(n==bits-1,request[n],q);response[n]=q;end
            tick(1,0,bitout);tick(0,0,bitout);
        end
    endtask
    task command(input [7:0] op,input [31:0] addr,input [31:0] data,
                 input [4:0] expected,output reg [31:0] result);
        reg [71:0] r;
        integer count;
        begin
            scan({op,addr,data},72,r);count=0;
            r=0;
            while(!r[71] || r[70])begin
                scan(72'd0,72,r);count=count+1;
                if(count>100)$fatal(1,"JTAG response timeout op %h",op);
            end
            if(r[69] || r[68:64]!==expected || r[63:32]!==addr)
                $fatal(1,"JTAG response %h for op=%h addr=%h error_expected=%0d",r,op,addr,expected);
            result=r[31:0];commands=commands+1;
        end
    endtask
    task c(input [7:0] op,input [31:0] addr,input [31:0] data);
        reg [31:0] ignored;
        begin command(op,addr,data,0,ignored);end
    endtask
    initial begin
        if(!$value$plusargs("DATA_DIR=%s",dir))$fatal(1,"DATA_DIR required");
        $readmemh({dir,"/input.hex"},inp);$readmemh({dir,"/weights.hex"},weights);
        $readmemh({dir,"/bias.hex"},biases);$readmemh({dir,"/multiplier.hex"},mult);
        $readmemh({dir,"/shift.hex"},shifts);$readmemh({dir,"/golden_fc.hex"},gold,0,3);
        // Exercise asynchronous reset even when TCK is completely stopped.
        #1;rst_n=1;#1;rst_n=0;#49;rst_n=1;tap_reset;
        scan(0,32,packet);if(packet[31:0]!==1)$fatal(1,"IDCODE %h",packet);
        select_ir('h12);
        c(1,0,0);command(32,0,0,11,value);command(1,3,0,3,value);
        // Original request must survive overrun and a TMS reset while stalled.
        force dut.cmd_ready=0;
        scan({8'd1,32'd0,32'd0},72,packet);
        scan({8'd1,32'hffff0000,32'd0},72,packet);
        scan(0,72,packet);if(!packet[70] || !packet[69])$fatal(1,"missing overrun/busy");
        tap_reset;select_ir('h12);release dut.cmd_ready;
        #251; // TCK stopped while system accepts and completes the old request
        packet=0;polls=0;
        while(!packet[71] || packet[70])begin
            scan(0,72,packet);polls=polls+1;
            if(polls>10)$fatal(1,"mailbox did not finish after TCK resumed");
        end
        if(!packet[71] || packet[70] || packet[31:0]!==32'h10000 || packet[63:32]!==0)
            $fatal(1,"mailbox/TMS reset lost original response %h",packet);
        c(10,0,0);c(3,0,0);
        for(i=0;i<241;i=i+1)begin c(2,'h2000+i*8,weights[i][31:0]);c(2,'h2004+i*8,weights[i][63:32]);end
        for(j=0;j<4;j=j+1)if(j!=2)begin
            for(k=0;k<8;k=k+1)begin
                c(2,'h4000+j*256+k*4,biases[j*8+k]);
                if(j!=3)begin
                    c(2,'h4020+j*256+k*4,mult[j*8+k]);
                    c(2,'h4040+j*256+k*4,{{24{shifts[j*8+k][7]}},shifts[j*8+k]});
                end
            end
            c(4,0,j);
        end
        c(5,0,0);
        for(j=0;j<2;j=j+1)begin
            c(6,0,0);
            for(i=0;i<80;i=i+1)c(2,'h1000+i*4,{inp[i*4+3],inp[i*4+2],inp[i*4+1],inp[i*4]});
            c(7,0,0);c(8,0,0);
            if(j==0)begin
                // No TCK needed to execute an inference or preserve its completion.
                #100000;
            end
            polls=0;value=0;
            while(!value[3])begin
                command(1,4,0,0,value);polls=polls+1;
                if(value[7] || polls>100)$fatal(1,"JTAG job status %h",value);
            end
            if(!irq)$fatal(1,"JTAG completion notification missing");
            for(i=0;i<4;i=i+1)begin
                command(1,'h100+i*4,0,0,value);
                if(value!==gold[i])$fatal(1,"JTAG logit %0d got %h expected %h",i,value,gold[i]);
            end
            command(1,'h2000,0,0,value);if(value!==weights[0][31:0])$fatal(1,"JTAG weight readback");
            command(1,'h4000,0,0,value);if(value!==biases[0])$fatal(1,"JTAG parameter readback");
            command(1,'h3000,0,0,value);
            c(9,0,0);
        end
        c(11,0,0);command(1,4,0,0,value);if(value!==0)$fatal(1,"JTAG soft reset");
        // Physical reset resets both CDC toggles; no phantom request on restart.
        rst_n=0;#51;rst_n=1;tap_reset;select_ir('h12);
        command(1,0,0,0,value);if(value!==32'h10000)$fatal(1,"JTAG physical reset");
        $display("PASS nocpu JTAG commands=%0d TCK_edges=%0d nonzero_jobs=2",commands,ticks);
        $finish;
    end
    initial begin #100000000;$fatal(1,"global JTAG timeout");end
endmodule
