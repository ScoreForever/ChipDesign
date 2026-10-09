`timescale 1ns/1ps
// NHWC 2x2 stride-2 MaxPool controller. Reuses vector_unit VACC so every
// packed lane is one channel and reduction is across four spatial positions.
module maxpool2x2_engine #(
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
    output reg  [LANES*8-1:0]              output_write_data,
    output reg  [LANES-1:0]                output_write_mask
);
    localparam [2:0] S_IDLE = 3'd0, S_READ_SEND = 3'd1,
                     S_READ_ADVANCE = 3'd2, S_OUTPUT_SEND = 3'd3,
                     S_OUTPUT_WAIT = 3'd4, S_WRITE = 3'd5,
                     S_READ_PREP = 3'd6;
    localparam [2:0] VU_MAX = 3'd2, VU_MOV = 3'd4;
    localparam VECTOR_A = 1'b0, VACC = 1'b1;
    localparam VECTOR_B = 1'b0, OUTPUT = 1'b0, DEST_VACC = 1'b1;

    reg [2:0] state;
    reg [DIM_WIDTH-1:0] cfg_input_height, cfg_input_width;
    reg [CHANNEL_WIDTH-1:0] cfg_channels;
    reg [ADDR_WIDTH-1:0] cfg_input_base, cfg_output_base;
    reg [DIM_WIDTH-1:0] out_y, out_x;
    reg [CHANNEL_WIDTH-1:0] channel_tile, tile_count;
    reg [1:0] position;
    reg [LANES-1:0] lane_mask;

    wire vu_in_ready, vu_out_valid;
    wire [LANES*8-1:0] vu_out_data;
    wire vu_in_valid = (state == S_READ_SEND) || (state == S_OUTPUT_SEND);
    wire [2:0] vu_opcode = (state == S_OUTPUT_SEND || position == 0) ? VU_MOV : VU_MAX;
    wire vu_src_a = (state == S_OUTPUT_SEND || position != 0) ? VACC : VECTOR_A;
    wire vu_dst = (state == S_OUTPUT_SEND) ? OUTPUT : DEST_VACC;
    wire [LANES*8-1:0] vu_vec_a = (position == 0) ? activation_read_data : 0;
    wire [LANES*8-1:0] vu_vec_b = activation_read_data;
    wire vu_out_ready = (state == S_OUTPUT_WAIT);

    vector_unit #(.LANES(LANES), .DATA_WIDTH(8)) vector (
        .clk(clk), .rst(rst), .in_valid(vu_in_valid), .in_ready(vu_in_ready),
        .opcode(vu_opcode), .src_a_sel(vu_src_a), .src_b_sel(VECTOR_B),
        .dst_sel(vu_dst), .scalar(8'd0), .lane_mask(lane_mask),
        .vec_a(vu_vec_a), .vec_b(vu_vec_b), .vec_out(vu_out_data),
        .out_valid(vu_out_valid), .out_ready(vu_out_ready)
    );

    integer lane;
    integer unsigned input_y_calc, input_x_calc, input_addr_calc, output_addr_calc;
    reg [ADDR_WIDTH-1:0] activation_read_addr_calc;
    wire descriptor_valid = (input_height >= 2) && (input_width >= 2) &&
                            (channels != 0);
    assign start_ready = (state == S_IDLE) && !rst && descriptor_valid;
    assign busy = (state != S_IDLE);

    always @* begin
        input_y_calc = ($unsigned(out_y) << 1) + position[1];
        input_x_calc = ($unsigned(out_x) << 1) + position[0];
        input_addr_calc = ((input_y_calc * $unsigned(cfg_input_width) + input_x_calc) *
                           $unsigned(cfg_channels)) +
                          ($unsigned(channel_tile) * LANES);
        activation_read_addr_calc = cfg_input_base + input_addr_calc[ADDR_WIDTH-1:0];
        output_addr_calc = (($unsigned(out_y) * ($unsigned(cfg_input_width) >> 1) +
                            $unsigned(out_x)) * $unsigned(cfg_channels)) +
                           ($unsigned(channel_tile) * LANES);
        output_write_addr = cfg_output_base + output_addr_calc[ADDR_WIDTH-1:0];
        output_write_valid = (state == S_WRITE);
        output_write_data = vu_out_data;
        output_write_mask = lane_mask;
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            done <= 1'b0;
            out_y <= 0;
            out_x <= 0;
            channel_tile <= 0;
            tile_count <= 0;
            position <= 0;
            lane_mask <= 0;
            activation_read_addr <= 0;
        end else begin
            done <= 1'b0;
            case (state)
                S_IDLE: begin
                    if (start_valid && start_ready) begin
                        cfg_input_height <= input_height;
                        cfg_input_width <= input_width;
                        cfg_channels <= channels;
                        cfg_input_base <= input_base;
                        cfg_output_base <= output_base;
                        tile_count <= (channels + LANES - 1) / LANES;
                        out_y <= 0;
                        out_x <= 0;
                        channel_tile <= 0;
                        position <= 0;
                        for (lane = 0; lane < LANES; lane = lane + 1)
                            lane_mask[lane] <= lane < channels;
                        state <= S_READ_PREP;
                    end
                end
                S_READ_PREP: begin
                    activation_read_addr <= activation_read_addr_calc;
                    state <= S_READ_SEND;
                end
                S_READ_SEND: begin
                    if (vu_in_ready)
                        state <= S_READ_ADVANCE;
                end
                S_READ_ADVANCE: begin
                    if (position == 3) begin
                        state <= S_OUTPUT_SEND;
                    end else begin
                        position <= position + 1'b1;
                        state <= S_READ_PREP;
                    end
                end
                S_OUTPUT_SEND: begin
                    if (vu_in_ready)
                        state <= S_OUTPUT_WAIT;
                end
                S_OUTPUT_WAIT: begin
                    if (vu_out_valid)
                        state <= S_WRITE;
                end
                S_WRITE: begin
                    if (output_write_ready) begin
                        position <= 0;
                        if (channel_tile != tile_count-1) begin
                            channel_tile <= channel_tile + 1'b1;
                            for (lane = 0; lane < LANES; lane = lane + 1)
                                lane_mask[lane] <=
                                    ((channel_tile+1)*LANES + lane) < cfg_channels;
                            state <= S_READ_PREP;
                        end else begin
                            channel_tile <= 0;
                            for (lane = 0; lane < LANES; lane = lane + 1)
                                lane_mask[lane] <= lane < cfg_channels;
                            if (out_x != (cfg_input_width >> 1)-1) begin
                                out_x <= out_x + 1'b1;
                                state <= S_READ_PREP;
                            end else begin
                                out_x <= 0;
                                if (out_y != (cfg_input_height >> 1)-1) begin
                                    out_y <= out_y + 1'b1;
                                    state <= S_READ_PREP;
                                end else begin
                                    state <= S_IDLE;
                                    done <= 1'b1;
                                end
                            end
                        end
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
