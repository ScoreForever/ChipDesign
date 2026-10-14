`timescale 1ns/1ps
module tb_axi_sram_pipeline;
    parameter READ_LATENCY=3;
    reg clk=0,rst_n=0;
    always #5 clk=~clk;
    AXI_BUS #(.AXI_ADDR_WIDTH(32),.AXI_DATA_WIDTH(32),
              .AXI_ID_WIDTH(4),.AXI_USER_WIDTH(1)) bus();
    wire req,we;
    wire [31:0] addr,wdata,rdata;
    wire [3:0] be;
    axi2mem #(.AXI_ID_WIDTH(4),.AXI_ADDR_WIDTH(32),.AXI_DATA_WIDTH(32),
              .AXI_USER_WIDTH(1),.READ_LATENCY(READ_LATENCY)) adapter(
        .clk_i(clk),.rst_ni(rst_n),.slave(bus),.req_o(req),.we_o(we),
        .addr_o(addr),.be_o(be),.user_o(),.data_o(wdata),.user_i(1'b0),.data_i(rdata));
    my_mainmem #(.INIT_FILE(""),.PIPELINED(READ_LATENCY==3)) mem(
        .clk_i(clk),.rst_ni(rst_n),.req_i(req),.we_i(we),.be_i(be),
        .addr_i(addr),.wdata_i(wdata),.rdata_o(rdata));
    reg [31:0] expected[0:2047];
    integer reads=0,writes=0;
    task write_burst;
        input integer base,count;
        input [31:0] seed;
        input [3:0] strobes;
        integer i,byte_index;
        reg [31:0] value;
        begin
            @(negedge clk);
            bus.aw_addr=base*4;bus.aw_len=count-1;bus.aw_valid=1;
            bus.w_valid=0;
            @(posedge clk);while(!bus.aw_ready) @(posedge clk);
            @(negedge clk);bus.aw_valid=0;
            repeat(2) @(negedge clk);
            for(i=0;i<count;i=i+1) begin
                value=seed+i*32'h01010101;
                bus.w_valid=1;bus.w_data=value;bus.w_strb=strobes;bus.w_last=(i==count-1);
                @(posedge clk);
                while(!bus.w_ready) @(posedge clk);
                for(byte_index=0;byte_index<4;byte_index=byte_index+1)
                    if(strobes[byte_index]) expected[base+i][byte_index*8 +: 8]=value[byte_index*8 +: 8];
                writes=writes+1;
                @(negedge clk);bus.aw_valid=0;bus.w_valid=0;
                // Exercise the WAIT_WVALID/WRITE state with gaps between beats.
                if(i<count-1) repeat(2) @(negedge clk);
            end
            while(!bus.b_valid) @(negedge clk);
            repeat(3) begin
                @(negedge clk);
                if(!bus.b_valid || bus.b_id!==4'd3 || bus.b_resp!==0)
                    $fatal(1,"write response changed under backpressure");
            end
            bus.b_ready=1;
            @(posedge clk);@(negedge clk);bus.b_ready=0;
        end
    endtask
    task read_burst;
        input integer base,count;
        integer i;
        reg [31:0] held_data;
        reg held_last;
        begin
            @(negedge clk);bus.ar_addr=base*4;bus.ar_len=count-1;bus.ar_valid=1;
            @(posedge clk);while(!bus.ar_ready) @(posedge clk);
            @(negedge clk);bus.ar_valid=0;
            for(i=0;i<count;i=i+1) begin
                while(!bus.r_valid) @(negedge clk);
                held_data=bus.r_data;held_last=bus.r_last;
                if(held_data!==expected[base+i] || held_last!==(i==count-1) ||
                   bus.r_id!==4'd5 || bus.r_resp!==0)
                    $fatal(1,"latency=%0d word=%0d expected=%h actual=%h last=%b",
                        READ_LATENCY,base+i,expected[base+i],held_data,held_last);
                repeat(4) begin
                    @(negedge clk);
                    if(!bus.r_valid || bus.r_data!==held_data || bus.r_last!==held_last)
                        $fatal(1,"read response changed under backpressure");
                end
                bus.r_ready=1;
                @(posedge clk);reads=reads+1;
                @(negedge clk);bus.r_ready=0;
            end
        end
    endtask
    task reset_with_pending_read;
        begin
            @(negedge clk);bus.ar_addr=16*4;bus.ar_len=0;bus.ar_valid=1;
            @(posedge clk);while(!bus.ar_ready) @(posedge clk);
            @(negedge clk);bus.ar_valid=0;rst_n=0;
            repeat(5) begin
                @(negedge clk);
                if(bus.r_valid || bus.b_valid) $fatal(1,"response survived reset");
            end
            rst_n=1;
            read_burst(16,8);
        end
    endtask
    integer i;
    initial begin
        bus.aw_id=3;bus.aw_addr=0;bus.aw_len=0;bus.aw_size=2;bus.aw_burst=1;
        bus.aw_lock=0;bus.aw_cache=0;bus.aw_prot=0;bus.aw_qos=0;bus.aw_region=0;
        bus.aw_atop=0;bus.aw_user=0;bus.aw_valid=0;
        bus.w_data=0;bus.w_strb=0;bus.w_last=0;bus.w_user=0;bus.w_valid=0;bus.b_ready=0;
        bus.ar_id=5;bus.ar_addr=0;bus.ar_len=0;bus.ar_size=2;bus.ar_burst=1;
        bus.ar_lock=0;bus.ar_cache=0;bus.ar_prot=0;bus.ar_qos=0;bus.ar_region=0;
        bus.ar_user=0;bus.ar_valid=0;bus.r_ready=0;
        for(i=0;i<2048;i=i+1) expected[i]=0;
        repeat(5) @(negedge clk);rst_n=1;
        write_burst(16,8,32'h12345678,4'b1111);
        read_burst(16,8);
        write_burst(18,4,32'habcdef80,4'b0101);
        write_burst(20,2,32'hffffffff,4'b0000);
        read_burst(16,8);
        write_burst(2044,4,32'h87654321,4'b1111);
        read_burst(2044,4);
        reset_with_pending_read();
        read_burst(2044,4);
        $display("ALL AXI SRAM PIPELINE TESTS PASSED latency=%0d writes=%0d reads=%0d",
            READ_LATENCY,writes,reads);
        $finish;
    end
    initial begin
        #1000000;$fatal(1,"AXI SRAM test timeout");
    end
endmodule
