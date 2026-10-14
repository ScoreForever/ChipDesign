`timescale 1ns/1ps
// Golden-file test for the fused TinyCNN-8 core (4x8 production shape).
// Loads one tinycnn8_golden.py case through the host ports, runs the same
// job twice without reset (reloading the input, as the ABI requires), and
// compares every Pool1/Pool2 byte left in the activation store plus all
// eight logits. Conv1/Conv2 outputs are never materialized by this core, so
// they are covered through the pooled tensors that depend on them.
module tb_tinycnn8_fused_golden;
    localparam ARRAY_ROWS=4,ARRAY_COLS=8,SHIFT_WIDTH=6;
    localparam P1_BASE=320,P2_BASE=0;
    reg clk=0,rst=1,start_valid=0;
    reg [3:0] class_count=6;
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
    wire [31:0] perf_total_cycles,perf_weight_rows,perf_matrix_issues;
    wire [31:0] perf_matrix_retires,perf_peak_inflight;
    wire [6*32-1:0] perf_layer_cycles;
    wire perf_overflow,perf_valid;

    tinycnn8_npu_fused #(.ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS)) dut(
        .clk(clk),.rst(rst),.start_valid(start_valid),.start_ready(start_ready),
        .class_count(class_count),.busy(busy),.done(done),.logits(logits),
        .host_activation_we(host_activation_we),.host_activation_addr(host_activation_addr),
        .host_activation_data(host_activation_data),.host_weight_we(host_weight_we),
        .host_weight_addr(host_weight_addr),.host_weight_data(host_weight_data),
        .host_parameter_we(host_parameter_we),.host_parameter_layer(host_parameter_layer),
        .host_parameter_tile(host_parameter_tile),.host_bias_data(host_bias_data),
        .host_multiplier_data(host_multiplier_data),.host_shift_data(host_shift_data),
        .perf_total_cycles(perf_total_cycles),.perf_layer_cycles(perf_layer_cycles),
        .perf_weight_rows(perf_weight_rows),.perf_matrix_issues(perf_matrix_issues),
        .perf_matrix_retires(perf_matrix_retires),.perf_peak_inflight(perf_peak_inflight),
        .perf_overflow(perf_overflow),.perf_valid(perf_valid));

    reg [7:0] input_image[0:319],shift_image[0:31];
    reg [63:0] weight_image[0:255];
    reg [31:0] bias_image[0:31],mult_image[0:31];
    reg [7:0] golden_pool1[0:639],golden_pool2[0:159];
    reg [31:0] golden_fc[0:7];
    string data_dir;
    integer classes=6,index,lane,layer,run,run_cycles,errors=0;

    function [7:0] act_byte(input integer addr);
        case(addr%8)
            0:act_byte=dut.g_act_banks[0].mem[addr/8];
            1:act_byte=dut.g_act_banks[1].mem[addr/8];
            2:act_byte=dut.g_act_banks[2].mem[addr/8];
            3:act_byte=dut.g_act_banks[3].mem[addr/8];
            4:act_byte=dut.g_act_banks[4].mem[addr/8];
            5:act_byte=dut.g_act_banks[5].mem[addr/8];
            6:act_byte=dut.g_act_banks[6].mem[addr/8];
            default:act_byte=dut.g_act_banks[7].mem[addr/8];
        endcase
    endfunction

    task load_input;
        begin
            for(index=0;index<320;index=index+1)begin
                @(negedge clk);host_activation_we=1;host_activation_addr=index;
                host_activation_data=input_image[index];
            end
            @(negedge clk);host_activation_we=0;
        end
    endtask

    task run_and_check;
        begin
            @(negedge clk);class_count=classes;start_valid=1;
            while(!start_ready)@(posedge clk);
            @(posedge clk);@(negedge clk);start_valid=0;
            run_cycles=0;
            while(!done)begin
                @(posedge clk);run_cycles=run_cycles+1;
                if(run_cycles>100000)$fatal(1,"fused core timeout");
            end
            @(negedge clk);
            for(index=0;index<640;index=index+1)
                if(act_byte(P1_BASE+index)!==golden_pool1[index])begin
                    errors=errors+1;
                    if(errors<10)$display("pool1[%0d] expected %0d actual %0d",index,
                        $signed(golden_pool1[index]),$signed(act_byte(P1_BASE+index)));
                end
            for(index=0;index<160;index=index+1)
                if(act_byte(P2_BASE+index)!==golden_pool2[index])begin
                    errors=errors+1;
                    if(errors<10)$display("pool2[%0d] expected %0d actual %0d",index,
                        $signed(golden_pool2[index]),$signed(act_byte(P2_BASE+index)));
                end
            for(index=0;index<8;index=index+1)begin
                if(index<classes && logits[index*32 +: 32]!==golden_fc[index])begin
                    errors=errors+1;
                    $display("logit[%0d] expected %0d actual %0d",index,
                        $signed(golden_fc[index]),$signed(logits[index*32 +: 32]));
                end
                if(index>=classes && logits[index*32 +: 32]!==0)begin
                    errors=errors+1;$display("masked logit[%0d] nonzero",index);
                end
            end
            if(!perf_valid || perf_overflow)$fatal(1,"profile not valid");
            if(perf_total_cycles!=perf_layer_cycles[31:0]+perf_layer_cycles[95:64]+
               perf_layer_cycles[191:160])$fatal(1,"profile lanes do not close");
            if(perf_layer_cycles[63:32]!=0||perf_layer_cycles[127:96]!=0||
               perf_layer_cycles[159:128]!=0)$fatal(1,"folded profile lanes nonzero");
            if(perf_matrix_issues!=perf_matrix_retires)$fatal(1,"matrix transaction loss");
            if(perf_matrix_issues!=960+1440+40*((classes+7)/8))
                $fatal(1,"unexpected issue count %0d",perf_matrix_issues);
            if(errors)$fatal(1,"FAIL fused golden run=%0d errors=%0d",run,errors);
            $display("PROFILE run=%0d classes=%0d total=%0d C1=%0d C2=%0d FC=%0d weight_rows=%0d issues=%0d peak_inflight=%0d",
                run,classes,perf_total_cycles,perf_layer_cycles[31:0],perf_layer_cycles[95:64],
                perf_layer_cycles[191:160],perf_weight_rows,perf_matrix_issues,perf_peak_inflight);
        end
    endtask

    initial begin
        if(!$value$plusargs("DATA_DIR=%s",data_dir))$fatal(1,"missing DATA_DIR");
        if($value$plusargs("CLASSES=%d",classes))begin end
        $readmemh({data_dir,"/input.hex"},input_image);
        $readmemh({data_dir,"/weights.hex"},weight_image);
        $readmemh({data_dir,"/bias.hex"},bias_image);
        $readmemh({data_dir,"/multiplier.hex"},mult_image);
        $readmemh({data_dir,"/shift.hex"},shift_image);
        $readmemh({data_dir,"/golden_pool1.hex"},golden_pool1);
        $readmemh({data_dir,"/golden_pool2.hex"},golden_pool2);
        for(index=0;index<8;index=index+1)golden_fc[index]=0;
        $readmemh({data_dir,"/golden_fc.hex"},golden_fc,0,classes-1);
        repeat(3)@(posedge clk);@(negedge clk);rst=0;
        for(index=0;index<256;index=index+1)begin
            @(negedge clk);host_weight_we=1;host_weight_addr=index;
            host_weight_data=weight_image[index];
        end
        @(negedge clk);host_weight_we=0;
        for(layer=0;layer<4;layer=layer+1)if(layer!=2)begin
            @(negedge clk);host_parameter_we=1;host_parameter_layer=layer;
            host_parameter_tile=0;
            for(lane=0;lane<8;lane=lane+1)begin
                host_bias_data[lane*32 +: 32]=bias_image[layer*8+lane];
                host_multiplier_data[lane*32 +: 32]=mult_image[layer*8+lane];
                host_shift_data[lane*6 +: 6]=shift_image[layer*8+lane][5:0];
            end
        end
        @(negedge clk);host_parameter_we=0;
        for(run=0;run<2;run=run+1)begin
            load_input();
            run_and_check();
        end
        $display("PASS fused golden classes=%0d cycles=%0d",classes,run_cycles);
        $finish;
    end
endmodule
