`timescale 1ns/1ps
module tb_nocpu_controller;
    reg clk=0, rst_n=0;
    always #5 clk=~clk;
    reg cv=0, rr=0;
    wire cr, rv, irq;
    reg [7:0] cop=0;
    reg [31:0] ca=0, cd=0;
    wire [4:0] re;
    wire [31:0] rd;
    wire ev, ew, er;
    wire [31:0] ea, ed;
    reg ext_ready=1, ext_valid=0, ext_stall=0;
    reg [31:0] ext_data=0;
    integer ext_count=0, ext_transactions=0;
    npu_control_subsystem #(.ENABLE_EXTENSION(1)) dut(
        .clk_i(clk),.rst_ni(rst_n),.cmd_valid_i(cv),.cmd_ready_o(cr),
        .cmd_op_i(cop),.cmd_addr_i(ca),.cmd_data_i(cd),
        .rsp_valid_o(rv),.rsp_ready_i(rr),.rsp_error_o(re),.rsp_data_o(rd),.irq_o(irq),
        .ext_valid_o(ev),.ext_ready_i(ext_ready),.ext_write_o(ew),.ext_addr_o(ea),.ext_data_o(ed),
        .ext_rsp_valid_i(ext_valid),.ext_rsp_ready_o(er),.ext_rsp_data_i(ext_data),.ext_rsp_error_i(5'd0));
    always @(posedge clk) begin
        if (!rst_n) begin ext_count<=0; ext_valid<=0; end
        else begin
            if(ev && ext_ready) begin
                ext_count<=5; ext_data<=ea^ed^{31'd0,ew}; ext_transactions<=ext_transactions+1;
            end
            if(ext_count>0 && !ext_stall) begin
                ext_count<=ext_count-1;
                if(ext_count==1) ext_valid<=1;
            end
            if(ext_valid && er)ext_valid<=0;
            if(dut.core_reset)begin ext_count<=0;ext_valid<=0;end
        end
    end
    reg [7:0] inp[0:319], shifts[0:31], p1[0:639], p2[0:159];
    reg [63:0] weights[0:255];
    reg [31:0] biases[0:31], mult[0:31], gold[0:7];
    string dir,dir2;
    integer classes=4, i,j,k,cycles, commands=0,checks=0;
    reg [31:0] value, saved;
    reg [4:0] saved_error;

    task command(input [7:0] op,input [31:0] addr,input [31:0] data,input [4:0] expected,
                 output [31:0] result);
        integer wait_cycles,hold;
        begin
            @(negedge clk);cv=1;cop=op;ca=addr;cd=data;rr=0;
            wait_cycles=0;
            while(!cr)begin @(negedge clk);wait_cycles=wait_cycles+1;
                if(wait_cycles>1000)$fatal(1,"command ready timeout");end
            @(negedge clk);cv=0;
            while(!rv)begin @(negedge clk);wait_cycles=wait_cycles+1;
                if(wait_cycles>110000)$fatal(1,"response timeout op=%h addr=%h",op,addr);end
            saved=rd;saved_error=re;
            // Deliberately hold every response: no re-execution, no data changes.
            for(hold=0;hold<(commands%7)+1;hold=hold+1)begin
                @(negedge clk);
                if(!rv || rd!==saved || re!==saved_error || cr)$fatal(1,"response backpressure violation");
            end
            if(re!==expected)$fatal(1,"op=%h addr=%h error=%0d expected=%0d",op,addr,re,expected);
            result=rd;rr=1;@(negedge clk);rr=0;commands=commands+1;
        end
    endtask
    task c(input [7:0] op,input [31:0] addr,input [31:0] data,input [4:0] error);
        reg [31:0] ignored;
        begin command(op,addr,data,error,ignored);end
    endtask
    task expect_read(input [31:0] addr,input [31:0] expected);
        reg [31:0] actual;
        begin command(1,addr,0,0,actual);checks=checks+1;
            if(actual!==expected)$fatal(1,"read %h got %h expected %h",addr,actual,expected);end
    endtask
    task vectors(input string path);
        begin
            $readmemh({path,"/input.hex"},inp);$readmemh({path,"/weights.hex"},weights);
            $readmemh({path,"/bias.hex"},biases);$readmemh({path,"/multiplier.hex"},mult);
            $readmemh({path,"/shift.hex"},shifts);$readmemh({path,"/golden_fc.hex"},gold,0,classes-1);
            $readmemh({path,"/golden_pool1.hex"},p1);$readmemh({path,"/golden_pool2.hex"},p2);
        end
    endtask
    task load_model;
        begin
            c(3,0,0,0);c(2,'h10,classes,0);
            c(5,0,0,7);c(2,'h2004,0,6); // incomplete and out-of-order
            for(i=0;i<241;i=i+1)begin c(2,'h2000+i*8,weights[i][31:0],0);c(2,'h2004+i*8,weights[i][63:32],0);end
            for(j=0;j<4;j=j+1)if(j!=2)begin
                c(4,0,j,7);
                for(k=0;k<8;k=k+1)begin
                    c(2,'h4000+j*256+k*4,biases[j*8+k],0);
                    if(j!=3)begin
                        c(2,'h4020+j*256+k*4,mult[j*8+k],0);
                        c(2,'h4040+j*256+k*4,{{24{shifts[j*8+k][7]}},shifts[j*8+k]},0);
                    end
                end
                if(j==0)begin
                    c(2,'h4100,0,6);c(4,0,1,6); // do not mix parameter groups
                end
                c(4,0,j,0);
                c(4,0,j,5);
            end
            c(5,0,0,0);expect_read('h28,482);expect_read('h38,11);
            // Read back actual committed NPU storage, not the staging registers.
            for(i=0;i<241;i=i+1)begin expect_read('h2000+i*8,weights[i][31:0]);expect_read('h2004+i*8,weights[i][63:32]);end
            for(j=0;j<4;j=j+1)if(j!=2)for(k=0;k<8;k=k+1)begin
                expect_read('h4000+j*256+k*4,biases[j*8+k]);
                if(j!=3)begin expect_read('h4020+j*256+k*4,mult[j*8+k]);
                    expect_read('h4040+j*256+k*4,{{24{shifts[j*8+k][7]}},shifts[j*8+k]});end
            end
        end
    endtask
    task load_input;
        begin
            c(6,0,0,0);c(7,0,0,7);c(2,'h1004,0,6);
            for(i=0;i<80;i=i+1)c(2,'h1000+i*4,{inp[i*4+3],inp[i*4+2],inp[i*4+1],inp[i*4]},0);
            c(7,0,0,0);expect_read('h2c,80);
            for(i=0;i<80;i=i+1)expect_read('h3000+i*4,{inp[i*4+3],inp[i*4+2],inp[i*4+1],inp[i*4]});
        end
    endtask
    task finish_job;
        begin
            cycles=0;
            while(!irq)begin @(negedge clk);cycles=cycles+1;if(cycles>110000)$fatal(1,"job timeout");end
            command(1,4,0,0,value);
            if(!value[3] || value[7])$fatal(1,"completion status %h",value);
            for(i=0;i<8;i=i+1)begin expect_read('h100+i*4,i<classes?gold[i]:0);expect_read('h5000+i*4,i<classes?gold[i]:0);end
            for(i=0;i<160;i=i+4)expect_read('h3000+i,{p2[i+3],p2[i+2],p2[i+1],p2[i]});
            for(i=0;i<640;i=i+4)expect_read('h3140+i,{p1[i+3],p1[i+2],p1[i+1],p1[i]});
            command(1,'h20,0,0,value);if(value<1000 || value>10000)$fatal(1,"bad performance count %0d",value);
            c(8,0,0,9);c(6,0,0,9);c(3,0,0,9); // result owns the snapshot until ACK
            c(9,0,0,0);if(irq)$fatal(1,"ACK did not clear IRQ");
            c(8,0,0,5); // consumed input cannot be re-used (NPU overwrites it)
        end
    endtask
    initial begin
        if(!$value$plusargs("DATA_DIR=%s",dir))$fatal(1,"DATA_DIR required");
        if(!$value$plusargs("DATA_DIR2=%s",dir2))dir2=dir;
        if($value$plusargs("CLASSES=%d",classes))begin end
        vectors(dir);repeat(5)@(negedge clk);rst_n=1;repeat(4)@(negedge clk);
        expect_read(0,'h10000);c(8,0,0,5);c(6,0,0,5);
        c(255,0,0,1);expect_read(8,5);c(10,0,0,0);expect_read(8,0);
        c(1,1,0,3);c(1,'hffff0000,0,2);c(2,'h14,0,8);
        c(3,1,0,8);c(3,0,0,0);c(2,'h10,9,8);
        c(2,'h4040,-32,8);c(2,'h4040,32,8);c(2,'h4020,'hffffffff,8);
        c(2,'h4200,0,2);c(4,0,2,8);
        load_model;load_input;
        force dut.core_ready=0;c(8,0,0,12);release dut.core_ready;
        c(8,0,0,0);c(8,0,0,4);c(2,'h2000,0,4);c(1,'h2000,0,4);
        c(6,0,0,4);c(9,0,0,4);finish_job;
        // Reuse the model across a second full frame.
        load_input;c(8,0,0,0);finish_job;
        // Enabled extension: request held through endpoint backpressure, exactly once.
        ext_ready=0;
        fork
            begin repeat(20)@(negedge clk);ext_ready=1;end
            begin command(32,'h100,32'h12345678,0,value);
                if(value!==32'h12345779)$fatal(1,"extension response");end
        join
        if(ext_transactions!=1)$fatal(1,"extension duplicated");
        c(2,'h14,20,0);ext_stall=1;c(33,0,0,10);ext_stall=0;
        expect_read(8,10);c(11,0,0,0);expect_read(4,0);expect_read(8,0);
        // Model update changes the actual weights and expected output.
        vectors(dir2);load_model;load_input;
        c(2,'h14,10,0);c(10,0,0,0);c(8,0,0,0);
        repeat(20)@(negedge clk);expect_read(8,10);
        command(1,4,0,0,value);if(!value[7] || value[0] || value[3])$fatal(1,"timeout status");
        if(!irq)$fatal(1,"timeout notification missing");
        c(10,0,0,0);c(8,0,0,5);c(11,0,0,0);
        load_model;load_input;c(8,0,0,0);
        c(11,0,0,0);repeat(20)@(negedge clk);expect_read(4,0); // abort in flight
        load_model;load_input;c(8,0,0,0);finish_job;
        // A replacement invalidates model readiness before any new weight write.
        c(3,0,0,0);c(8,0,0,5);c(11,0,0,0);
        // A synchronous inspection that never returns must also time out,
        // release command ownership and permit a clean soft reset.
        c(2,'h14,6,0);force dut.debug_valid=0;
        c(1,'h2000,0,10);release dut.debug_valid;
        expect_read(8,10);c(11,0,0,0);expect_read(4,0);
        $display("PASS nocpu controller classes=%0d commands=%0d readback_checks=%0d",classes,commands,checks);
        $finish;
    end
    initial begin #100000000;$fatal(1,"global timeout");end
endmodule
