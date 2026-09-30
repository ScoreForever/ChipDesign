`timescale 1ns/1ps
module tb_maxpool2x2_engine;
    parameter LANES = 8;
    parameter TEST_POOL = 1;
    localparam IH = (TEST_POOL == 1) ? 20 : 10;
    localparam IW = (TEST_POOL == 1) ? 16 : 8;
    localparam C = 8, OH = IH/2, OW = IW/2;
    localparam [7:0] IH_VALUE=IH, IW_VALUE=IW, C_VALUE=C;
    reg clk=0, rst=1, start_valid=0, output_write_ready=1;
    reg [7:0] cfg_input_height=IH_VALUE;
    always #5 clk=~clk;
    wire start_ready, busy, done;
    wire [15:0] activation_read_addr, output_write_addr;
    reg [7:0] activation_mem [0:IH*IW*C-1];
    reg [7:0] output_mem [0:OH*OW*C-1];
    reg [LANES*8-1:0] activation_read_data;
    wire output_write_valid;
    wire [LANES*8-1:0] output_write_data;
    wire [LANES-1:0] output_write_mask;
    integer lane, index, y, x, c, dy, dx, expected, writes=0, stalls=0, cycles=0;

    always @* begin
        activation_read_data = 0;
        for (lane=0; lane<LANES; lane=lane+1)
            if (activation_read_addr+lane < IH*IW*C)
                activation_read_data[lane*8 +: 8] = activation_mem[activation_read_addr+lane];
    end

    maxpool2x2_engine #(.LANES(LANES)) dut (
        .clk(clk), .rst(rst), .start_valid(start_valid), .start_ready(start_ready),
        .input_height(cfg_input_height), .input_width(IW_VALUE), .channels(C_VALUE),
        .input_base(16'd0), .output_base(16'd0), .busy(busy), .done(done),
        .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .output_write_valid(output_write_valid),
        .output_write_ready(output_write_ready), .output_write_addr(output_write_addr),
        .output_write_data(output_write_data), .output_write_mask(output_write_mask)
    );

    always @(posedge clk) begin
        cycles=cycles+1;
        if(cycles>100000) $fatal(1,"pool timeout");
        if(!rst && output_write_valid && !output_write_ready) stalls=stalls+1;
        if(!rst && output_write_valid && output_write_ready) begin
            for(lane=0; lane<LANES; lane=lane+1)
                if(output_write_mask[lane])
                    output_mem[output_write_addr+lane]=output_write_data[lane*8 +: 8];
            writes=writes+1;
        end
    end

    initial begin
        for(index=0; index<IH*IW*C; index=index+1)
            activation_mem[index]=((index*29+17)%256)-128;
        for(index=0; index<OH*OW*C; index=index+1) output_mem[index]=0;
        repeat(3) @(posedge clk);
        @(negedge clk); rst=0; cfg_input_height=0; start_valid=1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid pool descriptor was accepted");
        end
        @(negedge clk); start_valid=0; cfg_input_height=IH_VALUE;
        @(negedge clk); start_valid=1;
        @(posedge clk); while(!start_ready) @(posedge clk);
        @(negedge clk); start_valid=0;
        while(!done) begin
            @(negedge clk); output_write_ready=($urandom_range(0,5)!=0);
            @(posedge clk);
        end
        @(negedge clk); output_write_ready=1;
        for(y=0;y<OH;y=y+1)
            for(x=0;x<OW;x=x+1)
                for(c=0;c<C;c=c+1) begin
                    expected=-128;
                    for(dy=0;dy<2;dy=dy+1)
                        for(dx=0;dx<2;dx=dx+1)
                            if($signed(activation_mem[((y*2+dy)*IW+(x*2+dx))*C+c])>expected)
                                expected=$signed(activation_mem[((y*2+dy)*IW+(x*2+dx))*C+c]);
                    if($signed(output_mem[(y*OW+x)*C+c])!==expected)
                        $fatal(1,"pool y=%0d x=%0d c=%0d expected=%0d actual=%0d",
                               y,x,c,expected,$signed(output_mem[(y*OW+x)*C+c]));
                end
        if(writes!=OH*OW*((C+LANES-1)/LANES)) $fatal(1,"write count mismatch");
        if(stalls==0) $fatal(1,"missing stalls");
        $display("ALL MAXPOOL2X2 TESTS PASSED pool=%0d lanes=%0d writes=%0d cycles=%0d stalls=%0d",
                 TEST_POOL,LANES,writes,cycles,stalls);
        $finish;
    end
endmodule
