`timescale 1ns/1ps
module tb_global_avg_pool_engine;
    parameter LANES=8;
    localparam H=5,W=4,C=8,SHIFT_WIDTH=6;
    reg clk=0,rst=1,start_valid=0,output_write_ready=1;
    reg [7:0] cfg_input_width=4;
    always #5 clk=~clk;
    wire start_ready,busy,done;
    wire [15:0] activation_read_addr,parameter_tile_addr,output_write_addr;
    reg [7:0] activation_mem[0:H*W*C-1];
    reg [LANES*8-1:0] activation_read_data;
    reg [LANES*32-1:0] multiplier_mem[0:(C+LANES-1)/LANES-1];
    reg [LANES*SHIFT_WIDTH-1:0] shift_mem[0:(C+LANES-1)/LANES-1];
    wire output_write_valid;
    wire [LANES*8-1:0] output_write_data;
    wire [LANES-1:0] output_write_mask;
    integer lane,index,c,writes=0,stalls=0,cycles=0,expected;

    always @* begin
        activation_read_data=0;
        for(lane=0;lane<LANES;lane=lane+1)
            if(activation_read_addr+lane<H*W*C)
                activation_read_data[lane*8 +: 8]=activation_mem[activation_read_addr+lane];
    end

    global_avg_pool_engine #(.LANES(LANES)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .input_height(8'd5),.input_width(cfg_input_width),.channels(8'd8),
        .input_base(16'd0),.output_base(16'd0),.output_offset(32'sd0),
        .activation_min(8'sh80),.activation_max(8'sh7f),.busy(busy),.done(done),
        .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .parameter_tile_addr(parameter_tile_addr),
        .multiplier_read_data(multiplier_mem[parameter_tile_addr]),
        .shift_read_data(shift_mem[parameter_tile_addr]),
        .output_write_valid(output_write_valid),
        .output_write_ready(output_write_ready),.output_write_addr(output_write_addr),
        .output_write_data(output_write_data),.output_write_mask(output_write_mask));

    always @(posedge clk) begin
        cycles=cycles+1;if(cycles>10000)$fatal(1,"avg pool timeout");
        if(!rst && output_write_valid && !output_write_ready)stalls=stalls+1;
        if(!rst && output_write_valid && output_write_ready)begin
            for(lane=0;lane<LANES;lane=lane+1)
                if(output_write_mask[lane])begin
                    c=output_write_addr+lane;expected=c-4;
                    if($signed(output_write_data[lane*8 +: 8])!==expected)
                        $fatal(1,"channel=%0d expected=%0d actual=%0d",c,expected,
                               $signed(output_write_data[lane*8 +: 8]));
                end
            writes=writes+1;
        end
    end

    initial begin
        for(index=0;index<H*W*C;index=index+1)
            activation_mem[index]=(index%C)-4;
        for(index=0;index<(C+LANES-1)/LANES;index=index+1)begin
            multiplier_mem[index]=0;shift_mem[index]=0;
            for(lane=0;lane<LANES;lane=lane+1)begin
                multiplier_mem[index][lane*32 +: 32]=32'sh66666666;
                shift_mem[index][lane*SHIFT_WIDTH +: SHIFT_WIDTH]=-4;
            end
        end
        repeat(3)@(posedge clk);
        @(negedge clk);rst=0;cfg_input_width=0;start_valid=1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid avg-pool descriptor was accepted");
        end
        @(negedge clk);start_valid=0;cfg_input_width=4;
        @(negedge clk);start_valid=1;output_write_ready=0;
        @(posedge clk);while(!start_ready)@(posedge clk);
        @(negedge clk);start_valid=0;
        while(!output_write_valid)@(posedge clk);
        repeat(3)@(posedge clk);
        @(negedge clk);output_write_ready=1;
        while(!done)@(posedge clk);
        @(negedge clk);output_write_ready=1;
        if(writes!=(C+LANES-1)/LANES)$fatal(1,"write count mismatch");
        if(stalls==0)$fatal(1,"missing stalls");
        $display("ALL GLOBAL AVG POOL TESTS PASSED lanes=%0d writes=%0d cycles=%0d stalls=%0d",
                 LANES,writes,cycles,stalls);$finish;
    end
endmodule
