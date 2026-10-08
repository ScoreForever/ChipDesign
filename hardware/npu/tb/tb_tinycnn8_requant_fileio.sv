`timescale 1ns/1ps
module tb_tinycnn8_requant_fileio;
    reg clk=0,rst=1,in_valid=0,out_ready=0;
    always #5 clk=~clk;
    wire in_ready,out_valid;
    reg lane_mask=1;
    reg [31:0] acc_data=0,bias_data=0,multiplier_data=0;
    reg [5:0] shift_data=0;
    reg signed [31:0] output_offset=0;
    reg signed [7:0] activation_min=-128,activation_max=127;
    wire [7:0] out_data;
    reg [167:0] vectors[0:1023];
    string vector_file;
    integer count=0,index,stall;
    reg [7:0] expected;
    requant_unit #(.LANES(1)) dut(
        .clk(clk),.rst(rst),.in_valid(in_valid),.in_ready(in_ready),
        .lane_mask(lane_mask),.acc_data(acc_data),.bias_data(bias_data),
        .multiplier_data(multiplier_data),.shift_data(shift_data),
        .output_offset(output_offset),.activation_min(activation_min),
        .activation_max(activation_max),.out_data(out_data),
        .out_valid(out_valid),.out_ready(out_ready));
    initial begin
        if(!$value$plusargs("VECTORS=%s",vector_file))$fatal(1,"missing VECTORS");
        if(!$value$plusargs("COUNT=%d",count))$fatal(1,"missing COUNT");
        if(count<1||count>1024)$fatal(1,"invalid vector count");
        $readmemh(vector_file,vectors,0,count-1);
        repeat(3)@(negedge clk);rst=0;
        for(index=0;index<count;index=index+1)begin
            @(negedge clk);
            {acc_data,bias_data,multiplier_data,output_offset,
                activation_min,activation_max,expected}=vectors[index][167:16];
            shift_data=vectors[index][5:0];
            lane_mask=vectors[index][8];
            out_ready=0;in_valid=1;
            while(!in_ready)@(negedge clk);
            @(negedge clk);in_valid=0;
            if(!out_valid)$fatal(1,"requant output missing index=%0d",index);
            for(stall=0;stall<(index%4)+1;stall=stall+1)begin
                if(!out_valid||out_data!==expected)
                    $fatal(1,"requant mismatch index=%0d expected=%h actual=%h",index,expected,out_data);
                @(negedge clk);
            end
            out_ready=1;@(negedge clk);out_ready=0;
            if(out_valid)$fatal(1,"requant duplicate output");
        end
        $display("PASS independent Q31 oracle vectors=%0d with output backpressure",count);$finish;
    end
    initial begin #1000000;$fatal(1,"requant oracle timeout");end
endmodule
