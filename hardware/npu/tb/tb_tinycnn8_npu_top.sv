`timescale 1ns/1ps
module tb_tinycnn8_npu_top;
    parameter ARRAY_ROWS=4,ARRAY_COLS=8;
    parameter OPT_GATHER_LOAD=0,OPT_SPATIAL_TILE=0,SPATIAL_TILE=16;
    localparam SHIFT_WIDTH=6,TILES=(8+ARRAY_COLS-1)/ARRAY_COLS;
    localparam CONV2_BASE=9*TILES,FC_BASE=CONV2_BASE+72*TILES;
    localparam WEIGHT_WORDS=FC_BASE+160*TILES;
    reg clk=0,rst=1,start_valid=0;
    reg [3:0] class_count=6;
    always #5 clk=~clk;
    wire start_ready,busy,done;
    wire [255:0] logits;
    reg host_activation_we=0,host_weight_we=0,host_parameter_we=0;
    reg [15:0] host_activation_addr=0,host_weight_addr=0;
    reg [7:0] host_activation_data=0,host_parameter_tile=0;
    reg [1:0] host_parameter_layer=0;
    reg [ARRAY_COLS*8-1:0] host_weight_data=0;
    reg [ARRAY_COLS*32-1:0] host_bias_data=0,host_multiplier_data=0;
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] host_shift_data=0;
    integer k,tile,lane,oc,ic,index,o,expected,cycles=0;

    tinycnn8_npu_top #(.ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS),
        .WEIGHT_WORDS(WEIGHT_WORDS),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD),.OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
        .SPATIAL_TILE(SPATIAL_TILE)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .class_count(class_count),.busy(busy),.done(done),.logits(logits),
        .host_activation_we(host_activation_we),.host_activation_addr(host_activation_addr),
        .host_activation_data(host_activation_data),.host_weight_we(host_weight_we),
        .host_weight_addr(host_weight_addr),.host_weight_data(host_weight_data),
        .host_parameter_we(host_parameter_we),.host_parameter_layer(host_parameter_layer),
        .host_parameter_tile(host_parameter_tile),.host_bias_data(host_bias_data),
        .host_multiplier_data(host_multiplier_data),.host_shift_data(host_shift_data));

    task write_activation;
        input integer addr,value;
        begin @(negedge clk);host_activation_we=1;host_activation_addr=addr;
            host_activation_data=value;@(posedge clk);@(negedge clk);host_activation_we=0;end
    endtask
    task write_weight;
        input integer addr;
        input [ARRAY_COLS*8-1:0] data;
        begin @(negedge clk);host_weight_we=1;host_weight_addr=addr;host_weight_data=data;
            @(posedge clk);@(negedge clk);host_weight_we=0;end
    endtask
    task write_parameter;
        input [1:0] layer;
        input integer tile_addr;
        input [ARRAY_COLS*32-1:0] bias,mult;
        input [ARRAY_COLS*SHIFT_WIDTH-1:0] shift;
        begin @(negedge clk);host_parameter_we=1;host_parameter_layer=layer;
            host_parameter_tile=tile_addr;host_bias_data=bias;
            host_multiplier_data=mult;host_shift_data=shift;
            @(posedge clk);@(negedge clk);host_parameter_we=0;end
    endtask

    task reject_invalid_class_count;
        input [3:0] invalid_count;
        begin
            @(negedge clk);class_count=invalid_count;start_valid=1;
            repeat(3)begin
                @(posedge clk);
                if(start_ready||busy||done)
                    $fatal(1,"invalid class_count=%0d was accepted",invalid_count);
            end
            @(negedge clk);start_valid=0;
        end
    endtask

    task run_and_check;
        input integer classes;
        integer run_cycles;
        begin
            @(negedge clk);class_count=classes;start_valid=1;
            while(!start_ready)@(posedge clk);
            @(posedge clk);@(negedge clk);start_valid=0;
            run_cycles=0;
            while(!done)begin
                @(posedge clk);run_cycles=run_cycles+1;
                if(run_cycles>200000)$fatal(1,"TinyCNN top timeout classes=%0d",classes);
            end
            cycles=cycles+run_cycles;
            for(o=0;o<classes;o=o+1)begin
                expected=o-2;
                for(ic=0;ic<160;ic=ic+1)
                    expected=expected+(2+(ic%8))*(((ic+o)%3)-1);
                if($signed(logits[o*32 +: 32])!==expected)
                    $fatal(1,"classes=%0d logit=%0d expected=%0d actual=%0d",
                           classes,o,expected,$signed(logits[o*32 +: 32]));
            end
            for(o=classes;o<8;o=o+1)
                if(logits[o*32 +: 32]!==0)
                    $fatal(1,"classes=%0d masked logit=%0d is nonzero",classes,o);
        end
    endtask

    task load_fc_weights;
        input integer classes;
        integer fc_tiles,fc_k,fc_tile,fc_lane,fc_oc;
        reg [ARRAY_COLS*8-1:0] fc_word;
        begin
            fc_tiles=(classes+ARRAY_COLS-1)/ARRAY_COLS;
            for(fc_k=0;fc_k<160;fc_k=fc_k+1)
                for(fc_tile=0;fc_tile<fc_tiles;fc_tile=fc_tile+1)begin
                    fc_word=0;
                    for(fc_lane=0;fc_lane<ARRAY_COLS;fc_lane=fc_lane+1)begin
                        fc_oc=fc_tile*ARRAY_COLS+fc_lane;
                        if(fc_oc<classes)
                            fc_word[fc_lane*8 +: 8]=((fc_k+fc_oc)%3)-1;
                    end
                    write_weight(FC_BASE+fc_k*fc_tiles+fc_tile,fc_word);
                end
        end
    endtask

    reg [ARRAY_COLS*8-1:0] weight_word;
    reg [ARRAY_COLS*32-1:0] bias_word,mult_word;
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_word;
    initial begin
        repeat(3)@(posedge clk);@(negedge clk);rst=0;
        for(index=0;index<20*16;index=index+1)write_activation(index,2);

        // Conv1: only center tap is one; bias makes channel c equal 2+c.
        for(k=0;k<9;k=k+1)for(tile=0;tile<TILES;tile=tile+1)begin
            weight_word=0;
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)begin
                oc=tile*ARRAY_COLS+lane;
                if(k==4 && oc<8)weight_word[lane*8 +: 8]=1;
            end
            write_weight(k*TILES+tile,weight_word);
        end
        // Conv2: center 1x1 channel identity inside the 3x3 kernel.
        for(k=0;k<72;k=k+1)for(tile=0;tile<TILES;tile=tile+1)begin
            weight_word=0;ic=k%8;
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)begin
                oc=tile*ARRAY_COLS+lane;
                if(k>=32 && k<40 && oc==ic)weight_word[lane*8 +: 8]=1;
            end
            write_weight(CONV2_BASE+k*TILES+tile,weight_word);
        end
        // FC weights are packed using the selected model's output tile count.
        load_fc_weights(6);

        for(tile=0;tile<TILES;tile=tile+1)begin
            bias_word=0;mult_word=0;shift_word=0;
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)begin
                oc=tile*ARRAY_COLS+lane;
                if(oc<8)begin bias_word[lane*32 +: 32]=oc;
                    mult_word[lane*32 +: 32]=32'sh40000000;
                    shift_word[lane*SHIFT_WIDTH +: SHIFT_WIDTH]=1;end
            end
            write_parameter(0,tile,bias_word,mult_word,shift_word);
            bias_word=0;
            write_parameter(1,tile,bias_word,mult_word,shift_word);
            bias_word=0;
            for(lane=0;lane<ARRAY_COLS;lane=lane+1)begin
                oc=tile*ARRAY_COLS+lane;
                if(oc<8)bias_word[lane*32 +: 32]=oc-2;
            end
            write_parameter(3,tile,bias_word,0,0);
        end

        reject_invalid_class_count(0);
        reject_invalid_class_count(9);
        run_and_check(6);
        // Pool2 reuses act_a, so the host reloads the next inference tensor.
        for(index=0;index<20*16;index=index+1)write_activation(index,2);
        load_fc_weights(4);
        run_and_check(4);
        for(index=0;index<20*16;index=index+1)write_activation(index,2);
        load_fc_weights(8);
        run_and_check(8);
        $display("ALL TINYCNN8 TOP TESTS PASSED rows=%0d cols=%0d total_cycles=%0d logits0-3=%0d,%0d,%0d,%0d",
            ARRAY_ROWS,ARRAY_COLS,cycles,$signed(logits[31:0]),$signed(logits[63:32]),
            $signed(logits[95:64]),$signed(logits[127:96]));$finish;
    end
endmodule
