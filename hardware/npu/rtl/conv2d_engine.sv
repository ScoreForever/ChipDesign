`timescale 1ns/1ps
// Descriptor-driven Conv2D engine using the existing weight-stationary Matrix
// Unit and official requant unit. Generic descriptors retain the original
// schedule; fixed Conv2 can overlap gather/load or use K-major spatial tiles.
// Activation and parameter reads are combinational logical-SRAM interfaces;
// a later memory wrapper may pipeline them without changing model semantics.
module conv2d_engine #(
    parameter ARRAY_ROWS = 4,
    parameter ARRAY_COLS = 8,
    parameter DIM_WIDTH = 8,
    parameter CHANNEL_WIDTH = 8,
    parameter ADDR_WIDTH = 16,
    parameter SHIFT_WIDTH = 6,
    parameter EXTERNAL_REQUANT = 0,
    parameter OPT_GATHER_LOAD = 0,
    parameter OPT_SPATIAL_TILE = 0,
    parameter SPATIAL_TILE = 16
) (
    input  wire                                  clk,
    input  wire                                  rst,
    input  wire                                  start_valid,
    output wire                                  start_ready,
    input  wire [DIM_WIDTH-1:0]                  input_height,
    input  wire [DIM_WIDTH-1:0]                  input_width,
    input  wire [CHANNEL_WIDTH-1:0]              input_channels,
    input  wire [DIM_WIDTH-1:0]                  output_height,
    input  wire [DIM_WIDTH-1:0]                  output_width,
    input  wire [CHANNEL_WIDTH-1:0]              output_channels,
    input  wire [DIM_WIDTH-1:0]                  kernel_height,
    input  wire [DIM_WIDTH-1:0]                  kernel_width,
    input  wire [DIM_WIDTH-1:0]                  stride_height,
    input  wire [DIM_WIDTH-1:0]                  stride_width,
    input  wire [DIM_WIDTH-1:0]                  pad_top,
    input  wire [DIM_WIDTH-1:0]                  pad_left,
    input  wire [ADDR_WIDTH-1:0]                 activation_base,
    input  wire [ADDR_WIDTH-1:0]                 weight_base,
    input  wire [ADDR_WIDTH-1:0]                 output_base,
    input  wire signed [31:0]                     output_offset,
    input  wire signed [7:0]                      activation_min,
    input  wire signed [7:0]                      activation_max,
    input  wire                                  final_output_int32,
    output wire                                  busy,
    output reg                                   done,
    output reg  [ADDR_WIDTH-1:0]                 activation_read_addr,
    input  wire [7:0]                            activation_read_data,
    output reg  [ADDR_WIDTH-1:0]                 weight_read_addr,
    input  wire [ARRAY_COLS*8-1:0]               weight_read_data,
    output reg  [ADDR_WIDTH-1:0]                 parameter_tile_addr,
    input  wire [ARRAY_COLS*32-1:0]              bias_read_data,
    input  wire [ARRAY_COLS*32-1:0]              multiplier_read_data,
    input  wire [ARRAY_COLS*SHIFT_WIDTH-1:0]      shift_read_data,
    output reg                                   output_write_valid,
    input  wire                                  output_write_ready,
    output reg  [ADDR_WIDTH-1:0]                 output_write_addr,
    output reg  [ARRAY_COLS*8-1:0]               output_write_data,
    output reg  [ARRAY_COLS-1:0]                 output_write_mask,
    output reg                                   int32_write_valid,
    input  wire                                  int32_write_ready,
    output reg  [ADDR_WIDTH-1:0]                 int32_write_addr,
    output reg  [ARRAY_COLS*32-1:0]              int32_write_data,
    output reg  [ARRAY_COLS-1:0]                 int32_write_mask,
    output wire perf_activation_fire,
    output wire perf_activation_padding,
    output wire perf_weight_fire,
    output wire perf_matrix_issue,
    output wire perf_matrix_retire,
    output wire [7:0] perf_inflight,
    output wire perf_psum_read,
    output wire perf_psum_write,
    output wire requant_in_valid,
    input  wire requant_in_ready,
    output wire [ARRAY_COLS-1:0] requant_lane_mask,
    output wire [ARRAY_COLS*32-1:0] requant_acc_data,
    output wire [ARRAY_COLS*32-1:0] requant_multiplier_data,
    output wire [ARRAY_COLS*SHIFT_WIDTH-1:0] requant_shift_data,
    output wire signed [31:0] requant_output_offset,
    output wire signed [7:0] requant_activation_min,
    output wire signed [7:0] requant_activation_max,
    input  wire [ARRAY_COLS*8-1:0] requant_out_data,
    input  wire requant_out_valid,
    output wire requant_out_ready
);
    localparam TILE_CAPACITY = SPATIAL_TILE > 0 ? SPATIAL_TILE : 1;
    generate if (SPATIAL_TILE < 1 || SPATIAL_TILE > 80) begin : invalid_spatial_tile
        initial $fatal(1, "SPATIAL_TILE must be in range 1..80");
    end endgenerate
    localparam PACK_COUNT_WIDTH = $clog2(ARRAY_ROWS+1);
    localparam TILE_COUNT_WIDTH = CHANNEL_WIDTH;
    localparam [4:0] S_IDLE = 4'd0, S_WINDOW_START = 4'd1,
                     S_GATHER = 4'd2, S_MATRIX_START = 4'd3,
                     S_WEIGHT_LOAD = 4'd4, S_MATRIX_INPUT = 4'd5,
                     S_MATRIX_WAIT = 4'd6, S_REQUANT_INPUT = 4'd7,
                     S_REQUANT_WAIT = 4'd8, S_WRITE = 4'd9,
                     S_WRITE_INT32 = 4'd10;

    localparam [4:0] T_INIT=11, T_START=12, T_LOAD=13,
                     T_RUN=14, T_DRAIN=15, T_PREP=16;
    reg [4:0] state;
    reg cfg_spatial_tile;
    // K-major schedule. One bank entry per spatial position, never read again
    // until the previous K group has completely retired and Matrix is idle.
    reg [ARRAY_COLS*32-1:0] psum_bank [0:TILE_CAPACITY-1];
    reg [ARRAY_ROWS*8-1:0] tile_pack [0:1];
    reg pack_select;
    integer spatial_base, spatial_count, spatial_pos, write_pos, k_group;
    // The Matrix pipeline is ordered. Tags travel in a bounded circular FIFO;
    // enqueue and retirement can occur on the same edge without losing credit.
    localparam TAG_DEPTH = 16;
    integer tag_fifo [0:TAG_DEPTH-1];
    integer tag_head, tag_tail;
    reg [7:0] inflight;
    integer tile_k, tile_iy, tile_ix, tile_addr;

    reg [DIM_WIDTH-1:0] cfg_input_height, cfg_input_width;
    reg [CHANNEL_WIDTH-1:0] cfg_input_channels;
    reg [DIM_WIDTH-1:0] cfg_output_height, cfg_output_width;
    reg [CHANNEL_WIDTH-1:0] cfg_output_channels;
    reg [DIM_WIDTH-1:0] cfg_kernel_height, cfg_kernel_width;
    reg [DIM_WIDTH-1:0] cfg_stride_height, cfg_stride_width;
    reg [DIM_WIDTH-1:0] cfg_pad_top, cfg_pad_left;
    reg [ADDR_WIDTH-1:0] cfg_activation_base, cfg_weight_base, cfg_output_base;
    reg signed [31:0] cfg_output_offset;
    reg signed [7:0] cfg_activation_min, cfg_activation_max;
    reg cfg_final_output_int32;
    reg cfg_opt_gather_load;

    reg [TILE_COUNT_WIDTH-1:0] output_channel_tile;
    reg [TILE_COUNT_WIDTH-1:0] output_tile_count;
    reg [15:0] k_index;
    reg [PACK_COUNT_WIDTH-1:0] pack_count;
    reg [PACK_COUNT_WIDTH-1:0] weight_load_row;
    reg [ARRAY_ROWS*8-1:0] activation_pack;
    reg [ARRAY_COLS*8-1:0] weight_tile [0:ARRAY_ROWS-1];
    reg [ARRAY_COLS*32-1:0] partial_sum;
    reg [ARRAY_COLS*32-1:0] tile_bias;
    reg [ARRAY_COLS*32-1:0] tile_multiplier;
    reg [ARRAY_COLS*SHIFT_WIDTH-1:0] tile_shift;
    reg [ARRAY_COLS-1:0] tile_lane_mask;
    reg [DIM_WIDTH-1:0] completed_out_y, completed_out_x;
    reg completed_last_in_layer;
    reg group_last_in_output;

    wire tile_padding = tile_iy < 0 || tile_iy >= 10 || tile_ix < 0 || tile_ix >= 8;
    wire tile_gather_fire = !rst && state == T_RUN && pack_count < ARRAY_ROWS;
    wire tile_issue_valid = state == T_RUN && pack_count == ARRAY_ROWS &&
                           (inflight < TAG_DEPTH || perf_matrix_retire);
    wire [ARRAY_COLS*32-1:0] matrix_input_psum = cfg_spatial_tile ?
        ((k_group == 0) ? tile_bias : psum_bank[spatial_pos]) : partial_sum;
    wire [ARRAY_ROWS*8-1:0] matrix_input_act = cfg_spatial_tile ?
        tile_pack[pack_select] : activation_pack;

    wire window_start_ready, window_busy, window_done;
    wire window_item_valid;
    wire [ADDR_WIDTH-1:0] window_activation_addr;
    wire window_is_padding;
    wire [DIM_WIDTH-1:0] window_out_y, window_out_x;
    wire [DIM_WIDTH-1:0] window_kernel_y, window_kernel_x;
    wire [CHANNEL_WIDTH-1:0] window_input_channel;
    wire window_first_in_output, window_last_in_output;
    wire window_first_in_layer, window_last_in_layer;
    wire matrix_weight_start_ready, matrix_weight_ready, matrix_weights_loaded;
    // In the overlap schedule a window item and its current SRAM weight
    // must be consumed together, including when the Matrix loader stalls.
    wire window_item_ready = cfg_opt_gather_load ?
        ((state == S_WEIGHT_LOAD) && matrix_weight_ready) : (state == S_GATHER);
    wire window_item_fire = window_item_valid && window_item_ready;
    wire window_start_valid = (state == S_WINDOW_START);

    conv_window_addr_gen #(
        .DIM_WIDTH(DIM_WIDTH), .CHANNEL_WIDTH(CHANNEL_WIDTH),
        .ADDR_WIDTH(ADDR_WIDTH)
    ) window_gen (
        .clk(clk), .rst(rst), .start_valid(window_start_valid),
        .start_ready(window_start_ready), .input_height(cfg_input_height),
        .input_width(cfg_input_width), .input_channels(cfg_input_channels),
        .output_height(cfg_output_height), .output_width(cfg_output_width),
        .kernel_height(cfg_kernel_height), .kernel_width(cfg_kernel_width),
        .stride_height(cfg_stride_height), .stride_width(cfg_stride_width),
        .pad_top(cfg_pad_top), .pad_left(cfg_pad_left), .busy(window_busy),
        .done(window_done), .item_valid(window_item_valid),
        .item_ready(window_item_ready), .activation_addr(window_activation_addr),
        .is_padding(window_is_padding), .out_y(window_out_y),
        .out_x(window_out_x), .kernel_y(window_kernel_y),
        .kernel_x(window_kernel_x), .input_channel(window_input_channel),
        .first_in_output(window_first_in_output),
        .last_in_output(window_last_in_output),
        .first_in_layer(window_first_in_layer),
        .last_in_layer(window_last_in_layer)
    );

    wire matrix_idle, matrix_in_ready, matrix_out_valid;
    wire [ARRAY_COLS*32-1:0] matrix_out_data;
    wire matrix_weight_start_valid = (state == S_MATRIX_START) || (state == T_START);
    wire matrix_weight_valid = (state == T_LOAD) || ((state == S_WEIGHT_LOAD) &&
                               (!cfg_opt_gather_load || window_item_valid));
    wire matrix_weight_fire = matrix_weight_valid && matrix_weight_ready;
    wire [ARRAY_COLS*8-1:0] matrix_weight_data = (cfg_spatial_tile || cfg_opt_gather_load) ?
        weight_read_data : weight_tile[weight_load_row];
    wire matrix_in_valid = (state == S_MATRIX_INPUT) || tile_issue_valid;
    // Retirement is independent of activation gathering/issue. The bank has a
    // dedicated write port; tags identify its destination even during issue.
    wire matrix_out_ready = cfg_spatial_tile ? (!rst && inflight != 0) :
                            (state == S_MATRIX_WAIT);
    assign perf_activation_fire = !rst && (window_item_fire || tile_gather_fire);
    assign perf_activation_padding = perf_activation_fire &&
                                    (cfg_spatial_tile ? tile_padding : window_is_padding);
    assign perf_weight_fire = matrix_weight_fire;
    assign perf_matrix_issue = matrix_in_valid && matrix_in_ready;
    assign perf_matrix_retire = matrix_out_valid && matrix_out_ready && !rst;
    assign perf_inflight = inflight;
    assign perf_psum_read = cfg_spatial_tile && perf_matrix_issue && k_group != 0;
    assign perf_psum_write = cfg_spatial_tile && perf_matrix_retire;

    matrix_unit #(
        .ACT_WIDTH(8), .WGT_WIDTH(8), .ACC_WIDTH(32),
        .ARRAY_ROWS(ARRAY_ROWS), .ARRAY_COLS(ARRAY_COLS)
    ) matrix (
        .clk(clk), .rst(rst),
        .weight_start_valid(matrix_weight_start_valid),
        .weight_start_ready(matrix_weight_start_ready),
        .weight_valid(matrix_weight_valid), .weight_ready(matrix_weight_ready),
        .weight_data(matrix_weight_data),
        .weights_loaded(matrix_weights_loaded), .idle(matrix_idle),
        .in_valid(matrix_in_valid), .in_ready(matrix_in_ready),
        .in_act_data(matrix_input_act), .in_psum_data(matrix_input_psum),
        .out_valid(matrix_out_valid), .out_ready(matrix_out_ready),
        .out_psum_data(matrix_out_data)
    );

    wire internal_requant_in_ready, internal_requant_out_valid;
    wire [ARRAY_COLS*8-1:0] internal_requant_out_data;
    wire requant_in_ready_eff = EXTERNAL_REQUANT ? requant_in_ready :
                                                       internal_requant_in_ready;
    wire requant_out_valid_eff = EXTERNAL_REQUANT ? requant_out_valid :
                                                        internal_requant_out_valid;
    wire [ARRAY_COLS*8-1:0] requant_out_data_eff = EXTERNAL_REQUANT ? requant_out_data :
                                                                         internal_requant_out_data;
    assign requant_in_valid = (state == S_REQUANT_INPUT);
    assign requant_lane_mask = tile_lane_mask;
    assign requant_acc_data = partial_sum;
    assign requant_multiplier_data = tile_multiplier;
    assign requant_shift_data = tile_shift;
    assign requant_output_offset = cfg_output_offset;
    assign requant_activation_min = cfg_activation_min;
    assign requant_activation_max = cfg_activation_max;
    assign requant_out_ready = (state == S_REQUANT_WAIT);
    generate if (!EXTERNAL_REQUANT) begin : g_internal_requant
        requant_unit #(.LANES(ARRAY_COLS), .SHIFT_WIDTH(SHIFT_WIDTH)) requant (
            .clk(clk), .rst(rst), .in_valid(requant_in_valid),
            .in_ready(internal_requant_in_ready), .lane_mask(tile_lane_mask),
            .acc_data(partial_sum), .bias_data({ARRAY_COLS*32{1'b0}}),
            .multiplier_data(tile_multiplier), .shift_data(tile_shift),
            .output_offset(cfg_output_offset), .activation_min(cfg_activation_min),
            .activation_max(cfg_activation_max), .out_data(internal_requant_out_data),
            .out_valid(internal_requant_out_valid), .out_ready(requant_out_ready)
        );
    end else begin : g_external_requant
        assign internal_requant_in_ready = 1'b0;
        assign internal_requant_out_valid = 1'b0;
        assign internal_requant_out_data = {ARRAY_COLS*8{1'b0}};
    end endgenerate

    integer lane_index;
    integer row_index;
    integer unsigned weight_offset_calc;
    integer unsigned output_offset_calc;

    wire descriptor_valid = (input_height != 0) && (input_width != 0) &&
                            (input_channels != 0) && (output_height != 0) &&
                            (output_width != 0) && (output_channels != 0) &&
                            (kernel_height != 0) && (kernel_width != 0) &&
                            (stride_height != 0) && (stride_width != 0);
    // Restrict overlap to TinyCNN Conv2: K=72 is a whole number of ROW4
    // groups. All other descriptors retain the gather-then-load schedule.
    wire fixed_conv2_descriptor =
        (ARRAY_ROWS == 4) && (ARRAY_COLS == 8) && (input_height == 10) && (input_width == 8) &&
        (input_channels == 8) && (output_height == 10) && (output_width == 8) &&
        (output_channels == 8) && (kernel_height == 3) && (kernel_width == 3) &&
        (stride_height == 1) && (stride_width == 1) &&
        (pad_top == 1) && (pad_left == 1) && !final_output_int32;
    wire opt_spatial_tile_descriptor = (OPT_SPATIAL_TILE != 0) &&
        (SPATIAL_TILE > 0) && fixed_conv2_descriptor;
    wire opt_gather_load_descriptor = (OPT_GATHER_LOAD != 0) &&
        fixed_conv2_descriptor && !opt_spatial_tile_descriptor;
    assign start_ready = (state == S_IDLE) && !rst && descriptor_valid;
    assign busy = (state != S_IDLE);

    always @* begin
        tile_k = k_group*4 + pack_count;
        tile_iy = (spatial_base+spatial_pos)/8 + tile_k/24 - 1;
        tile_ix = (spatial_base+spatial_pos)%8 + (tile_k/8)%3 - 1;
        tile_addr = (tile_iy*8+tile_ix)*8 + tile_k%8;
        activation_read_addr = cfg_activation_base +
            (cfg_spatial_tile ? (tile_padding ? 0 : tile_addr) : window_activation_addr);
        weight_offset_calc = cfg_spatial_tile ? k_group*4 + weight_load_row :
                             (k_index * output_tile_count) + output_channel_tile;
        weight_read_addr = cfg_weight_base + weight_offset_calc[ADDR_WIDTH-1:0];
        parameter_tile_addr = output_channel_tile;
        output_offset_calc = (($unsigned(completed_out_y) * $unsigned(cfg_output_width) +
                              $unsigned(completed_out_x)) *
                              $unsigned(cfg_output_channels)) +
                             ($unsigned(output_channel_tile) * ARRAY_COLS);
        output_write_addr = cfg_output_base + output_offset_calc[ADDR_WIDTH-1:0];
        output_write_valid = (state == S_WRITE);
        output_write_data = requant_out_data_eff;
        output_write_mask = tile_lane_mask;
        int32_write_valid = (state == S_WRITE_INT32);
        int32_write_addr = cfg_output_base + output_offset_calc[ADDR_WIDTH-1:0];
        int32_write_data = partial_sum;
        int32_write_mask = tile_lane_mask;
    end

    always @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            done <= 1'b0;
            cfg_opt_gather_load <= 1'b0;
            cfg_spatial_tile <= 1'b0;
            spatial_base <= 0; spatial_count <= 0; spatial_pos <= 0;
            write_pos <= 0; k_group <= 0; pack_select <= 0;
            tile_pack[0] <= 0; tile_pack[1] <= 0;
            inflight <= 0; tag_head <= 0; tag_tail <= 0;
            output_channel_tile <= 0;
            output_tile_count <= 0;
            k_index <= 0;
            pack_count <= 0;
            weight_load_row <= 0;
            activation_pack <= 0;
            partial_sum <= 0;
            tile_bias <= 0;
            tile_multiplier <= 0;
            tile_shift <= 0;
            tile_lane_mask <= 0;
            completed_out_y <= 0;
            completed_out_x <= 0;
            completed_last_in_layer <= 0;
            group_last_in_output <= 0;
            for (row_index = 0; row_index < ARRAY_ROWS; row_index = row_index + 1)
                weight_tile[row_index] <= 0;
        end else begin
            done <= 1'b0;
            case ({perf_matrix_issue, perf_matrix_retire})
                2'b10: inflight <= inflight + 1'b1;
                2'b01: inflight <= inflight - 1'b1;
                default: begin end
            endcase
            if (cfg_spatial_tile && perf_matrix_issue) begin
                tag_fifo[tag_tail] <= spatial_pos;
                tag_tail <= (tag_tail == TAG_DEPTH-1) ? 0 : tag_tail+1;
            end
            if (cfg_spatial_tile && perf_matrix_retire) begin
                psum_bank[tag_fifo[tag_head]] <= matrix_out_data;
                tag_head <= (tag_head == TAG_DEPTH-1) ? 0 : tag_head+1;
            end
            case (state)
                S_IDLE: begin
                    if (start_valid && start_ready) begin
                        cfg_input_height <= input_height;
                        cfg_input_width <= input_width;
                        cfg_input_channels <= input_channels;
                        cfg_output_height <= output_height;
                        cfg_output_width <= output_width;
                        cfg_output_channels <= output_channels;
                        cfg_kernel_height <= kernel_height;
                        cfg_kernel_width <= kernel_width;
                        cfg_stride_height <= stride_height;
                        cfg_stride_width <= stride_width;
                        cfg_pad_top <= pad_top;
                        cfg_pad_left <= pad_left;
                        cfg_activation_base <= activation_base;
                        cfg_weight_base <= weight_base;
                        cfg_output_base <= output_base;
                        cfg_output_offset <= output_offset;
                        cfg_activation_min <= activation_min;
                        cfg_activation_max <= activation_max;
                        cfg_final_output_int32 <= final_output_int32;
                        cfg_opt_gather_load <= opt_gather_load_descriptor;
                        cfg_spatial_tile <= opt_spatial_tile_descriptor;
                        spatial_base <= 0; tag_head <= 0; tag_tail <= 0;
                        output_channel_tile <= 0;
                        output_tile_count <=
                            (output_channels + ARRAY_COLS - 1) / ARRAY_COLS;
                        state <= opt_spatial_tile_descriptor ? T_INIT : S_WINDOW_START;
                    end
                end
                S_WINDOW_START: begin
                    if (window_start_ready) begin
                        k_index <= 0;
                        pack_count <= 0;
                        partial_sum <= bias_read_data;
                        tile_bias <= bias_read_data;
                        tile_multiplier <= multiplier_read_data;
                        tile_shift <= shift_read_data;
                        for (lane_index = 0; lane_index < ARRAY_COLS; lane_index = lane_index + 1)
                            tile_lane_mask[lane_index] <=
                                (output_channel_tile*ARRAY_COLS + lane_index) <
                                cfg_output_channels;
                        state <= cfg_opt_gather_load ? S_MATRIX_START : S_GATHER;
                    end
                end
                S_GATHER: begin
                    if (window_item_fire) begin
                        activation_pack[pack_count*8 +: 8] <=
                            window_is_padding ? 8'd0 : activation_read_data;
                        weight_tile[pack_count] <= weight_read_data;
                        if (window_first_in_output) begin
                            partial_sum <= tile_bias;
                            k_index <= 1;
                        end else if (window_last_in_output)
                            k_index <= 0;
                        else
                            k_index <= k_index + 1'b1;

                        if ((pack_count == ARRAY_ROWS-1) || window_last_in_output) begin
                            for (row_index = 0; row_index < ARRAY_ROWS; row_index = row_index + 1)
                                if (row_index > pack_count) begin
                                    activation_pack[row_index*8 +: 8] <= 8'd0;
                                    weight_tile[row_index] <= {ARRAY_COLS*8{1'b0}};
                                end
                            pack_count <= 0;
                            weight_load_row <= 0;
                            group_last_in_output <= window_last_in_output;
                            if (window_last_in_output) begin
                                completed_out_y <= window_out_y;
                                completed_out_x <= window_out_x;
                                completed_last_in_layer <= window_last_in_layer;
                            end
                            state <= S_MATRIX_START;
                        end else begin
                            pack_count <= pack_count + 1'b1;
                        end
                    end
                end
                S_MATRIX_START: begin
                    if (matrix_weight_start_ready) begin
                        weight_load_row <= 0;
                        state <= S_WEIGHT_LOAD;
                    end
                end
                S_WEIGHT_LOAD: begin
                    if (matrix_weight_fire) begin
                        if (cfg_opt_gather_load) begin
                            activation_pack[weight_load_row*8 +: 8] <=
                                window_is_padding ? 8'd0 : activation_read_data;
                            if (window_first_in_output) begin
                                partial_sum <= tile_bias;
                                k_index <= 1;
                            end else if (window_last_in_output)
                                k_index <= 0;
                            else
                                k_index <= k_index + 1'b1;

                            if (weight_load_row == ARRAY_ROWS-1) begin
                                group_last_in_output <= window_last_in_output;
                                if (window_last_in_output) begin
                                    completed_out_y <= window_out_y;
                                    completed_out_x <= window_out_x;
                                    completed_last_in_layer <= window_last_in_layer;
                                end
                            end
                        end
                        // The last activation and weight latch on this edge;
                        // Matrix input is not presented until the next state.
                        if (weight_load_row == ARRAY_ROWS-1)
                            state <= S_MATRIX_INPUT;
                        else
                            weight_load_row <= weight_load_row + 1'b1;
                    end
                end
                S_MATRIX_INPUT: begin
                    if (matrix_in_ready)
                        state <= S_MATRIX_WAIT;
                end
                S_MATRIX_WAIT: begin
                    if (matrix_out_valid) begin
                        partial_sum <= matrix_out_data;
                        state <= group_last_in_output ?
                            (cfg_final_output_int32 ? S_WRITE_INT32 : S_REQUANT_INPUT) :
                            (cfg_opt_gather_load ? S_MATRIX_START : S_GATHER);
                    end
                end
                S_REQUANT_INPUT: begin
                    if (requant_in_ready_eff)
                        state <= S_REQUANT_WAIT;
                end
                S_REQUANT_WAIT: begin
                    if (requant_out_valid_eff)
                        state <= S_WRITE;
                end
                S_WRITE: begin
                    if (output_write_ready) begin
                        if (cfg_spatial_tile) begin
                            if (write_pos+1 < spatial_count) begin
                                write_pos <= write_pos+1;
                                state <= T_PREP;
                            end else if (spatial_base+spatial_count == 80) begin
                                state <= S_IDLE;
                                done <= 1'b1;
                            end else begin
                                spatial_base <= spatial_base+spatial_count;
                                state <= T_INIT;
                            end
                        end else if (completed_last_in_layer) begin
                            if (output_channel_tile == output_tile_count-1) begin
                                state <= S_IDLE;
                                done <= 1'b1;
                            end else begin
                                output_channel_tile <= output_channel_tile + 1'b1;
                                state <= S_WINDOW_START;
                            end
                        end else begin
                            state <= cfg_opt_gather_load ? S_MATRIX_START : S_GATHER;
                        end
                    end
                end
                S_WRITE_INT32: begin
                    if (int32_write_ready) begin
                        if (completed_last_in_layer) begin
                            if (output_channel_tile == output_tile_count-1) begin
                                state <= S_IDLE;
                                done <= 1'b1;
                            end else begin
                                output_channel_tile <= output_channel_tile + 1'b1;
                                state <= S_WINDOW_START;
                            end
                        end else begin
                            state <= cfg_opt_gather_load ? S_MATRIX_START : S_GATHER;
                        end
                    end
                end
                T_INIT: begin
                    spatial_count <= (80-spatial_base < SPATIAL_TILE) ?
                                     80-spatial_base : SPATIAL_TILE;
                    spatial_pos <= 0; write_pos <= 0; k_group <= 0;
                    pack_count <= 0; pack_select <= 0;
                    tile_bias <= bias_read_data;
                    tile_multiplier <= multiplier_read_data;
                    tile_shift <= shift_read_data;
                    tile_lane_mask <= {ARRAY_COLS{1'b1}};
                    state <= T_START;
                end
                T_START: begin
                    if (matrix_weight_start_ready) begin
                        weight_load_row <= 0;
                        state <= T_LOAD;
                    end
                end
                T_LOAD: begin
                    if (matrix_weight_fire) begin
                        if (weight_load_row == ARRAY_ROWS-1) begin
                            spatial_pos <= 0; pack_count <= 0;
                            state <= T_RUN;
                        end else weight_load_row <= weight_load_row+1'b1;
                    end
                end
                T_RUN: begin
                    // Four byte reads followed by one issue. Alternating packs
                    // retain the accepted vector while the next is assembled.
                    // Gathering does not wait for earlier Matrix retirements.
                    if (tile_gather_fire) begin
                        tile_pack[pack_select][pack_count*8 +: 8] <=
                            tile_padding ? 8'd0 : activation_read_data;
                        pack_count <= pack_count+1'b1;
                    end
                    if (perf_matrix_issue) begin
                        pack_select <= !pack_select;
                        pack_count <= 0;
                        if (spatial_pos+1 == spatial_count) state <= T_DRAIN;
                        else spatial_pos <= spatial_pos+1;
                    end
                end
                T_DRAIN: begin
                    // A weight swap cannot overtake a tagged transaction.
                    if (inflight == 0 && matrix_idle) begin
                        if (k_group == 17) begin
                            write_pos <= 0; state <= T_PREP;
                        end else begin
                            k_group <= k_group+1; state <= T_START;
                        end
                    end
                end
                T_PREP: begin
                    partial_sum <= psum_bank[write_pos];
                    completed_out_y <= (spatial_base+write_pos)/8;
                    completed_out_x <= (spatial_base+write_pos)%8;
                    state <= S_REQUANT_INPUT;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
