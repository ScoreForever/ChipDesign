`timescale 1ns/1ps
// INT8 fully-connected engine reusing matrix_unit. Final outputs remain INT32.
module fc_engine #(
    parameter ARRAY_ROWS=4,
    parameter ARRAY_COLS=8,
    parameter CHANNEL_WIDTH=8,
    parameter ADDR_WIDTH=16
) (
    input wire clk,input wire rst,
    input wire start_valid,output wire start_ready,
    input wire [CHANNEL_WIDTH-1:0] input_channels,output_channels,
    input wire [ADDR_WIDTH-1:0] input_base,weight_base,output_base,
    output wire busy,output reg done,
    output reg [ADDR_WIDTH-1:0] input_read_addr,
    input wire [7:0] input_read_data,
    output reg [ADDR_WIDTH-1:0] weight_read_addr,
    input wire [ARRAY_COLS*8-1:0] weight_read_data,
    output reg [ADDR_WIDTH-1:0] bias_tile_addr,
    input wire [ARRAY_COLS*32-1:0] bias_read_data,
    output reg output_write_valid,input wire output_write_ready,
    output reg [ADDR_WIDTH-1:0] output_write_addr,
    output reg [ARRAY_COLS*32-1:0] output_write_data,
    output reg [ARRAY_COLS-1:0] output_write_mask
);
    localparam PCW=$clog2(ARRAY_ROWS+1);
    localparam [3:0] S_IDLE=0,S_GATHER=1,S_MATRIX_START=2,S_WEIGHT_LOAD=3,
                     S_MATRIX_INPUT=4,S_MATRIX_WAIT=5,S_WRITE=6;
    reg [3:0] state;
    reg [CHANNEL_WIDTH-1:0] cfg_ic,cfg_oc,oc_tile,tile_count;
    reg [ADDR_WIDTH-1:0] cfg_input_base,cfg_weight_base,cfg_output_base;
    reg [CHANNEL_WIDTH-1:0] k_index;
    reg [PCW-1:0] pack_count,weight_load_row;
    reg [ARRAY_ROWS*8-1:0] input_pack;
    reg [ARRAY_COLS*8-1:0] weight_tile[0:ARRAY_ROWS-1];
    reg [ARRAY_COLS*32-1:0] partial_sum;
    reg group_last;
    reg [ARRAY_COLS-1:0] lane_mask;

    wire mw_start_ready,mw_ready,m_in_ready,m_out_valid;
    wire [ARRAY_COLS*32-1:0] m_out_data;
    matrix_unit #(.ACT_WIDTH(8),.WGT_WIDTH(8),.ACC_WIDTH(32),
        .ARRAY_ROWS(ARRAY_ROWS),.ARRAY_COLS(ARRAY_COLS)) matrix(
        .clk(clk),.rst(rst),.weight_start_valid(state==S_MATRIX_START),
        .weight_start_ready(mw_start_ready),.weight_valid(state==S_WEIGHT_LOAD),
        .weight_ready(mw_ready),.weight_data(weight_tile[weight_load_row]),
        .weights_loaded(),.idle(),.in_valid(state==S_MATRIX_INPUT),
        .in_ready(m_in_ready),.in_act_data(input_pack),
        .in_psum_data(partial_sum),.out_valid(m_out_valid),
        .out_ready(state==S_MATRIX_WAIT),.out_psum_data(m_out_data));

    integer row,lane;
    integer unsigned weight_addr_calc;
    wire descriptor_valid=(input_channels!=0)&&(output_channels!=0);
    assign start_ready=(state==S_IDLE)&&!rst&&descriptor_valid;
    assign busy=(state!=S_IDLE);
    always @* begin
        input_read_addr=cfg_input_base+k_index;
        weight_addr_calc=(k_index*tile_count)+oc_tile;
        weight_read_addr=cfg_weight_base+weight_addr_calc[ADDR_WIDTH-1:0];
        bias_tile_addr=oc_tile;
        output_write_valid=(state==S_WRITE);
        output_write_addr=cfg_output_base+oc_tile*ARRAY_COLS;
        output_write_data=partial_sum;
        output_write_mask=lane_mask;
    end

    always @(posedge clk) begin
        if(rst)begin
            state<=S_IDLE;done<=0;oc_tile<=0;tile_count<=0;k_index<=0;
            pack_count<=0;weight_load_row<=0;input_pack<=0;partial_sum<=0;
            group_last<=0;lane_mask<=0;
            for(row=0;row<ARRAY_ROWS;row=row+1)weight_tile[row]<=0;
        end else begin
            done<=0;
            case(state)
                S_IDLE:if(start_valid&&start_ready)begin
                    cfg_ic<=input_channels;cfg_oc<=output_channels;
                    cfg_input_base<=input_base;cfg_weight_base<=weight_base;
                    cfg_output_base<=output_base;oc_tile<=0;
                    tile_count<=(output_channels+ARRAY_COLS-1)/ARRAY_COLS;
                    k_index<=0;pack_count<=0;partial_sum<=bias_read_data;
                    for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                        lane_mask[lane]<=lane<output_channels;
                    state<=S_GATHER;
                end
                S_GATHER:begin
                    if(k_index==0 && pack_count==0)
                        partial_sum<=bias_read_data;
                    input_pack[pack_count*8 +: 8]<=input_read_data;
                    weight_tile[pack_count]<=weight_read_data;
                    if((pack_count==ARRAY_ROWS-1)||(k_index==cfg_ic-1))begin
                        for(row=0;row<ARRAY_ROWS;row=row+1)
                            if(row>pack_count)begin input_pack[row*8 +: 8]<=0;
                                weight_tile[row]<={ARRAY_COLS*8{1'b0}};end
                        group_last<=k_index==cfg_ic-1;pack_count<=0;
                        weight_load_row<=0;
                        if(k_index!=cfg_ic-1)k_index<=k_index+1'b1;
                        state<=S_MATRIX_START;
                    end else begin
                        pack_count<=pack_count+1'b1;k_index<=k_index+1'b1;
                    end
                end
                S_MATRIX_START:if(mw_start_ready)begin weight_load_row<=0;state<=S_WEIGHT_LOAD;end
                S_WEIGHT_LOAD:if(mw_ready)begin
                    if(weight_load_row==ARRAY_ROWS-1)state<=S_MATRIX_INPUT;
                    else weight_load_row<=weight_load_row+1'b1;
                end
                S_MATRIX_INPUT:if(m_in_ready)state<=S_MATRIX_WAIT;
                S_MATRIX_WAIT:if(m_out_valid)begin
                    partial_sum<=m_out_data;
                    state<=group_last?S_WRITE:S_GATHER;
                end
                S_WRITE:if(output_write_ready)begin
                    if(oc_tile==tile_count-1)begin state<=S_IDLE;done<=1;end
                    else begin
                        oc_tile<=oc_tile+1'b1;k_index<=0;pack_count<=0;
                        for(lane=0;lane<ARRAY_COLS;lane=lane+1)
                            lane_mask[lane]<=((oc_tile+1)*ARRAY_COLS+lane)<cfg_oc;
                        state<=S_GATHER;
                    end
                end
                default:state<=S_IDLE;
            endcase
        end
    end
endmodule
