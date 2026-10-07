`timescale 1ns/1ps
// NHWC global spatial sum controller. The fixed 1/(H*W) average factor is
// intentionally folded into the following FC scale by the model exporter.
module global_sum_pool_engine #(
    parameter LANES = 8,
    parameter DIM_WIDTH = 8,
    parameter CHANNEL_WIDTH = 8,
    parameter ADDR_WIDTH = 16
) (
    input  wire                             clk,
    input  wire                             rst,
    input  wire                             start_valid,
    output wire                             start_ready,
    input  wire [DIM_WIDTH-1:0]             input_height,
    input  wire [DIM_WIDTH-1:0]             input_width,
    input  wire [CHANNEL_WIDTH-1:0]         channels,
    input  wire [ADDR_WIDTH-1:0]            input_base,
    input  wire [ADDR_WIDTH-1:0]            output_base,
    output wire                             busy,
    output reg                              done,
    output reg  [ADDR_WIDTH-1:0]            activation_read_addr,
    input  wire [LANES*8-1:0]              activation_read_data,
    output reg                              output_write_valid,
    input  wire                             output_write_ready,
    output reg  [ADDR_WIDTH-1:0]            output_write_addr,
    output reg  [LANES*32-1:0]             output_write_data,
    output reg  [LANES-1:0]                output_write_mask
);
    localparam [2:0] S_IDLE=3'd0, S_SEND=3'd1, S_WAIT=3'd2, S_WRITE=3'd3;
    reg [2:0] state;
    reg [DIM_WIDTH-1:0] cfg_height, cfg_width;
    reg [CHANNEL_WIDTH-1:0] cfg_channels;
    reg [ADDR_WIDTH-1:0] cfg_input_base, cfg_output_base;
    reg [15:0] spatial_index, spatial_count;
    reg [CHANNEL_WIDTH-1:0] channel_tile, tile_count;
    reg [LANES-1:0] lane_mask;

    wire reduce_in_ready, reduce_out_valid;
    wire [LANES*32-1:0] reduce_out_sum;
    wire reduce_in_valid = (state == S_SEND);
    wire reduce_out_ready = (state == S_WAIT);
    reduction_sum_unit #(.LANES(LANES), .DATA_WIDTH(8), .ACC_WIDTH(32)) reduction (
        .clk(clk), .rst(rst), .in_valid(reduce_in_valid),
        .in_ready(reduce_in_ready), .first(spatial_index == 0),
        .last(spatial_index == spatial_count-1), .lane_mask(lane_mask),
        .in_data(activation_read_data), .out_sum(reduce_out_sum),
        .out_valid(reduce_out_valid), .out_ready(reduce_out_ready)
    );

    integer lane;
    integer unsigned input_addr_calc;
    wire descriptor_valid = (input_height != 0) && (input_width != 0) &&
                            (channels != 0);
    assign start_ready = (state == S_IDLE) && !rst && descriptor_valid;
    assign busy = (state != S_IDLE);

    always @* begin
        input_addr_calc = ($unsigned(spatial_index) * $unsigned(cfg_channels)) +
                          ($unsigned(channel_tile) * LANES);
        activation_read_addr = cfg_input_base + input_addr_calc[ADDR_WIDTH-1:0];
        output_write_addr = cfg_output_base + channel_tile*LANES;
        output_write_valid = (state == S_WRITE);
        output_write_data = reduce_out_sum;
        output_write_mask = lane_mask;
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            done <= 1'b0;
            spatial_index <= 0;
            spatial_count <= 0;
            channel_tile <= 0;
            tile_count <= 0;
            lane_mask <= 0;
        end else begin
            done <= 1'b0;
            case(state)
                S_IDLE: if(start_valid && start_ready) begin
                    cfg_height <= input_height;
                    cfg_width <= input_width;
                    cfg_channels <= channels;
                    cfg_input_base <= input_base;
                    cfg_output_base <= output_base;
                    spatial_count <= input_height*input_width;
                    spatial_index <= 0;
                    channel_tile <= 0;
                    tile_count <= (channels+LANES-1)/LANES;
                    for(lane=0;lane<LANES;lane=lane+1)
                        lane_mask[lane] <= lane < channels;
                    state <= S_SEND;
                end
                S_SEND: if(reduce_in_ready) begin
                    if(spatial_index == spatial_count-1)
                        state <= S_WAIT;
                    else
                        spatial_index <= spatial_index+1'b1;
                end
                S_WAIT: if(reduce_out_valid) state <= S_WRITE;
                S_WRITE: if(output_write_ready) begin
                    spatial_index <= 0;
                    if(channel_tile != tile_count-1) begin
                        channel_tile <= channel_tile+1'b1;
                        for(lane=0;lane<LANES;lane=lane+1)
                            lane_mask[lane] <= ((channel_tile+1)*LANES+lane) < cfg_channels;
                        state <= S_SEND;
                    end else begin
                        state <= S_IDLE;
                        done <= 1'b1;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
