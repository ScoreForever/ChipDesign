`timescale 1ns/1ps
// Generates one flattened NHWC activation address per accepted item for a
// complete Conv2D layer. Locations outside the input tensor are marked as
// padding and must be supplied as quantized real zero by the caller.
module conv_window_addr_gen #(
    parameter DIM_WIDTH = 8,
    parameter CHANNEL_WIDTH = 8,
    parameter ADDR_WIDTH = 16
) (
    input  wire                         clk,
    input  wire                         rst,
    input  wire                         start_valid,
    output wire                         start_ready,
    input  wire [DIM_WIDTH-1:0]         input_height,
    input  wire [DIM_WIDTH-1:0]         input_width,
    input  wire [CHANNEL_WIDTH-1:0]     input_channels,
    input  wire [DIM_WIDTH-1:0]         output_height,
    input  wire [DIM_WIDTH-1:0]         output_width,
    input  wire [DIM_WIDTH-1:0]         kernel_height,
    input  wire [DIM_WIDTH-1:0]         kernel_width,
    input  wire [DIM_WIDTH-1:0]         stride_height,
    input  wire [DIM_WIDTH-1:0]         stride_width,
    input  wire [DIM_WIDTH-1:0]         pad_top,
    input  wire [DIM_WIDTH-1:0]         pad_left,
    output reg                          busy,
    output reg                          done,
    output wire                         item_valid,
    input  wire                         item_ready,
    output reg  [ADDR_WIDTH-1:0]        activation_addr,
    output reg                          is_padding,
    output wire [DIM_WIDTH-1:0]         out_y,
    output wire [DIM_WIDTH-1:0]         out_x,
    output wire [DIM_WIDTH-1:0]         kernel_y,
    output wire [DIM_WIDTH-1:0]         kernel_x,
    output wire [CHANNEL_WIDTH-1:0]     input_channel,
    output wire                         first_in_output,
    output wire                         last_in_output,
    output wire                         first_in_layer,
    output wire                         last_in_layer
);
    reg [DIM_WIDTH-1:0] cfg_input_height, cfg_input_width;
    reg [CHANNEL_WIDTH-1:0] cfg_input_channels;
    reg [DIM_WIDTH-1:0] cfg_output_height, cfg_output_width;
    reg [DIM_WIDTH-1:0] cfg_kernel_height, cfg_kernel_width;
    reg [DIM_WIDTH-1:0] cfg_stride_height, cfg_stride_width;
    reg [DIM_WIDTH-1:0] cfg_pad_top, cfg_pad_left;

    reg [DIM_WIDTH-1:0] out_y_count, out_x_count;
    reg [DIM_WIDTH-1:0] kernel_y_count, kernel_x_count;
    reg [CHANNEL_WIDTH-1:0] channel_count;

    integer signed input_y_calc;
    integer signed input_x_calc;
    integer unsigned address_calc;

    wire start_fire = start_valid && start_ready;
    wire item_fire = item_valid && item_ready;

    assign start_ready = !busy && !rst;
    assign item_valid = busy && !rst;
    assign out_y = out_y_count;
    assign out_x = out_x_count;
    assign kernel_y = kernel_y_count;
    assign kernel_x = kernel_x_count;
    assign input_channel = channel_count;

    assign first_in_output = (kernel_y_count == 0) &&
                             (kernel_x_count == 0) &&
                             (channel_count == 0);
    assign last_in_output = (kernel_y_count == cfg_kernel_height-1) &&
                            (kernel_x_count == cfg_kernel_width-1) &&
                            (channel_count == cfg_input_channels-1);
    assign first_in_layer = first_in_output &&
                            (out_y_count == 0) && (out_x_count == 0);
    assign last_in_layer = last_in_output &&
                           (out_y_count == cfg_output_height-1) &&
                           (out_x_count == cfg_output_width-1);

    always @* begin
        address_calc = 0;
        input_y_calc = $unsigned(out_y_count) * $unsigned(cfg_stride_height) +
                       $unsigned(kernel_y_count) - $unsigned(cfg_pad_top);
        input_x_calc = $unsigned(out_x_count) * $unsigned(cfg_stride_width) +
                       $unsigned(kernel_x_count) - $unsigned(cfg_pad_left);
        is_padding = (input_y_calc < 0) ||
                     (input_x_calc < 0) ||
                     (input_y_calc >= $unsigned(cfg_input_height)) ||
                     (input_x_calc >= $unsigned(cfg_input_width));
        if (is_padding) begin
            activation_addr = {ADDR_WIDTH{1'b0}};
        end else begin
            address_calc = ((input_y_calc * $unsigned(cfg_input_width)) +
                            input_x_calc) * $unsigned(cfg_input_channels) +
                           $unsigned(channel_count);
            activation_addr = address_calc[ADDR_WIDTH-1:0];
        end
    end

    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            done <= 1'b0;
            cfg_input_height <= 0;
            cfg_input_width <= 0;
            cfg_input_channels <= 0;
            cfg_output_height <= 0;
            cfg_output_width <= 0;
            cfg_kernel_height <= 0;
            cfg_kernel_width <= 0;
            cfg_stride_height <= 0;
            cfg_stride_width <= 0;
            cfg_pad_top <= 0;
            cfg_pad_left <= 0;
            out_y_count <= 0;
            out_x_count <= 0;
            kernel_y_count <= 0;
            kernel_x_count <= 0;
            channel_count <= 0;
        end else begin
            done <= 1'b0;
            if (start_fire) begin
                cfg_input_height <= input_height;
                cfg_input_width <= input_width;
                cfg_input_channels <= input_channels;
                cfg_output_height <= output_height;
                cfg_output_width <= output_width;
                cfg_kernel_height <= kernel_height;
                cfg_kernel_width <= kernel_width;
                cfg_stride_height <= stride_height;
                cfg_stride_width <= stride_width;
                cfg_pad_top <= pad_top;
                cfg_pad_left <= pad_left;
                out_y_count <= 0;
                out_x_count <= 0;
                kernel_y_count <= 0;
                kernel_x_count <= 0;
                channel_count <= 0;
                // Zero dimensions are illegal and deliberately rejected.
                busy <= (input_height != 0) && (input_width != 0) &&
                        (input_channels != 0) && (output_height != 0) &&
                        (output_width != 0) && (kernel_height != 0) &&
                        (kernel_width != 0) && (stride_height != 0) &&
                        (stride_width != 0);
            end else if (item_fire) begin
                if (last_in_layer) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                end else if (channel_count != cfg_input_channels-1) begin
                    channel_count <= channel_count + 1'b1;
                end else begin
                    channel_count <= 0;
                    if (kernel_x_count != cfg_kernel_width-1) begin
                        kernel_x_count <= kernel_x_count + 1'b1;
                    end else begin
                        kernel_x_count <= 0;
                        if (kernel_y_count != cfg_kernel_height-1) begin
                            kernel_y_count <= kernel_y_count + 1'b1;
                        end else begin
                            kernel_y_count <= 0;
                            if (out_x_count != cfg_output_width-1)
                                out_x_count <= out_x_count + 1'b1;
                            else begin
                                out_x_count <= 0;
                                out_y_count <= out_y_count + 1'b1;
                            end
                        end
                    end
                end
            end
        end
    end
endmodule
