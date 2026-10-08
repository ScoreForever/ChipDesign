`timescale 1ns/1ps
module tb_conv2d_engine;
    parameter ARRAY_ROWS = 4;
    parameter ARRAY_COLS = 8;
    parameter TEST_LAYER = 1;
    parameter OPT_GATHER_LOAD = 0;
    parameter OPT_SPATIAL_TILE = 0;
    parameter SPATIAL_TILE = 16;
    parameter TEST_LIFECYCLE = 0;
    parameter FALLBACK_PAD = 1;
    parameter FINAL_INT32 = 0;
    localparam ADDR_WIDTH = 16, SHIFT_WIDTH = 6;
    localparam IH = (TEST_LAYER == 1) ? 20 : 10;
    localparam IW = (TEST_LAYER == 1) ? 16 : 8;
    localparam IC = (TEST_LAYER == 1) ? 1 : 8;
    localparam OH = IH, OW = IW, OC = 8;
    localparam K = 9*IC;
    localparam [7:0] PAD_VALUE = FALLBACK_PAD;
    localparam [7:0] IH_VALUE = IH, IW_VALUE = IW, IC_VALUE = IC;
    localparam [7:0] OH_VALUE = OH, OW_VALUE = OW, OC_VALUE = OC;

    reg clk = 0, rst = 1;
    always #5 clk = ~clk;
    reg start_valid = 0;
    reg [7:0] cfg_input_height = IH_VALUE;
    wire start_ready, busy, done;
    wire [ADDR_WIDTH-1:0] activation_read_addr, weight_read_addr;
    wire [ADDR_WIDTH-1:0] parameter_tile_addr;
    reg [7:0] activation_mem [0:IH*IW*IC-1];
    reg [ARRAY_COLS*8-1:0] weight_mem [0:K*((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*32-1:0] bias_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*32-1:0] multiplier_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_mem [0:((OC+ARRAY_COLS-1)/ARRAY_COLS)-1];
    wire [7:0] activation_read_data = activation_mem[activation_read_addr];
    wire [ARRAY_COLS*8-1:0] weight_read_data = weight_mem[weight_read_addr];
    wire [ARRAY_COLS*32-1:0] bias_read_data = bias_mem[parameter_tile_addr];
    wire [ARRAY_COLS*32-1:0] multiplier_read_data = multiplier_mem[parameter_tile_addr];
    wire [ARRAY_COLS*SHIFT_WIDTH-1:0] shift_read_data = shift_mem[parameter_tile_addr];
    wire byte_valid, i32_valid;
    wire [ADDR_WIDTH-1:0] byte_addr, i32_addr;
    wire [ARRAY_COLS*8-1:0] byte_data;
    wire [ARRAY_COLS*32-1:0] i32_data;
    wire [ARRAY_COLS-1:0] byte_mask, i32_mask;
    wire output_write_valid = FINAL_INT32 ? i32_valid : byte_valid;
    reg output_write_ready = 1;
    wire [ADDR_WIDTH-1:0] output_write_addr = FINAL_INT32 ? i32_addr : byte_addr;
    wire [ARRAY_COLS*8-1:0] output_write_data = byte_data;
    wire [ARRAY_COLS-1:0] output_write_mask = FINAL_INT32 ? i32_mask : byte_mask;

    conv2d_engine #(.ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD), .OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
        .SPATIAL_TILE(SPATIAL_TILE)) dut (
        .clk(clk), .rst(rst), .start_valid(start_valid), .start_ready(start_ready),
        .input_height(cfg_input_height), .input_width(IW_VALUE), .input_channels(IC_VALUE),
        .output_height(OH_VALUE), .output_width(OW_VALUE), .output_channels(OC_VALUE),
        .kernel_height(8'd3), .kernel_width(8'd3), .stride_height(8'd1),
        .stride_width(8'd1), .pad_top(PAD_VALUE), .pad_left(PAD_VALUE),
        .activation_base(16'd0), .weight_base(16'd0), .output_base(16'd0),
        .output_offset(32'sd0), .activation_min(8'sh80), .activation_max(8'sh7f),
        .final_output_int32(FINAL_INT32 != 0),
        .busy(busy), .done(done), .activation_read_addr(activation_read_addr),
        .activation_read_data(activation_read_data),
        .weight_read_addr(weight_read_addr), .weight_read_data(weight_read_data),
        .parameter_tile_addr(parameter_tile_addr), .bias_read_data(bias_read_data),
        .multiplier_read_data(multiplier_read_data), .shift_read_data(shift_read_data),
        .output_write_valid(byte_valid),
        .output_write_ready(output_write_ready), .output_write_addr(byte_addr),
        .output_write_data(byte_data), .output_write_mask(byte_mask),
        .int32_write_valid(i32_valid), .int32_write_ready(output_write_ready), .int32_write_addr(i32_addr),
        .int32_write_data(i32_data), .int32_write_mask(i32_mask)
    );

    integer expected [0:OH*OW*OC-1];
    integer y, x, c, ci, ky, kx, iy, ix, sum, index, tile, lane, windex;
    integer writes = 0, cycles = 0, stalls = 0;
    integer activation_fires=0, padding_fires=0, weight_fires=0;
    integer issues=0, retires=0, peak=0, credit=0, simultaneous=0;
    integer job_start, job, matrix_stalls=0;
    reg checking=0;
    reg seen [0:OH*OW*OC-1];
    reg write_held=0, input_held=0, result_held=0;
    reg [ADDR_WIDTH-1:0] held_addr;
    reg [ARRAY_COLS*8-1:0] held_write;
    reg [ARRAY_COLS*32-1:0] held_i32;
    reg [ARRAY_COLS-1:0] held_mask;
    reg [ARRAY_ROWS*8-1:0] held_act;
    reg [ARRAY_COLS*32-1:0] held_psum, held_result;
    // Test-only forced retirement stalls exercise Matrix's global CE and
    // stable inputs at full credit. Production output_ready stays independent.
    reg retire_enable=1;
    always @(negedge clk) begin
        retire_enable = !rst && dut.inflight != 0 && (cycles%47 >= 25);
    end
    initial if (TEST_LIFECYCLE && OPT_SPATIAL_TILE && TEST_LAYER == 2 &&
                FALLBACK_PAD == 1 && ARRAY_ROWS == 4 && ARRAY_COLS == 8 && !FINAL_INT32)
        force dut.matrix_out_ready = retire_enable;

    always @(posedge clk) begin
        if (rst) begin
            credit=0; write_held=0; input_held=0; result_held=0;
        end else begin
            if (dut.perf_inflight !== credit) $fatal(1,"inflight credit mismatch got=%0d expected=%0d",dut.perf_inflight,credit);
            if (dut.perf_inflight > 16) $fatal(1,"tag FIFO overflow");
            if (dut.matrix_weight_start_valid && (!dut.matrix_idle || credit != 0))
                $fatal(1,"weight barrier violated");
            if (write_held && (!output_write_valid || held_addr !== output_write_addr ||
                (FINAL_INT32 ? held_i32 !== i32_data : held_write !== output_write_data) || held_mask !== output_write_mask))
                $fatal(1,"output unstable under backpressure");
            if (input_held && (!dut.matrix_in_valid || held_act !== dut.matrix_input_act ||
                held_psum !== dut.matrix_input_psum)) $fatal(1,"Matrix issue unstable");
            if (result_held && (!dut.matrix_out_valid || held_result !== dut.matrix_out_data))
                $fatal(1,"Matrix result unstable");
            write_held=output_write_valid && !output_write_ready;
            held_addr=output_write_addr; held_write=output_write_data; held_i32=i32_data; held_mask=output_write_mask;
            input_held=dut.matrix_in_valid && !dut.matrix_in_ready;
            held_act=dut.matrix_input_act; held_psum=dut.matrix_input_psum;
            result_held=dut.matrix_out_valid && !dut.matrix_out_ready;
            held_result=dut.matrix_out_data;
            if (dut.perf_matrix_retire && credit == 0) $fatal(1,"untagged retirement");
            credit=credit+dut.perf_matrix_issue-dut.perf_matrix_retire;
            if(checking) begin
                activation_fires=activation_fires+dut.perf_activation_fire;
                padding_fires=padding_fires+dut.perf_activation_padding;
                weight_fires=weight_fires+dut.perf_weight_fire;
                issues=issues+dut.perf_matrix_issue;
                retires=retires+dut.perf_matrix_retire;
                if (credit>peak) peak=credit;
                if(dut.perf_matrix_issue && dut.perf_matrix_retire) simultaneous=simultaneous+1;
                if(result_held) matrix_stalls=matrix_stalls+1;
            end
        end
    end

    always @(posedge clk) begin
        cycles = cycles + 1;
        if (cycles > 200000) $fatal(1, "conv engine timeout");
        if (!rst && output_write_valid && !output_write_ready) stalls = stalls + 1;
        if (!rst && checking && output_write_valid && output_write_ready) begin
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1) begin
                index = output_write_addr + lane;
                if (output_write_mask[lane]) begin
                    if (index < 0 || index >= OH*OW*OC) $fatal(1,"write outside output");
                    if (seen[index]) $fatal(1,"duplicate output index %0d", index);
                    seen[index]=1;
                    if ((FINAL_INT32 ? $signed(i32_data[lane*32 +: 32]) : $signed(output_write_data[lane*8 +: 8])) !== expected[index])
                        $fatal(1, "output index=%0d expected=%0d actual=%0d",
                               index, expected[index],
                               $signed(output_write_data[lane*8 +: 8]));
                end
            end
            writes = writes + 1;
        end
    end

    initial begin
        // Deterministic small values exercise positive/negative outputs and make the
        // identity Q31 requantization (multiplier=0.5, shift=+1) exact.
        for (index = 0; index < IH*IW*IC; index = index + 1)
            activation_mem[index] = (index % 9) - 4;
        for (tile = 0; tile < (OC+ARRAY_COLS-1)/ARRAY_COLS; tile = tile + 1) begin
            bias_mem[tile] = 0;
            multiplier_mem[tile] = 0;
            shift_mem[tile] = 0;
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1) begin
                bias_mem[tile][lane*32 +: 32] = lane - 3;
                multiplier_mem[tile][lane*32 +: 32] = 32'sh40000000;
                shift_mem[tile][lane*SHIFT_WIDTH +: SHIFT_WIDTH] = 1;
            end
        end
        for (windex = 0; windex < K*((OC+ARRAY_COLS-1)/ARRAY_COLS); windex = windex + 1) begin
            weight_mem[windex] = 0;
            for (lane = 0; lane < ARRAY_COLS; lane = lane + 1)
                weight_mem[windex][lane*8 +: 8] = ((windex*7 + lane*3) % 5) - 2;
        end

        for (y = 0; y < OH; y = y + 1)
            for (x = 0; x < OW; x = x + 1)
                for (c = 0; c < OC; c = c + 1) begin
                    tile = c / ARRAY_COLS;
                    lane = c % ARRAY_COLS;
                    sum = lane - 3;
                    for (ky = 0; ky < 3; ky = ky + 1)
                        for (kx = 0; kx < 3; kx = kx + 1)
                            for (ci = 0; ci < IC; ci = ci + 1) begin
                                iy = y + ky - FALLBACK_PAD;
                                ix = x + kx - FALLBACK_PAD;
                                if (iy >= 0 && ix >= 0 && iy < IH && ix < IW)
                                    sum = sum +
                                        $signed(activation_mem[(iy*IW+ix)*IC+ci]) *
                                        $signed(weight_mem[((ky*3+kx)*IC+ci)*
                                            ((OC+ARRAY_COLS-1)/ARRAY_COLS)+tile]
                                            [lane*8 +: 8]);
                            end
                    if (!FINAL_INT32 && sum > 127) sum = 127;
                    if (!FINAL_INT32 && sum < -128) sum = -128;
                    expected[(y*OW+x)*OC+c] = sum;
                end

        repeat (3) @(posedge clk);
        @(negedge clk); rst = 0; cfg_input_height = 0; start_valid = 1;
        repeat(3)begin
            @(posedge clk);
            if(start_ready||busy||done)$fatal(1,"invalid descriptor was accepted");
        end
        @(negedge clk); start_valid = 0; cfg_input_height = IH_VALUE;
        if (TEST_LIFECYCLE) begin
            // Abort an in-flight job, then ensure stale tags/data cannot leak.
            @(negedge clk); start_valid=1;
            @(negedge clk); start_valid=0;
            wait(dut.perf_inflight != 0);
            @(negedge clk); rst=1;
            repeat(3) @(negedge clk);
            if(busy || done || dut.perf_inflight != 0) $fatal(1,"reset busy lifecycle");
            rst=0;
        end
        for(job=0; job<(TEST_LIFECYCLE ? 2 : 1); job=job+1) begin
            writes=0; stalls=0; activation_fires=0; padding_fires=0;
            weight_fires=0; issues=0; retires=0; peak=0; simultaneous=0; matrix_stalls=0;
            for(index=0; index<OH*OW*OC; index=index+1) seen[index]=0;
            checking=1; job_start=cycles;
            @(negedge clk); start_valid = 1;
            @(posedge clk); while (!start_ready) @(posedge clk);
            @(negedge clk); start_valid = 0;
            while (!done) begin
                @(negedge clk);
                output_write_ready = (cycles%13 < 7);
                @(posedge clk);
            end
            @(negedge clk); output_write_ready = 1;
            if (busy || dut.perf_inflight != 0) $fatal(1,"busy/credit after completion");
            if (writes != OH*OW*((OC+ARRAY_COLS-1)/ARRAY_COLS))
                $fatal(1,"write count expected=%0d actual=%0d",OH*OW*((OC+ARRAY_COLS-1)/ARRAY_COLS),writes);
            for(index=0; index<OH*OW*OC; index=index+1)
                if(!seen[index]) $fatal(1,"missing output %0d",index);
            if(stalls==0) $fatal(1,"missing output backpressure coverage");
            if(issues != OH*OW*((OC+ARRAY_COLS-1)/ARRAY_COLS)*((K+ARRAY_ROWS-1)/ARRAY_ROWS) || issues != retires)
                $fatal(1,"Matrix event counts issues=%0d retires=%0d",issues,retires);
            if(activation_fires != OH*OW*K*((OC+ARRAY_COLS-1)/ARRAY_COLS))
                $fatal(1,"activation count %0d",activation_fires);
            if(OPT_SPATIAL_TILE && TEST_LAYER==2 && ARRAY_ROWS==4 && ARRAY_COLS==8 && FALLBACK_PAD==1 && !FINAL_INT32) begin
                if(weight_fires != ((80+SPATIAL_TILE-1)/SPATIAL_TILE)*72 ||
                   (SPATIAL_TILE > 1 && peak<=1))
                    $fatal(1,"tile weight reuse/peak count weights=%0d peak=%0d",weight_fires,peak);
                if(TEST_LIFECYCLE && (matrix_stalls==0 || simultaneous==0))
                    $fatal(1,"missing matrix backpressure/issue+retire coverage");
            end else if(weight_fires != issues*ARRAY_ROWS)
                $fatal(1,"fallback weight count %0d",weight_fires);
            $display("ALL CONV2D ENGINE TESTS PASSED layer=%0d rows=%0d cols=%0d gather=%0d tile=%0d size=%0d pad=%0d job=%0d writes=%0d cycles=%0d stalls=%0d activation=%0d padding=%0d weights=%0d issues=%0d retires=%0d peak=%0d simultaneous=%0d matrix_stalls=%0d",
                TEST_LAYER,ARRAY_ROWS,ARRAY_COLS,OPT_GATHER_LOAD,OPT_SPATIAL_TILE,SPATIAL_TILE,FALLBACK_PAD,job,writes,cycles-job_start,stalls,activation_fires,padding_fires,weight_fires,issues,retires,peak,simultaneous,matrix_stalls);
            checking=0;
            repeat(3) @(negedge clk);
            if(done) $fatal(1,"done not pulse");
        end
        $finish;
    end
endmodule
