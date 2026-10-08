`timescale 1ns/1ps
module tb_tinycnn8_fileio;
    parameter OPT_GATHER_LOAD=0,OPT_SPATIAL_TILE=0,SPATIAL_TILE=16;
    localparam LANES=8,SHIFT_WIDTH=6;
    reg clk=0,rst=1,start_valid=0;
    always #5 clk=~clk;
    reg [3:0] class_count=6;
    wire start_ready,busy,done;
    wire [255:0] logits;
    reg host_activation_we=0,host_weight_we=0,host_parameter_we=0;
    reg [15:0] host_activation_addr=0,host_weight_addr=0;
    reg [7:0] host_activation_data=0,host_parameter_tile=0;
    reg [1:0] host_parameter_layer=0;
    reg [63:0] host_weight_data=0;
    wire [31:0] perf_total_cycles,perf_weight_rows,perf_matrix_issues,perf_matrix_retires,perf_peak_inflight;
    wire [191:0] perf_layer_cycles;
    wire perf_valid,perf_overflow;
    integer hw_cycles[0:5],hw_total=0,hw_peak=0,hw_layer,hw_inflight=0;
    reg [255:0] host_bias_data=0,host_multiplier_data=0;
    reg [47:0] host_shift_data=0;

    tinycnn8_npu_top #(.ARRAY_ROWS(4),.ARRAY_COLS(8),
        .OPT_GATHER_LOAD(OPT_GATHER_LOAD),.OPT_SPATIAL_TILE(OPT_SPATIAL_TILE),
        .SPATIAL_TILE(SPATIAL_TILE)) dut(
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

    reg [7:0] input_image[0:319];
    reg [63:0] weight_image[0:255];
    reg [31:0] bias_image[0:31],mult_image[0:31];
    reg [7:0] shift_image[0:31];
    string data_dir,out_dir,path;
    integer event_fd,output_fd,perf_fd,state_fd;
    integer cycle=0,job_start=-1,layer_active=-1,start_cycle[0:5];
    integer layer_cycles[0:5],output_count[0:5];
    integer gather_cycles[0:5],weight_cycles[0:5],wait_cycles[0:5];
    integer joint_cycles[0:5],window_items[0:5],padding_items[0:5];
    integer activation_bytes[0:5],weight_bytes[0:5];
    integer weight_rows[0:5],matrix_inputs[0:5],matrix_outputs[0:5];
    integer useful_mac[0:5],state_cycles[0:5][0:255];
    integer classes=6,index,lane,layer,job_cycles,total_layer_cycles;
    integer wave_enabled=0,wave_dumping=0,wave_begin,wave_end;
    integer protocol_joint=0;

    task event_record;
        input integer current_layer;
        input string event_name;
        input integer address,mask;
        input [255:0] data;
        begin
            $fdisplay(event_fd,"%0d,%0d,%s,%0d,%0h,%064h",cycle,current_layer,
                event_name,address,mask,data);
        end
    endtask

    task begin_layer;
        input integer current_layer;
        begin
            if(layer_active!=-1)$fatal(1,"overlapping layer execution");
            layer_active=current_layer;start_cycle[current_layer]=cycle;
            event_record(current_layer,"start",0,0,0);
        end
    endtask

    task end_layer;
        input integer current_layer;
        begin
            if(layer_active!=current_layer)$fatal(1,"unexpected layer completion");
            layer_cycles[current_layer]=cycle-start_cycle[current_layer];
            event_record(current_layer,"done",0,0,0);layer_active=-1;
        end
    endtask

    task record_output;
        input integer current_layer,address,mask,is_int32;
        input [255:0] data;
        integer out_lane,signed_value;
        begin
            if((^mask)===1'bx || (^address)===1'bx)$fatal(1,"unknown output address/mask");
            event_record(current_layer,"write",address,mask,data);
            for(out_lane=0;out_lane<LANES;out_lane=out_lane+1)
                if(mask&(1<<out_lane))begin
                    if(is_int32)begin
                        if((^data[out_lane*32 +: 32])===1'bx)$fatal(1,"unknown INT32 output");
                        signed_value=$signed(data[out_lane*32 +: 32]);
                    end else begin
                        if((^data[out_lane*8 +: 8])===1'bx)$fatal(1,"unknown INT8 output");
                        signed_value=$signed(data[out_lane*8 +: 8]);
                    end
                    $fdisplay(output_fd,"%0d,%0d,%0d,%0d",cycle,current_layer,
                        address+out_lane,signed_value);
                    output_count[current_layer]=output_count[current_layer]+1;
                end
        end
    endtask

    always @(posedge clk) begin
        cycle=cycle+1;
        if(!rst)begin
            hw_layer=-1;
            case(dut.state)
                dut.C1_START,dut.C1_WAIT: hw_layer=0;
                dut.P1_START,dut.P1_WAIT: hw_layer=1;
                dut.C2_START,dut.C2_WAIT: hw_layer=2;
                dut.P2_START,dut.P2_WAIT: hw_layer=3;
                dut.FC_START,dut.FC_WAIT: hw_layer=5;
            endcase
            if(hw_layer>=0)begin
                hw_total=hw_total+1;hw_cycles[hw_layer]=hw_cycles[hw_layer]+1;
                if(dut.conv.perf_inflight!==hw_inflight[7:0])$fatal(1,"inflight disagrees with independent handshakes");
                if(hw_inflight>hw_peak)hw_peak=hw_inflight;
                if(dut.conv.matrix_in_valid && dut.conv.matrix_in_ready)hw_inflight=hw_inflight+1;
                if(dut.conv.matrix_out_valid && dut.conv.matrix_out_ready)hw_inflight=hw_inflight-1;
                if(hw_inflight<0)$fatal(1,"negative inflight");
            end
            if(start_valid&&start_ready)begin
                job_start=cycle;event_record(-1,"job_start",0,0,0);
            end
            if(dut.conv_start_valid&&dut.conv_start_ready)begin
                if(dut.conv_phase==0)begin_layer(0);
                else if(dut.conv_phase==1)begin_layer(2);
                else begin_layer(5);
            end
            if(dut.pool_start_valid&&dut.pool_start_ready)begin
                if(dut.state==dut.P1_START)begin_layer(1);else begin_layer(3);
            end
            if(layer_active>=0)begin
                if(dut.conv_busy&&(layer_active==0||layer_active==2||layer_active==5))begin
                    state_cycles[layer_active][dut.conv.state]=
                        state_cycles[layer_active][dut.conv.state]+1;
                    if(dut.conv.perf_activation_fire)begin
                        gather_cycles[layer_active]=gather_cycles[layer_active]+1;
                        window_items[layer_active]=window_items[layer_active]+1;
                        if(dut.conv.perf_activation_padding)
                            padding_items[layer_active]=padding_items[layer_active]+1;
                        else begin
                            activation_bytes[layer_active]=activation_bytes[layer_active]+1;
                            if(layer_active==5)useful_mac[layer_active]=useful_mac[layer_active]+classes;
                            else useful_mac[layer_active]=useful_mac[layer_active]+8;
                        end
                        event_record(layer_active,"activation",dut.conv.activation_read_addr,
                            dut.conv.perf_activation_padding?0:1,{248'd0,dut.conv.activation_read_data});
                    end
                    // Baseline C1 reads weights on window gather; tiled C2 consumes on weight fire.
                    if((layer_active!=2 || !OPT_SPATIAL_TILE) && dut.conv.window_item_fire)
                        weight_bytes[layer_active]=weight_bytes[layer_active]+8;
                    if(layer_active==2 && OPT_SPATIAL_TILE && dut.conv.perf_weight_fire)
                        weight_bytes[layer_active]=weight_bytes[layer_active]+8;
                    if(dut.conv.matrix_weight_start_valid&&dut.conv.matrix_weight_start_ready)
                        event_record(layer_active,"weight_start",0,0,0);
                    if(dut.conv.perf_weight_fire)begin
                        weight_cycles[layer_active]=weight_cycles[layer_active]+1;
                        weight_rows[layer_active]=weight_rows[layer_active]+1;
                        event_record(layer_active,"weight",0,8'hff,
                            {192'd0,dut.conv.matrix.weight_data});
                        if(dut.conv.perf_activation_fire)joint_cycles[layer_active]=joint_cycles[layer_active]+1;
                    end
                    if(dut.conv.perf_matrix_issue)begin
                        matrix_inputs[layer_active]=matrix_inputs[layer_active]+1;
                        event_record(layer_active,"matrix_in",0,0,
                            {224'd0,dut.conv.activation_pack});
                    end
                    if(dut.conv.perf_matrix_retire)begin
                        matrix_outputs[layer_active]=matrix_outputs[layer_active]+1;
                        event_record(layer_active,"matrix_out",0,8'hff,dut.conv.matrix_out_data);
                    end
                end
                if(dut.pool.vu_in_valid&&dut.pool.vu_in_ready&&dut.pool.state==1)begin
                    activation_bytes[layer_active]=activation_bytes[layer_active]+8;
                    event_record(layer_active,"pool_read",dut.pool_act_addr,8'hff,
                        {192'd0,dut.pool_act_data});
                end
                if(dut.conv_write_valid)record_output(layer_active,dut.conv_write_addr,
                    dut.conv_write_mask,0,{192'd0,dut.conv_write_data});
                if(dut.pool_write_valid)record_output(layer_active,dut.pool_write_addr,
                    dut.pool_write_mask,0,{192'd0,dut.pool_write_data});
                if(dut.conv_i32_valid)record_output(layer_active,dut.conv_i32_addr,
                    dut.conv_i32_mask,1,dut.conv_i32_data);
            end
            if(dut.conv_done)begin
                if(dut.conv_phase==0)end_layer(0);
                else if(dut.conv_phase==1)end_layer(2);
                else end_layer(5);
            end
            if(dut.pool_done)begin
                if(dut.state==dut.P1_WAIT)end_layer(1);else end_layer(3);
            end
            if(wave_enabled)begin
                if(layer_active==2&&wave_dumping==0)begin
                    wave_begin=cycle;wave_end=cycle+180;wave_dumping=1;$dumpon;
                end
                if(wave_dumping==1&&cycle>=wave_end)begin wave_dumping=2;$dumpoff;end
            end
        end
    end

    initial begin
        if(!$value$plusargs("DATA_DIR=%s",data_dir))$fatal(1,"missing DATA_DIR");
        if(!$value$plusargs("OUT_DIR=%s",out_dir))$fatal(1,"missing OUT_DIR");
        if($value$plusargs("CLASSES=%d",classes))begin end
        if(classes<1||classes>8)$fatal(1,"invalid classes");
        class_count=classes;
        for(layer=0;layer<6;layer=layer+1)begin
            start_cycle[layer]=-1;layer_cycles[layer]=0;output_count[layer]=0;
            gather_cycles[layer]=0;weight_cycles[layer]=0;wait_cycles[layer]=0;
            joint_cycles[layer]=0;window_items[layer]=0;padding_items[layer]=0;
            activation_bytes[layer]=0;weight_bytes[layer]=0;
            weight_rows[layer]=0;matrix_inputs[layer]=0;matrix_outputs[layer]=0;
            useful_mac[layer]=0;hw_cycles[layer]=0;
            for(index=0;index<256;index=index+1)state_cycles[layer][index]=0;
        end
        event_fd=$fopen({out_dir,"/trace.csv"},"w");
        output_fd=$fopen({out_dir,"/outputs.csv"},"w");
        perf_fd=$fopen({out_dir,"/perf.csv"},"w");
        state_fd=$fopen({out_dir,"/states.csv"},"w");
        if(!event_fd||!output_fd||!perf_fd||!state_fd)$fatal(1,"could not open result files");
        $fdisplay(event_fd,"cycle,layer,event,addr,mask,data_hex");
        $fdisplay(output_fd,"cycle,layer,index,value");
        $fdisplay(perf_fd,"layer,cycles,gather_cycles,weight_cycles,wait_cycles,joint_cycles,window_items,padding_items,weight_rows,matrix_inputs,matrix_outputs,issued_mac,useful_mac,activation_bytes,weight_bytes,output_bytes");
        $fdisplay(state_fd,"layer,state,cycles");
        $readmemh({data_dir,"/input.hex"},input_image);
        $readmemh({data_dir,"/weights.hex"},weight_image);
        $readmemh({data_dir,"/bias.hex"},bias_image);
        $readmemh({data_dir,"/multiplier.hex"},mult_image);
        $readmemh({data_dir,"/shift.hex"},shift_image);
        if($test$plusargs("WAVE"))begin
            wave_enabled=1;$dumpfile({out_dir,"/conv2_window.vcd"});
            $dumpvars(0,dut.conv);$dumpoff;
        end
        repeat(3)@(negedge clk);rst=0;
        for(index=0;index<320;index=index+1)begin
            @(negedge clk);host_activation_we=1;host_activation_addr=index;
            host_activation_data=input_image[index];
        end
        @(negedge clk);host_activation_we=0;
        for(index=0;index<256;index=index+1)begin
            @(negedge clk);host_weight_we=1;host_weight_addr=index;
            host_weight_data=weight_image[index];
        end
        @(negedge clk);host_weight_we=0;
        for(layer=0;layer<4;layer=layer+1)begin
            if(layer!=2)begin
                @(negedge clk);host_parameter_we=1;host_parameter_layer=layer;
                host_parameter_tile=0;
                for(lane=0;lane<8;lane=lane+1)begin
                    host_bias_data[lane*32 +: 32]=bias_image[layer*8+lane];
                    host_multiplier_data[lane*32 +: 32]=mult_image[layer*8+lane];
                    host_shift_data[lane*6 +: 6]=shift_image[layer*8+lane][5:0];
                end
            end
        end
        @(negedge clk);host_parameter_we=0;start_valid=1;
        @(negedge clk);start_valid=0;
        while(!done)begin
            @(negedge clk);
            if(cycle>300000)$fatal(1,"network timeout");
        end
        job_cycles=cycle-job_start;
        total_layer_cycles=0;
        for(layer=0;layer<6;layer=layer+1)begin
            total_layer_cycles=total_layer_cycles+layer_cycles[layer];
            $fdisplay(perf_fd,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
                layer,layer_cycles[layer],gather_cycles[layer],weight_cycles[layer],
                wait_cycles[layer],joint_cycles[layer],window_items[layer],padding_items[layer],
                weight_rows[layer],matrix_inputs[layer],matrix_outputs[layer],
                matrix_inputs[layer]*32,useful_mac[layer],activation_bytes[layer],
                weight_bytes[layer],output_count[layer]*((layer==5)?4:1));
            for(index=0;index<256;index=index+1)
                $fdisplay(state_fd,"%0d,%0d,%0d",layer,index,state_cycles[layer][index]);
        end
        $fdisplay(perf_fd,"CTRL,%0d,0,0,0,0,0,0,0,0,0,0,0,0,0,0",job_cycles-total_layer_cycles);
        $fdisplay(perf_fd,"TOTAL,%0d,0,0,0,0,0,0,0,0,0,0,0,0,0,0",job_cycles);
        for(lane=classes;lane<8;lane=lane+1)
            if(logits[lane*32 +: 32]!==0)$fatal(1,"invalid FC lane nonzero");
        if(!perf_valid||perf_overflow)$fatal(1,"invalid hardware profile");
        if(perf_total_cycles!==hw_total || perf_peak_inflight!==hw_peak)
            $fatal(1,"hardware total/peak differs from independent monitor");
        for(layer=0;layer<6;layer=layer+1)
            if(perf_layer_cycles[layer*32 +: 32]!==hw_cycles[layer])
                $fatal(1,"hardware layer cycles differ layer=%0d",layer);
        if(perf_weight_rows!==weight_rows[0]+weight_rows[2]+weight_rows[5] ||
           perf_matrix_issues!==matrix_inputs[0]+matrix_inputs[2]+matrix_inputs[5] ||
           perf_matrix_retires!==matrix_outputs[0]+matrix_outputs[2]+matrix_outputs[5])
            $fatal(1,"hardware event counters differ");
        index=$fopen({out_dir,"/hardware_profile.csv"},"w");
        $fdisplay(index,"total_cycles,c1,p1,c2,p2,reserved,fc,weight_rows,issues,retires,peak_inflight,overflow,valid");
        $fdisplay(index,"%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d,%0d",
            perf_total_cycles,hw_cycles[0],hw_cycles[1],hw_cycles[2],hw_cycles[3],hw_cycles[4],hw_cycles[5],
            perf_weight_rows,perf_matrix_issues,perf_matrix_retires,perf_peak_inflight,perf_overflow,perf_valid);
        $fclose(index);
        event_record(-1,"job_done",0,0,logits);
        $fclose(event_fd);$fclose(output_fd);$fclose(perf_fd);$fclose(state_fd);
        $display("PASS fileio opt=%0d classes=%0d job_cycles=%0d outputs=%0d/%0d/%0d/%0d/%0d/%0d",
            OPT_GATHER_LOAD,classes,job_cycles,output_count[0],output_count[1],output_count[2],
            output_count[3],output_count[4],output_count[5]);
        $finish;
    end
endmodule
