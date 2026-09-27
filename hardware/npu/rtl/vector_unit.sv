`timescale 1ns/1ps
// INT8 SIMD ALU. Lane 0 occupies the least-significant packed bits.
// Control and data are accepted together on in_valid && in_ready.
module vector_unit #(
    parameter LANES = 8,
    parameter DATA_WIDTH = 8
) (
    input  wire                             clk,
    input  wire                             rst,
    input  wire                             in_valid,
    output wire                             in_ready,
    input  wire [2:0]                       opcode,
    input  wire                             src_a_sel,
    input  wire                             src_b_sel,
    input  wire                             dst_sel,
    input  wire [DATA_WIDTH-1:0]            scalar,
    input  wire [LANES-1:0]                 lane_mask,
    input  wire [LANES*DATA_WIDTH-1:0]      vec_a,
    input  wire [LANES*DATA_WIDTH-1:0]      vec_b,
    output reg  [LANES*DATA_WIDTH-1:0]      vec_out,
    output reg                              out_valid,
    input  wire                             out_ready
);
    localparam [2:0] VU_ADD = 3'd0, VU_SUB = 3'd1,
                     VU_MAX = 3'd2, VU_MIN = 3'd3, VU_MOV = 3'd4;
    localparam VECTOR_A = 1'b0, VACC = 1'b1;
    localparam VECTOR_B = 1'b0, SCALAR = 1'b1;
    localparam OUTPUT = 1'b0, DEST_VACC = 1'b1;
    localparam [DATA_WIDTH-1:0] MAX_VALUE = {1'b0, {(DATA_WIDTH-1){1'b1}}};
    localparam [DATA_WIDTH-1:0] MIN_VALUE = {1'b1, {(DATA_WIDTH-1){1'b0}}};

    reg [LANES*DATA_WIDTH-1:0] vacc;
    wire advance = !out_valid || out_ready;
    wire input_fire = in_valid && in_ready;
    wire opcode_valid = (opcode == VU_ADD) || (opcode == VU_SUB) ||
                        (opcode == VU_MAX) || (opcode == VU_MIN) ||
                        (opcode == VU_MOV);
    wire subtract = (opcode != VU_ADD);
    wire [LANES*DATA_WIDTH-1:0] result;

    // Conservatively stall all operations, including VACC writes, while an
    // output is blocked. A consumed output can be replaced on the same edge.
    assign in_ready = advance && !rst;

    genvar lane;
    generate
        for (lane = 0; lane < LANES; lane = lane + 1) begin : alu_lane
            wire [DATA_WIDTH-1:0] a = (src_a_sel == VACC) ?
                vacc[lane*DATA_WIDTH +: DATA_WIDTH] :
                vec_a[lane*DATA_WIDTH +: DATA_WIDTH];
            wire [DATA_WIDTH-1:0] b = (src_b_sel == SCALAR) ? scalar :
                vec_b[lane*DATA_WIDTH +: DATA_WIDTH];
            // Sign extend BEFORE arithmetic: signed INT8 differences span
            // -255..255, so their sign is reliable only at 9-bit precision.
            wire signed [DATA_WIDTH:0] a_ext = $signed({a[DATA_WIDTH-1], a});
            wire signed [DATA_WIDTH:0] b_ext = $signed({b[DATA_WIDTH-1], b});
            // One shared add/sub path: complement B and set carry-in for
            // SUB/MAX/MIN. MAX/MIN reuse the full-width difference sign.
            wire signed [DATA_WIDTH:0] b_add =
                b_ext ^ {(DATA_WIDTH+1){subtract}};
            wire signed [DATA_WIDTH:0] arithmetic =
                a_ext + b_add + {{DATA_WIDTH{1'b0}}, subtract};
            // A full-width result fits in DATA_WIDTH iff its two top bits
            // agree. Otherwise clamp according to the full-width sign.
            wire [DATA_WIDTH-1:0] saturated =
                (arithmetic[DATA_WIDTH] == arithmetic[DATA_WIDTH-1]) ?
                arithmetic[DATA_WIDTH-1:0] :
                (arithmetic[DATA_WIDTH] ? MIN_VALUE : MAX_VALUE);
            reg [DATA_WIDTH-1:0] lane_result;
            always @* begin
                case (opcode)
                    VU_ADD, VU_SUB: lane_result = saturated;
                    VU_MAX: lane_result = arithmetic[DATA_WIDTH] ? b : a;
                    VU_MIN: lane_result = arithmetic[DATA_WIDTH] ? a : b;
                    VU_MOV: lane_result = a;
                    default: lane_result = {DATA_WIDTH{1'b0}};
                endcase
            end
            assign result[lane*DATA_WIDTH +: DATA_WIDTH] = lane_mask[lane] ?
                lane_result : {DATA_WIDTH{1'b0}};

            // VACC commits at the acceptance edge, so the very next accepted
            // operation sees this value without forwarding or extra bubbles.
            always @(posedge clk) begin
                if (rst)
                    vacc[lane*DATA_WIDTH +: DATA_WIDTH] <= {DATA_WIDTH{1'b0}};
                else if (input_fire && opcode_valid && dst_sel == DEST_VACC && lane_mask[lane])
                    vacc[lane*DATA_WIDTH +: DATA_WIDTH] <= lane_result;
            end
        end
    endgenerate

    // OUTPUT requests become valid just after their acceptance edge; the
    // earliest output handshake is the next edge (one registered stage).
    // VACC requests and reserved opcodes produce no output transaction.
    always @(posedge clk) begin
        if (rst) begin
            vec_out <= {LANES*DATA_WIDTH{1'b0}};
            out_valid <= 1'b0;
        end else if (advance) begin
            out_valid <= input_fire && opcode_valid && dst_sel == OUTPUT;
            if (input_fire && opcode_valid && dst_sel == OUTPUT)
                vec_out <= result;
        end
    end
endmodule
