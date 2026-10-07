`timescale 1ns/1ps
module tb_fc_engine;
    parameter ARRAY_ROWS=4,ARRAY_COLS=8,OUTPUT_CHANNELS=6;
    localparam IC=8,OC=OUTPUT_CHANNELS,TILES=(OC+ARRAY_COLS-1)/ARRAY_COLS;
    localparam [7:0] OC_VALUE=OC;
    reg clk=0,rst=1,start_valid=0,output_write_ready=1;
    reg [7:0] cfg_input_channels=8;
    always #5 clk=~clk;
    wire start_ready,busy,done;
    wire [15:0] input_read_addr,weight_read_addr,bias_tile_addr,output_write_addr;
    reg [7:0] inputs[0:IC-1];
    reg [ARRAY_COLS*8-1:0] weights[0:IC*TILES-1];
    reg [ARRAY_COLS*32-1:0] biases[0:TILES-1];
    wire output_write_valid;
    wire [ARRAY_COLS*32-1:0] output_write_data;
    wire [ARRAY_COLS-1:0] output_write_mask;
    integer expected[0:OC-1];
    integer i,o,tile,lane,sum,writes=0,stalls=0,cycles=0;

    fc_engine #(.ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .input_channels(cfg_input_channels),.output_channels(OC_VALUE),.input_base(16'd0),
        .weight_base(16'd0),.output_base(16'd0),.busy(busy),.done(done),
        .input_read_addr(input_read_addr),.input_read_data(inputs[input_read_addr]),
        .weight_read_addr(weight_read_addr),.weight_read_data(weights[weight_read_addr]),
        .bias_tile_addr(bias_tile_addr),.bias_read_data(biases[bias_tile_addr]),
        .output_write_valid(output_write_valid),
        .output_write_ready(output_write_ready),.output_write_addr(output_write_addr),
        .output_write_data(output_write_data),.output_write_mask(output_write_mask));

    always @(posedge clk)begin
        cycles=cycles+1;if(cycles>10000)$fatal(1,"FC timeout");
        if(!rst&&output_write_valid&&!output_write_ready)stalls=stalls+1;
        if(!rst&&output_write_valid&&output_write_ready)begin
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                if(output_write_mask[lane])begin
                    o=output_write_addr+lane;
                    if($signed(output_write_data[lane*32 +: 32])!==expected[o])
                        $fatal(1,"output=%0d expected=%0d actual=%0d",o,expected[o],
                               $signed(output_write_data[lane*32 +: 32]));
                end
            writes=writes+1;
        end
    end

    initial begin
        for(i=0;i<IC;i=i+1)inputs[i]=i-4;
        for(tile=0;tile<TILES;tile=tile+1)begin
            weights[tile]=0;biases[tile]=0;
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                biases[tile][lane*32 +: 32]=tile*7+lane-3;
        end
        for(i=0;i<IC;i=i+1)
            for(tile=0;tile<TILES;tile=tile+1)begin
                weights[i*TILES+tile]=0;
                for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                    weights[i*TILES+tile][lane*8 +: 8]=((i*5+tile*3+lane)%7)-3;
            end
        for(o=0;o<OC;o=o+1)begin
            tile=o/ARRAY_COLS;lane=o%ARRAY_COLS;
            sum=tile*7+lane-3;
            for(i=0;i<IC;i=i+1)
                sum=sum+$signed(inputs[i])*$signed(weights[i*TILES+tile][lane*8 +: 8]);
            expected[o]=sum;
        end
        repeat(3)@(posedge clk);
        @(negedge clk);rst=0;cfg_input_channels=0;start_valid=1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid FC descriptor was accepted");
        end
        @(negedge clk);start_valid=0;cfg_input_channels=8;
        @(negedge clk);start_valid=1;
        @(posedge clk);while(!start_ready)@(posedge clk);
        @(negedge clk);start_valid=0;output_write_ready=0;
        repeat(4)@(posedge clk);
        @(negedge clk);output_write_ready=1;
        while(!done)begin
            @(negedge clk);output_write_ready=($urandom_range(0,3)!=0);
            @(posedge clk);
        end
        @(negedge clk);output_write_ready=1;
        if(writes!=TILES)$fatal(1,"write count mismatch");
        if(stalls==0)$fatal(1,"missing stalls");
        $display("ALL FC ENGINE TESTS PASSED outputs=%0d rows=%0d cols=%0d writes=%0d cycles=%0d stalls=%0d",
                 OC,ARRAY_ROWS,ARRAY_COLS,writes,cycles,stalls);$finish;
    end
endmodule
