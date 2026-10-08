`timescale 1ns/1ps

module tb_tinycnn8_trained_model;
    localparam ARRAY_ROWS=4,ARRAY_COLS=8,SHIFT_WIDTH=6;
    reg clk=0,rst=1,start_valid=0;
    reg [3:0] class_count=4;
    always #5 clk=~clk;
    wire start_ready,busy,done;
    wire [255:0] logits;
    reg host_activation_we=0,host_weight_we=0,host_parameter_we=0;
    reg [15:0] host_activation_addr=0,host_weight_addr=0;
    reg [7:0] host_activation_data=0,host_parameter_tile=0;
    reg [1:0] host_parameter_layer=0;
    reg [63:0] host_weight_data=0;
    reg [255:0] host_bias_data=0,host_multiplier_data=0;
    reg [47:0] host_shift_data=0;

    reg [63:0] weight_vectors[0:255];
    reg [7:0] input_vectors[0:31999];
    reg [31:0] expected_logits[0:399];
    reg [31:0] conv1_bias[0:7],conv1_mult[0:7];
    reg [7:0] conv1_shift[0:7];
    reg [31:0] conv2_bias[0:7],conv2_mult[0:7];
    reg [7:0] conv2_shift[0:7];
    reg [31:0] fc_bias[0:7];
    reg [31:0] count_mem[0:0];
    string vector_dir;
    integer sample_count,sample,index,lane,cycles;
    reg [255:0] bias_word,mult_word;
    reg [47:0] shift_word;

    tinycnn8_npu_top dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .class_count(class_count),.busy(busy),.done(done),.logits(logits),
        .host_activation_we(host_activation_we),.host_activation_addr(host_activation_addr),
        .host_activation_data(host_activation_data),.host_weight_we(host_weight_we),
        .host_weight_addr(host_weight_addr),.host_weight_data(host_weight_data),
        .host_parameter_we(host_parameter_we),.host_parameter_layer(host_parameter_layer),
        .host_parameter_tile(host_parameter_tile),.host_bias_data(host_bias_data),
        .host_multiplier_data(host_multiplier_data),.host_shift_data(host_shift_data));

    task write_activation;
        input integer addr;
        input [7:0] value;
        begin @(negedge clk);host_activation_we=1;host_activation_addr=addr;
            host_activation_data=value;@(posedge clk);@(negedge clk);host_activation_we=0;end
    endtask
    task write_weight;
        input integer addr;
        input [63:0] value;
        begin @(negedge clk);host_weight_we=1;host_weight_addr=addr;
            host_weight_data=value;@(posedge clk);@(negedge clk);host_weight_we=0;end
    endtask
    task write_parameter;
        input [1:0] layer;
        input [255:0] bias,mult;
        input [47:0] shift;
        begin @(negedge clk);host_parameter_we=1;host_parameter_layer=layer;
            host_parameter_tile=0;host_bias_data=bias;host_multiplier_data=mult;
            host_shift_data=shift;@(posedge clk);@(negedge clk);host_parameter_we=0;end
    endtask

    initial begin
        if(!$value$plusargs("VECTOR_DIR=%s",vector_dir))
            $fatal(1,"missing +VECTOR_DIR=<path>");
        $readmemh({vector_dir,"/weights.hex"},weight_vectors);
        $readmemh({vector_dir,"/conv1_bias.hex"},conv1_bias);
        $readmemh({vector_dir,"/conv1_mult.hex"},conv1_mult);
        $readmemh({vector_dir,"/conv1_shift.hex"},conv1_shift);
        $readmemh({vector_dir,"/conv2_bias.hex"},conv2_bias);
        $readmemh({vector_dir,"/conv2_mult.hex"},conv2_mult);
        $readmemh({vector_dir,"/conv2_shift.hex"},conv2_shift);
        $readmemh({vector_dir,"/fc_bias.hex"},fc_bias);
        $readmemh({vector_dir,"/count.hex"},count_mem);
        sample_count=count_mem[0];
        if(sample_count<1 || sample_count>100)$fatal(1,"bad sample count %0d",sample_count);
        $readmemh({vector_dir,"/inputs.hex"},input_vectors,0,sample_count*320-1);
        $readmemh({vector_dir,"/logits.hex"},expected_logits,0,sample_count*4-1);

        repeat(3)@(posedge clk);@(negedge clk);rst=0;
        for(index=0;index<256;index=index+1)write_weight(index,weight_vectors[index]);

        bias_word=0;mult_word=0;shift_word=0;
        for(lane=0;lane<8;lane=lane+1)begin
            bias_word[lane*32 +: 32]=conv1_bias[lane];
            mult_word[lane*32 +: 32]=conv1_mult[lane];
            shift_word[lane*6 +: 6]=conv1_shift[lane][5:0];
        end
        write_parameter(0,bias_word,mult_word,shift_word);
        bias_word=0;mult_word=0;shift_word=0;
        for(lane=0;lane<8;lane=lane+1)begin
            bias_word[lane*32 +: 32]=conv2_bias[lane];
            mult_word[lane*32 +: 32]=conv2_mult[lane];
            shift_word[lane*6 +: 6]=conv2_shift[lane][5:0];
        end
        write_parameter(1,bias_word,mult_word,shift_word);
        bias_word=0;
        for(lane=0;lane<8;lane=lane+1)bias_word[lane*32 +: 32]=fc_bias[lane];
        write_parameter(3,bias_word,0,0);

        for(sample=0;sample<sample_count;sample=sample+1)begin
            for(index=0;index<320;index=index+1)
                write_activation(index,input_vectors[sample*320+index]);
            @(negedge clk);start_valid=1;while(!start_ready)@(posedge clk);
            @(posedge clk);@(negedge clk);start_valid=0;cycles=0;
            while(!done)begin @(posedge clk);cycles=cycles+1;
                if(cycles>100000)$fatal(1,"timeout sample=%0d",sample);end
            for(lane=0;lane<4;lane=lane+1)
                if(logits[lane*32 +: 32]!==expected_logits[sample*4+lane])
                    $fatal(1,"sample=%0d logit=%0d expected=%0d actual=%0d",
                        sample,lane,$signed(expected_logits[sample*4+lane]),
                        $signed(logits[lane*32 +: 32]));
        end
        $display("ALL TRAINED FLATTEN RTL VECTORS PASSED samples=%0d",sample_count);
        $finish;
    end
endmodule
