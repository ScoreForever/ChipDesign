`timescale 1ns/1ps
// Cross-checks the pipelined single-lane quantizer against requant_unit,
// which is itself verified against the independent Python oracle. Every
// cycle issues a new operand (II=1); results are compared in order.
module tb_requant_lane;
    localparam N=20000, SHIFT_WIDTH=6;
    reg clk=0,rst=1;
    always #5 clk=~clk;

    reg in_valid=0;
    reg signed [31:0] value=0,multiplier=0;
    reg signed [SHIFT_WIDTH-1:0] shift=0;
    reg signed [7:0] act_min=0,act_max=127;
    reg [15:0] tag_in=0;
    wire out_valid;
    wire [7:0] out_data;
    wire [15:0] out_tag;
    requant_lane #(.SHIFT_WIDTH(SHIFT_WIDTH),.TAG_WIDTH(16)) dut(
        .clk(clk),.rst(rst),.in_valid(in_valid),.in_value(value),
        .in_multiplier(multiplier),.in_shift(shift),.activation_min(act_min),
        .activation_max(act_max),.in_tag(tag_in),.out_valid(out_valid),
        .out_data(out_data),.out_tag(out_tag));

    // Reference: one-lane requant_unit, sampled combinationally per operand.
    wire ref_in_ready,ref_out_valid;
    wire [7:0] ref_out;
    requant_unit #(.LANES(1),.SHIFT_WIDTH(SHIFT_WIDTH)) reference(
        .clk(clk),.rst(rst),.in_valid(in_valid),.in_ready(ref_in_ready),
        .lane_mask(1'b1),.acc_data(value),.bias_data(32'd0),
        .multiplier_data(multiplier),.shift_data(shift),.output_offset(32'sd0),
        .activation_min(act_min),.activation_max(act_max),.out_data(ref_out),
        .out_valid(ref_out_valid),.out_ready(1'b1));

    reg [7:0] expected[0:N-1];
    integer issued=0,checked=0,ref_index=0,seed=20261013,pick;
    always @(posedge clk) begin
        if(ref_out_valid)begin expected[ref_index]=ref_out;ref_index=ref_index+1;end
        if(out_valid)begin
            if(out_tag!==checked[15:0])$fatal(1,"tag order mismatch %0d/%0d",out_tag,checked);
            // The reference retires each operand three cycles before the lane.
            if(out_data!==expected[checked])
                $fatal(1,"mismatch %0d expected %0d actual %0d",checked,
                    $signed(expected[checked]),$signed(out_data));
            checked=checked+1;
        end
    end

    function signed [31:0] edge_value(input integer k);
        case(k%12)
            0:edge_value=32'sh80000000; 1:edge_value=32'sh7fffffff; 2:edge_value=0;
            3:edge_value=-1; 4:edge_value=1; 5:edge_value=127; 6:edge_value=-129;
            7:edge_value=65537; 8:edge_value=-65537; 9:edge_value=32'sh40000000;
            10:edge_value=1540; default:edge_value=-1540;
        endcase
    endfunction

    initial begin
        repeat(3)@(posedge clk);@(negedge clk);rst=0;
        while(issued<N)begin
            @(negedge clk);
            pick=$urandom(seed)%4;
            value=(pick==0)?edge_value(issued):(pick==1)?($random(seed)%(1<<22)):$random(seed);
            pick=$urandom(seed)%4;
            multiplier=(pick==0)?32'sh80000000:(pick==1)?32'sh7fffffff:
                       (pick==2)?(32'sh40000000+($urandom(seed)%32'h40000000)):$random(seed);
            shift=$random(seed);
            pick=$urandom(seed)%3;
            act_min=(pick==0)?-128:(pick==1)?0:-64;
            act_max=(pick==0)?127:(pick==1)?100:63;
            tag_in=issued[15:0];in_valid=1;
            @(posedge clk);issued=issued+1;
        end
        @(negedge clk);in_valid=0;
        repeat(10)@(posedge clk);
        if(checked!=N)$fatal(1,"only %0d of %0d results retired",checked,N);
        $display("PASS requant_lane %0d operands at II=1",N);
        $finish;
    end
endmodule
