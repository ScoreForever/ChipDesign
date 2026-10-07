`timescale 1ns/1ps
module tb_global_sum_pool_engine;
    parameter LANES=8;
    localparam H=5,W=4,C=8;
    reg clk=0,rst=1,start_valid=0,output_write_ready=1;
    reg [7:0] cfg_channels=8;
    always #5 clk=~clk;
    wire start_ready,busy,done;
    wire [15:0] activation_read_addr,output_write_addr;
    reg [7:0] activation_mem[0:H*W*C-1];
    reg [LANES*8-1:0] activation_read_data;
    wire output_write_valid;
    wire [LANES*32-1:0] output_write_data;
    wire [LANES-1:0] output_write_mask;
    integer expected[0:C-1];
    integer lane,index,c,writes=0,stalls=0,cycles=0;

    always @* begin
        activation_read_data=0;
        for(lane=0;lane<LANES;lane=lane+1)
            if(activation_read_addr+lane<H*W*C)
                activation_read_data[lane*8 +: 8]=activation_mem[activation_read_addr+lane];
    end

    global_sum_pool_engine #(.LANES(LANES)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .input_height(8'd5),.input_width(8'd4),.channels(cfg_channels),
        .input_base(16'd0),.output_base(16'd0),.busy(busy),.done(done),
        .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .output_write_valid(output_write_valid),
        .output_write_ready(output_write_ready),.output_write_addr(output_write_addr),
        .output_write_data(output_write_data),.output_write_mask(output_write_mask)
    );

    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>10000)$fatal(1,"GAP timeout");
        if(!rst && output_write_valid && !output_write_ready)stalls=stalls+1;
        if(!rst && output_write_valid && output_write_ready)begin
            for(lane=0;lane<LANES;lane=lane+1)
                if(output_write_mask[lane])begin
                    c=output_write_addr+lane;
                    if($signed(output_write_data[lane*32 +: 32])!==expected[c])
                        $fatal(1,"channel=%0d expected=%0d actual=%0d",c,expected[c],
                               $signed(output_write_data[lane*32 +: 32]));
                end
            writes=writes+1;
        end
    end

    initial begin
        for(c=0;c<C;c=c+1)expected[c]=0;
        for(index=0;index<H*W*C;index=index+1)begin
            activation_mem[index]=((index*43+11)%256)-128;
            expected[index%C]=expected[index%C]+$signed(activation_mem[index]);
        end
        repeat(3)@(posedge clk);
        @(negedge clk);rst=0;cfg_channels=0;start_valid=1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid sum-pool descriptor was accepted");
        end
        @(negedge clk);start_valid=0;cfg_channels=8;
        @(negedge clk);start_valid=1;
        @(posedge clk);while(!start_ready)@(posedge clk);
        @(negedge clk);start_valid=0;
        while(!done)begin
            @(negedge clk);output_write_ready=($urandom_range(0,3)!=0);
            @(posedge clk);
        end
        @(negedge clk);output_write_ready=1;
        if(writes!=(C+LANES-1)/LANES)$fatal(1,"write count mismatch");
        if(stalls==0)$fatal(1,"missing stalls");
        $display("ALL GLOBAL SUM POOL TESTS PASSED lanes=%0d writes=%0d cycles=%0d stalls=%0d",
                 LANES,writes,cycles,stalls);
        $finish;
    end
endmodule
