`timescale 1ns/1ps
// Regression for the current Conv/Pool/Conv/Pool/Flatten/FC implementation.
// Exercise every Conv1 activation location, including byte 2559, and compare
// nonzero logits against a hand-computed identity-convolution network.
module tb_tinycnn8_capacity;
    parameter ACT_BANK_BYTES=2560;
    parameter ACT_A_BANK_BYTES=640;
    reg clk=0,rst=1,start_valid=0;
    always #5 clk=~clk;
    reg [3:0] class_count=4;
    wire start_ready,busy,done;
    wire [255:0] logits;
    reg host_activation_we=0,host_weight_we=0,host_parameter_we=0;
    reg [15:0] host_activation_addr=0,host_weight_addr=0;
    reg [7:0] host_activation_data=0,host_parameter_tile=0;
    reg [1:0] host_parameter_layer=0;
    reg [63:0] host_weight_data=0;
    reg [255:0] host_bias_data=0,host_multiplier_data=0;
    reg [47:0] host_shift_data=0;
    tinycnn8_npu_top #(.ACT_BANK_BYTES(ACT_BANK_BYTES),
        .ACT_A_BANK_BYTES(ACT_A_BANK_BYTES)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .class_count(class_count),.busy(busy),.done(done),.logits(logits),
        .host_activation_we(host_activation_we),.host_activation_addr(host_activation_addr),
        .host_activation_data(host_activation_data),.host_weight_we(host_weight_we),
        .host_weight_addr(host_weight_addr),.host_weight_data(host_weight_data),
        .host_parameter_we(host_parameter_we),.host_parameter_layer(host_parameter_layer),
        .host_parameter_tile(host_parameter_tile),.host_bias_data(host_bias_data),
        .host_multiplier_data(host_multiplier_data),.host_shift_data(host_shift_data));
    reg seen_last_byte=0,seen_last_a_byte=0;
    always @(posedge clk) begin
        if(rst || (start_valid && start_ready)) begin
            seen_last_byte<=0;seen_last_a_byte<=0;
        end else begin
            if(dut.conv_phase==0 && dut.conv_write_valid &&
                dut.conv_write_addr==2552 && dut.conv_write_mask[7])
                seen_last_byte<=1;
            if(dut.pool_write_valid && dut.pool_write_addr==632 && dut.pool_write_mask[7])
                seen_last_a_byte<=1;
        end
    end
    function integer spatial_input;
        input integer y,x;
        begin spatial_input=((y*16+x)*7+y)%20+1; end
    endfunction
    task write_activation;
        input integer addr,value;
        begin
            @(negedge clk);host_activation_we=1;host_activation_addr=addr;host_activation_data=value;
            @(negedge clk);host_activation_we=0;
        end
    endtask
    task write_weight;
        input integer addr;
        input [63:0] value;
        begin
            @(negedge clk);host_weight_we=1;host_weight_addr=addr;host_weight_data=value;
            @(negedge clk);host_weight_we=0;
        end
    endtask
    task write_parameter;
        input integer layer;
        input [255:0] bias,mult;
        input [47:0] shift;
        begin
            @(negedge clk);host_parameter_we=1;host_parameter_layer=layer;
            host_bias_data=bias;host_multiplier_data=mult;host_shift_data=shift;
            @(negedge clk);host_parameter_we=0;
        end
    endtask
    task run_and_check;
        input integer classes,input_value;
        integer index,cycles,o,c,expected,y,x,dy,dx,pool_max,spatial_sum;

        begin
            for(index=0;index<320;index=index+1)
                write_activation(index,input_value<0?spatial_input(index/16,index%16):input_value);
            @(negedge clk);class_count=classes;start_valid=1;
            @(posedge clk);
            if(!start_ready) $fatal(1,"job not ready");
            @(negedge clk);start_valid=0;
            cycles=0;
            while(!done && cycles<200000) begin
                @(negedge clk);cycles=cycles+1;
            end
            if(!done || !seen_last_byte || !seen_last_a_byte)
                $fatal(1,"timeout or missing bank boundary coverage (A=639,B=2559)");
            spatial_sum=0;
            for(y=0;y<5;y=y+1) for(x=0;x<4;x=x+1) begin
                pool_max=0;
                for(dy=0;dy<4;dy=dy+1) for(dx=0;dx<4;dx=dx+1)
                    if((input_value<0?spatial_input(y*4+dy,x*4+dx):input_value)>pool_max)
                        pool_max=(input_value<0?spatial_input(y*4+dy,x*4+dx):input_value);
                spatial_sum=spatial_sum+pool_max;
            end
            for(o=0;o<8;o=o+1) begin
                expected=0;
                if(o<classes) begin
                    expected=o-2;
                    for(c=0;c<8;c=c+1)
                        expected=expected+(spatial_sum+20*c)*(((c+o)%3)-1);
                end
                if($signed(logits[o*32 +: 32])!==expected)
                    $fatal(1,"capacity=%0d classes=%0d lane=%0d expected=%0d actual=%0d",
                        ACT_BANK_BYTES,classes,o,expected,$signed(logits[o*32 +: 32]));
            end
            $display("PASS Flat capacity=%0d classes=%0d input=%0d cycles=%0d",
                ACT_BANK_BYTES,classes,input_value,cycles);
        end
    endtask
    integer k,c;
    reg [63:0] weights;
    reg [255:0] biases;
    initial begin
        repeat(3) @(negedge clk);rst=0;
        for(k=0;k<256;k=k+1) begin
            weights=0;
            if(k==4) weights={8{8'd1}};
            if(k>=41 && k<49) weights[(k-41)*8 +: 8]=1;
            if(k>=81 && k<241)
                for(c=0;c<8;c=c+1) weights[c*8 +: 8]=((((k-81)%8+c)%3)-1);
            write_weight(k,weights);
        end
        biases=0;
        for(c=0;c<8;c=c+1) biases[c*32 +: 32]=c;
        write_parameter(0,biases,{8{32'h40000000}},{8{6'd1}});
        write_parameter(1,0,{8{32'h40000000}},{8{6'd1}});
        for(c=0;c<8;c=c+1) biases[c*32 +: 32]=c-2;
        write_parameter(3,biases,0,0);
        run_and_check(6,2);
        run_and_check(4,5);
        run_and_check(8,9);
        run_and_check(8,-1);
        $display("ALL FLAT CAPACITY TESTS PASSED");
        $finish;
    end
endmodule
