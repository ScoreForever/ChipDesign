`timescale 1ns/1ps
// File-backed end-to-end MMIO test. Only the observer uses hierarchy; all
// model/input loading and result/profile reads go through the real MMIO ABI.
module tb_tinycnn8_mmio_fileio #(
    parameter OPT_GATHER_LOAD=0, OPT_SPATIAL_TILE=0, SPATIAL_TILE=16
);
    reg clk=0,rst_ni=0,req=0,we=0;
    reg [31:0] addr=0,wdata=0;
    wire [31:0] rdata;
    wire irq;
    always #5 clk=~clk;
    tinycnn8_npu_mmio_wrapper #(.OPT_GATHER_LOAD(OPT_GATHER_LOAD),
        .OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),.SPATIAL_TILE(SPATIAL_TILE)) dut(
        .clk_i(clk),.rst_ni(rst_ni),.req_i(req),.we_i(we),.addr_i(addr),
        .wdata_i(wdata),.rdata_o(rdata),.irq_o(irq),
        .dma_src_o(),.dma_dst_o(),.dma_len_o(),.dma_start_o(),
        .dma_irq_en_o(),.dma_clear_done_o(),.dma_busy_i(1'b0),.dma_done_i(1'b0));
    reg [7:0] input_image[0:319],shift_image[0:31];
    reg [63:0] weight_image[0:255];
    reg [31:0] bias_image[0:31],mult_image[0:31],golden[0:7];
    string data_dir,out_dir,data_dir2;
    integer classes=6,cycle=0,accepted_writes=0;
    integer load_cycles,load_writes,load_begin,write_begin;
    integer perf_fd,logit_fd,job,index,lane,group;
    integer mon_total=0,mon_layer[0:5],mon_weights=0,mon_issues=0,mon_retires=0;
    integer mon_phase=0,mon_inflight=0,mon_peak=0;
    reg mon_active=0;
    reg [31:0] value,profile[0:11],snapshot[0:11];
    // Independent observer derives layer transitions from engine handshakes,
    // never from sequencer state or any hardware profile counter/event port.
    always @(posedge clk) begin
        cycle=cycle+1;
        if(rst_ni && dut.npu_start_pulse && dut.npu_start_ready)begin
            mon_active=1;mon_phase=0;mon_total=0;mon_weights=0;
            mon_issues=0;mon_retires=0;mon_inflight=0;mon_peak=0;
            for(integer i=0;i<6;i=i+1)mon_layer[i]=0;
        end else if(mon_active)begin
            mon_total=mon_total+1;mon_layer[mon_phase]=mon_layer[mon_phase]+1;
            if(dut.i_tinycnn8_npu.perf_inflight!==mon_inflight[7:0])
                $fatal(1,"live inflight mismatch expected=%0d actual=%0d",mon_inflight,
                    dut.i_tinycnn8_npu.perf_inflight);
            if(mon_inflight>mon_peak)mon_peak=mon_inflight;
            if(dut.i_tinycnn8_npu.conv.matrix_weight_valid &&
               dut.i_tinycnn8_npu.conv.matrix_weight_ready)mon_weights=mon_weights+1;
            if(dut.i_tinycnn8_npu.conv.matrix_in_valid &&
               dut.i_tinycnn8_npu.conv.matrix_in_ready)begin
                mon_issues=mon_issues+1;mon_inflight=mon_inflight+1;
            end
            if(dut.i_tinycnn8_npu.conv.matrix_out_valid &&
               dut.i_tinycnn8_npu.conv.matrix_out_ready)begin
                mon_retires=mon_retires+1;mon_inflight=mon_inflight-1;
            end
            if(mon_inflight<0)$fatal(1,"negative matrix inflight");
            case(mon_phase)
                0,2:if(dut.i_tinycnn8_npu.conv_done)mon_phase=mon_phase+1;
                1,3:if(dut.i_tinycnn8_npu.pool_done)mon_phase=mon_phase+1;
                4:if(dut.i_tinycnn8_npu.gap_done)mon_phase=5;
                5:if(dut.i_tinycnn8_npu.conv_done)mon_active=0;
            endcase
        end
    end
    task mmio_write(input [15:0] offset,input [31:0] val);
        begin
            @(negedge clk);req=1;we=1;addr={16'h7000,offset};wdata=val;
            @(negedge clk);req=0;we=0;
        end
    endtask
    task mmio_read(input [15:0] offset,output [31:0] val);
        begin
            @(negedge clk);req=1;we=0;addr={16'h7000,offset};
            @(negedge clk);val=rdata;req=0;
        end
    endtask
    task load_write(input [15:0] offset,input [31:0] val);
        begin mmio_write(offset,val);accepted_writes=accepted_writes+1;end
    endtask
    task read_images(input string dir);
        begin
            for(integer i=0;i<8;i=i+1)golden[i]=0;
            $readmemh({dir,"/input.hex"},input_image);
            $readmemh({dir,"/weights.hex"},weight_image);
            $readmemh({dir,"/bias.hex"},bias_image);
            $readmemh({dir,"/multiplier.hex"},mult_image);
            $readmemh({dir,"/shift.hex"},shift_image);
            $readmemh({dir,"/golden_fc.hex"},golden,0,classes-1);
            for(integer i=0;i<24;i=i+1)
                if(shift_image[i][5:0]==6'b100000)$fatal(1,"golden model has reserved shift -32");
        end
    endtask
    task load_model(input bit full_model);
        begin
            load_begin=cycle;write_begin=accepted_writes;
            for(integer i=0;i<320;i=i+1)load_write(16'h1000+i*4,{24'd0,input_image[i]});
            if(full_model)begin
                for(integer i=0;i<256;i=i+1)begin
                    load_write(16'h2000+i*8,weight_image[i][31:0]);
                    load_write(16'h2004+i*8,weight_image[i][63:32]);
                end
                for(integer g=0;g<4;g=g+1)begin
                    for(integer l=0;l<8;l=l+1)begin
                        load_write(16'h3000+g*256+l*4,bias_image[g*8+l]);
                        load_write(16'h3020+g*256+l*4,mult_image[g*8+l]);
                        load_write(16'h3040+g*256+l*4,
                            {{24{shift_image[g*8+l][7]}},shift_image[g*8+l]});
                    end
                    load_write(16'h3060+g*256,1);
                end
            end
            // Drain the wrapper's registered native-host write before start.
            @(negedge clk);
            load_cycles=cycle-load_begin;load_writes=accepted_writes-write_begin;
            mmio_read(16'h000c,value);
            if(value!==0)$fatal(1,"load error %0d",value);
            if(load_writes!=(full_model?932:320))$fatal(1,"load transaction count mismatch");
        end
    endtask
    task read_profile;
        begin for(integer i=0;i<12;i=i+1)mmio_read(16'h0040+i*4,profile[i]);end
    endtask
    task expect_error(input [31:0] code);
        begin
            mmio_read(16'h000c,value);
            if(value!==code)$fatal(1,"error expected=%0d actual=%0d",code,value);
            mmio_write(0,8);
        end
    endtask
    task run_job(input integer number,input bit irq_enabled);
        integer timeout_count,sum_layers;
        begin
            mmio_write(0,irq_enabled?3:1);
            // Illegal busy start and input write must not restart or clear profile.
            mmio_write(0,irq_enabled?3:1);
            mmio_read(16'h000c,value);
            if(value!==2)$fatal(1,"busy start did not fail");
            mmio_write(16'h1000,32'hff);
            mmio_read(16'h000c,value);
            if(value!==2)$fatal(1,"busy load did not fail");
            mmio_write(0,irq_enabled?10:8);
            timeout_count=0;value=0;
            while(!value[2])begin
                mmio_read(4,value);timeout_count=timeout_count+1;
                if(timeout_count>300000)$fatal(1,"MMIO job timeout");
            end
            if(value[1]||value[3])$fatal(1,"bad completion status %h",value);
            if(irq!==irq_enabled)$fatal(1,"IRQ mask mismatch");
            read_profile();
            if(profile[11]!==1)$fatal(1,"profile invalid/overflow/active %h",profile[11]);
            if(profile[0]!==mon_total || profile[7]!==mon_weights ||
               profile[8]!==mon_issues || profile[9]!==mon_retires ||
               profile[10]!==mon_peak)$fatal(1,"profile handshake mismatch total %0d/%0d weight %0d/%0d issue %0d/%0d retire %0d/%0d peak %0d/%0d",
               profile[0],mon_total,profile[7],mon_weights,profile[8],mon_issues,
               profile[9],mon_retires,profile[10],mon_peak);
            sum_layers=0;
            for(integer i=0;i<6;i=i+1)begin
                if(profile[i+1]!==mon_layer[i])$fatal(1,"layer %0d cycles mismatch",i);
                sum_layers=sum_layers+profile[i+1];
            end
            if(sum_layers!=profile[0] || mon_issues!=mon_retires || mon_inflight!=0)
                $fatal(1,"cycle conservation or matrix drain mismatch");
            for(integer i=0;i<8;i=i+1)begin
                mmio_read(16'h0010+i*4,value);
                if(value!==(i<classes?golden[i]:32'd0))$fatal(1,"job %0d logit %0d got %h expected %h",number,i,value,golden[i]);
                $fdisplay(logit_fd,"%0d,%0d,%0d,%0d",number,i,$signed(value),$signed(golden[i]));
            end
            $fdisplay(perf_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                number,profile[0],profile[1],profile[2],profile[3],profile[4],
                profile[5],profile[6],profile[7],profile[8],profile[9],profile[10],
                load_writes,accepted_writes,load_cycles);
            for(integer i=0;i<12;i=i+1)snapshot[i]=profile[i];
            // Enabling a previously masked completed IRQ must assert it.
            mmio_write(0,2);if(!irq)$fatal(1,"pending done IRQ did not unmask");
            mmio_write(0,6);if(irq)$fatal(1,"done clear did not clear IRQ");
            mmio_read(4,value);if(value[2])$fatal(1,"done latch retained");
            repeat(5)@(negedge clk);
            read_profile();
            for(integer i=0;i<12;i=i+1)
                if(profile[i]!==snapshot[i])$fatal(1,"clear_done changed profile %0d",i);
            mmio_write(16'h0040,32'hdeadbeef);expect_error(6);
            read_profile();
            for(integer i=0;i<12;i=i+1)
                if(profile[i]!==snapshot[i])$fatal(1,"readonly write changed profile");
        end
    endtask
    initial begin
        if(!$value$plusargs("DATA_DIR=%s",data_dir))$fatal(1,"missing DATA_DIR");
        if(!$value$plusargs("OUT_DIR=%s",out_dir))$fatal(1,"missing OUT_DIR");
        if($value$plusargs("CLASSES=%d",classes))begin end
        if(classes<1||classes>8)$fatal(1,"invalid CLASSES");
        if(!$value$plusargs("DATA_DIR2=%s",data_dir2))data_dir2=data_dir;
        perf_fd=$fopen({out_dir,"/mmio_perf.csv"},"w");
        logit_fd=$fopen({out_dir,"/mmio_logits.csv"},"w");
        if(!perf_fd||!logit_fd)$fatal(1,"cannot open result files");
        $fdisplay(perf_fd,"job,total_cycles,conv1_cycles,pool1_cycles,conv2_cycles,pool2_cycles,gap_cycles,fc_cycles,weight_rows,matrix_issues,matrix_retires,peak_inflight,jobacceptedwrites,totalacceptedwrites,hostloadcycles");
        $fdisplay(logit_fd,"job,class,actual,golden");
        repeat(4)@(negedge clk);rst_ni=1;
        read_profile();for(integer i=0;i<12;i=i+1)
            if(profile[i]!==0)$fatal(1,"reset profile nonzero");
        mmio_write(8,0);expect_error(1);
        mmio_write(8,9);expect_error(1);
        mmio_write(8,classes);
        mmio_write(16'h3040,32'hffffffe0);mmio_write(16'h3060,1);expect_error(5);
        read_images(data_dir);load_model(1);run_job(0,0);
        load_model(0);run_job(1,1);
        read_images(data_dir2);load_model(1);run_job(2,1);
        $fclose(perf_fd);$fclose(logit_fd);
        $display("PASS MMIO fileio opt=%0d tile=%0d classes=%0d writes=%0d",OPT_GATHER_LOAD,OPT_SPATIAL_TILE,classes,accepted_writes);
        $finish;
    end
endmodule
