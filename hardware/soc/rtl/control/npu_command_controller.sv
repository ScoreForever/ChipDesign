`timescale 1ns/1ps
// Transport-independent, single-command ready/valid endpoint. No instruction CPU.
// Model replacement is transactional with respect to RUN eligibility, not RAM:
// MODEL_BEGIN invalidates the old model before the first byte can be replaced.
module npu_command_controller #(
    parameter [31:0] DEFAULT_TIMEOUT = 100000,
    parameter ENABLE_EXTENSION = 0
) (
    input wire clk_i, rst_ni,
    input wire cmd_valid_i, output wire cmd_ready_o,
    input wire [7:0] cmd_op_i,
    input wire [31:0] cmd_addr_i, cmd_data_i,
    output reg rsp_valid_o, input wire rsp_ready_i,
    output reg [4:0] rsp_error_o, output reg [31:0] rsp_data_o,
    output wire irq_o,

    output reg core_reset_o, output wire core_start_o,
    input wire core_ready_i, core_busy_i, core_done_i,
    input wire [255:0] core_logits_i,
    output reg [3:0] class_count_o,
    output wire activation_we_o, output wire [15:0] activation_addr_o,
    output wire [7:0] activation_data_o,
    output wire weight_we_o, input wire weight_ready_i, output wire [15:0] weight_addr_o,
    output wire [63:0] weight_data_o,
    output wire parameter_we_o, output wire [1:0] parameter_layer_o,
    output wire [255:0] bias_o, multiplier_o, output wire [47:0] shift_o,
    output wire debug_en_o, output wire [15:0] debug_addr_o,
    input wire [31:0] debug_data_i, input wire debug_valid_i,
    input wire [31:0] perf_total_i, perf_weight_rows_i, perf_issues_i,
    input wire [31:0] perf_retires_i, perf_peak_i,
    input wire [191:0] perf_layers_i,
    input wire perf_valid_i, perf_overflow_i,

    // Future module register/descriptor programming. Disabled endpoints reject
    // requests explicitly; no promise of a programmable compute graph today.
    output wire ext_valid_o, input wire ext_ready_i,
    output wire ext_write_o, output wire [31:0] ext_addr_o, ext_data_o,
    input wire ext_rsp_valid_i, output wire ext_rsp_ready_o,
    input wire [31:0] ext_rsp_data_i, input wire [4:0] ext_rsp_error_i
);
    localparam READ=1, WRITE=2, MODEL_BEGIN=3, PARAM_COMMIT=4, MODEL_COMMIT=5,
        INPUT_BEGIN=6, INPUT_COMMIT=7, START=8, ACK=9, CLEAR_ERROR=10,
        SOFT_RESET=11, MODULE_WRITE=32, MODULE_READ=33;
    localparam E_OPCODE=1, E_ADDRESS=2, E_ALIGN=3, E_BUSY=4,
        E_STATE=5, E_ORDER=6, E_INCOMPLETE=7, E_PARAMETER=8,
        E_RESULT_PENDING=9, E_TIMEOUT=10, E_UNSUPPORTED=11, E_NOT_READY=12;
    localparam IDLE=0, EXEC=1, PACK=2, EXT_SEND=3, EXT_WAIT=4;
    reg [2:0] state;
    reg [7:0] op;
    reg [31:0] addr, data;
    reg model_valid, input_valid, model_loading, input_loading;
    reg job_active, done, faulted, irq_enable;
    reg [4:0] error_code;
    reg [31:0] error_addr;
    reg [8:0] weight_count;  // ordered 32-bit halves, exactly 482
    reg [6:0] input_count;   // ordered packed words, exactly 80
    reg [3:0] param_committed;
    reg [23:0] param_written;
    reg [1:0] stage_layer;
    reg [255:0] bias_stage, mult_stage;
    reg [47:0] shift_stage;
    reg [31:0] weight_low;
    reg [1:0] pack_byte;
    reg [255:0] result_logits;
    reg [31:0] timeout_limit, elapsed, job_id, completed_id;
    reg [31:0] ext_elapsed;
    wire owned = job_active || core_busy_i || core_reset_o;
    wire locked = owned || done || faulted;
    wire param_layer_ok = data == 0 || data == 1 || data == 3;
    wire param_complete = data == 3 ? (&param_written[7:0]) : (&param_written);
    wire [7:0] suboffset = addr[7:0];
    wire [2:0] lane = addr[4:2];
    wire param_address = addr >= 32'h4000 && addr < 32'h4400 &&
        addr[9:8] != 2 && (suboffset <= 8'h1c ||
        (addr[9:8] != 3 && suboffset >= 8'h20 && suboffset <= 8'h5c));
    wire model_weight_write = state == EXEC && op == WRITE && !locked &&
        model_loading && addr == 32'h2000 + {21'd0,weight_count,2'b00} && weight_count < 482;
    wire commit_parameter = state == EXEC && op == PARAM_COMMIT && !locked &&
        model_loading && addr == 0 && param_layer_ok && param_complete &&
        stage_layer == data[1:0] && !param_committed[data[1:0]];
    wire run_allowed = !locked && model_valid && input_valid && core_ready_i;
    wire inspect_address = (addr >= 32'h2000 && addr < 32'h2800) ||
        (addr >= 32'h3000 && addr < 32'h3400) || param_address ||
        (addr >= 32'h5000 && addr < 32'h5020);

    assign cmd_ready_o = state == IDLE && !rsp_valid_o && !core_reset_o;
    assign irq_o = irq_enable && (done || faulted);
    assign core_start_o = state == EXEC && op == START && addr == 0 && data == 0 && run_allowed;
    assign activation_we_o = state == PACK;
    assign activation_addr_o = {7'd0,input_count,2'b00} + {14'd0,pack_byte};
    assign activation_data_o = data[pack_byte*8 +: 8];
    assign weight_we_o = model_weight_write && weight_count[0];
    assign weight_addr_o = {8'd0,weight_count[8:1]};
    assign weight_data_o = {data,weight_low};
    assign parameter_we_o = commit_parameter;
    assign parameter_layer_o = data[1:0];
    assign bias_o = bias_stage;
    assign multiplier_o = mult_stage;
    assign shift_o = shift_stage;
    assign debug_en_o = state == EXEC && op == READ && addr[1:0] == 0 &&
        inspect_address && !owned;
    assign debug_addr_o = addr[15:0] - 16'h2000;
    assign ext_valid_o = state == EXT_SEND;
    assign ext_write_o = op == MODULE_WRITE;
    assign ext_addr_o = addr;
    assign ext_data_o = data;
    assign ext_rsp_ready_o = state == EXT_WAIT;

    task reply(input [4:0] code, input [31:0] value);
        begin
            rsp_valid_o <= 1;
            rsp_error_o <= code;
            rsp_data_o <= value;
            state <= IDLE;
            if (code != 0 && (error_code == 0 || code == E_TIMEOUT)) begin
                error_code <= code;
                error_addr <= addr;
            end
        end
    endtask

    integer i;
    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state <= IDLE; op <= 0; addr <= 0; data <= 0;
            rsp_valid_o <= 0; rsp_error_o <= 0; rsp_data_o <= 0;
            core_reset_o <= 1; class_count_o <= 4;
            model_valid <= 0; input_valid <= 0; model_loading <= 0; input_loading <= 0;
            job_active <= 0; done <= 0; faulted <= 0; irq_enable <= 1;
            error_code <= 0; error_addr <= 0;
            weight_count <= 0; input_count <= 0; param_committed <= 0; param_written <= 0; stage_layer <= 0;
            bias_stage <= 0; mult_stage <= 0; shift_stage <= 0; weight_low <= 0; pack_byte <= 0;
            result_logits <= 0; timeout_limit <= DEFAULT_TIMEOUT; elapsed <= 0;
            job_id <= 0; completed_id <= 0; ext_elapsed <= 0;
        end else begin
            core_reset_o <= 0;
            if (rsp_valid_o && rsp_ready_i) rsp_valid_o <= 0;
            if (job_active) begin
                if (elapsed != 32'hffffffff) elapsed <= elapsed + 1;
                if (core_done_i) begin
                    job_active <= 0; done <= 1; completed_id <= job_id;
                    result_logits <= core_logits_i;
                end else if (elapsed >= timeout_limit - 1) begin
                    job_active <= 0; faulted <= 1; core_reset_o <= 1;
                    // TIMEOUT supersedes earlier diagnostic errors.
                    error_code <= E_TIMEOUT; error_addr <= 0;
                end
            end
            case (state)
                IDLE: if (cmd_valid_i && cmd_ready_o) begin
                    op <= cmd_op_i; addr <= cmd_addr_i; data <= cmd_data_i; state <= EXEC;
                    ext_elapsed<=0;
                end
                EXEC: begin
                    if ((op == READ || op == WRITE || op == MODULE_READ || op == MODULE_WRITE) && addr[1:0] != 0)
                        reply(E_ALIGN,0);
                    else if (op != READ && op != WRITE && op != MODULE_READ && op != MODULE_WRITE &&
                        (addr != 0 || (op != PARAM_COMMIT && data != 0))) reply(E_PARAMETER,0);
                    else case (op)
                        READ: begin
                            if (addr >= 32'h0100 && addr < 32'h0120)
                                reply(0,result_logits[addr[4:2]*32 +: 32]);
                            else if (inspect_address) begin
                                if (owned) reply(E_BUSY,0);
                                else if (debug_valid_i) reply(0,debug_data_i);
                                else if (ext_elapsed >= timeout_limit-1) begin
                                    faulted<=1; core_reset_o<=1; reply(E_TIMEOUT,0);
                                end else ext_elapsed<=ext_elapsed+1'b1;
                            end else case (addr)
                                'h0000: reply(0,32'h0001_0000);
                                'h0004: reply(0,{24'd0,faulted,input_loading,model_loading,
                                    (error_code != 0),done,input_valid,model_valid,owned});
                                'h0008: reply(0,{27'd0,error_code});
                                'h000c: reply(0,32'h0000_001f | (ENABLE_EXTENSION ? 32'h20 : 0));
                                'h0010: reply(0,{28'd0,class_count_o});
                                'h0014: reply(0,timeout_limit);
                                'h0018: reply(0,job_id);
                                'h001c: reply(0,completed_id);
                                'h0020: reply(0,perf_total_i);
                                'h0024: reply(0,elapsed);
                                'h0028: reply(0,{23'd0,weight_count});
                                'h002c: reply(0,{25'd0,input_count});
                                'h0030: reply(0,1); // fixed TinyCNN-8-Flat backend ID
                                'h0034: reply(0,error_addr);
                                'h0038: reply(0,{28'd0,param_committed});
                                'h003c: reply(0,{31'd0,irq_enable});
                                'h0040: reply(0,perf_weight_rows_i);
                                'h0044: reply(0,perf_issues_i);
                                'h0048: reply(0,perf_retires_i);
                                'h004c: reply(0,perf_peak_i);
                                'h0050: reply(0,{30'd0,perf_overflow_i,perf_valid_i});
                                'h0060,'h0064,'h0068,'h006c,'h0070,'h0074:
                                    reply(0,perf_layers_i[((addr-32'h60)>>2)*32 +: 32]);
                                default: reply(E_ADDRESS,0);
                            endcase
                        end
                        WRITE: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else if (addr == 'h003c) begin
                                if (data > 1) reply(E_PARAMETER,0);
                                else begin irq_enable <= data[0]; reply(0,0); end
                            end else if (addr == 'h0014) begin
                                if (data == 0) reply(E_PARAMETER,0);
                                else begin timeout_limit <= data; reply(0,0); end
                            end else if (addr == 'h0010) begin
                                if (!model_loading) reply(E_STATE,0);
                                else if (data < 1 || data > 8) reply(E_PARAMETER,0);
                                else begin class_count_o <= data[3:0]; reply(0,0); end
                            end else if (addr >= 'h2000 && addr < 'h2800) begin
                                if (!model_loading) reply(E_STATE,0);
                                else if (!model_weight_write) reply(E_ORDER,0);
                                else if (!weight_count[0] || weight_ready_i) begin
                                    if (!weight_count[0]) weight_low <= data;
                                    weight_count <= weight_count + 1;
                                    reply(0,0);
                                end else if (ext_elapsed >= timeout_limit-1) begin
                                    faulted<=1; core_reset_o<=1; reply(E_TIMEOUT,0);
                                end else ext_elapsed<=ext_elapsed+1'b1;
                            end else if (addr >= 'h1000 && addr < 'h1140) begin
                                if (!input_loading) reply(E_STATE,0);
                                else if (addr != 'h1000 + {23'd0,input_count,2'b00} || input_count >= 80)
                                    reply(E_ORDER,0);
                                else begin pack_byte <= 0; state <= PACK; end
                            end else if (param_address) begin
                                if (!model_loading) reply(E_STATE,0);
                                // The address names the target layer; a staging
                                // group must not mix values from different layers.
                                else if (param_committed[addr[9:8]]) reply(E_STATE,0);
                                else if (param_written != 0 && stage_layer != addr[9:8]) reply(E_ORDER,0);
                                else if (suboffset <= 'h1c) begin
                                    stage_layer <= addr[9:8];
                                    bias_stage[lane*32 +: 32] <= data;
                                    param_written[lane] <= 1; reply(0,0);
                                end else if (suboffset < 'h40) begin
                                    if (data[31]) reply(E_PARAMETER,0);
                                    else begin stage_layer <= addr[9:8]; mult_stage[lane*32 +: 32] <= data;
                                        param_written[8+lane] <= 1; reply(0,0); end
                                end else if ($signed(data) < -31 || $signed(data) > 31) reply(E_PARAMETER,0);
                                else begin stage_layer <= addr[9:8]; shift_stage[lane*6 +: 6] <= data[5:0];
                                    param_written[16+lane] <= 1; reply(0,0); end
                            end else reply(E_ADDRESS,0);
                        end
                        MODEL_BEGIN: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else begin
                                model_valid <= 0; model_loading <= 1; input_valid <= 0; input_loading <= 0;
                                weight_count <= 0; input_count <= 0; param_committed <= 0; param_written <= 0;
                                bias_stage <= 0; mult_stage <= 0; shift_stage <= 0;
                                reply(0,0);
                            end
                        end
                        PARAM_COMMIT: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else if (!model_loading) reply(E_STATE,0);
                            else if (!param_layer_ok) reply(E_PARAMETER,0);
                            else if (param_committed[data[1:0]]) reply(E_STATE,0);
                            else if (param_written != 0 && stage_layer != data[1:0]) reply(E_ORDER,0);
                            else if (!param_complete) reply(E_INCOMPLETE,0);
                            else begin
                                param_committed[data[1:0]] <= 1;
                                param_written <= 0; bias_stage <= 0; mult_stage <= 0; shift_stage <= 0;
                                reply(0,0);
                            end
                        end
                        MODEL_COMMIT: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else if (!model_loading) reply(E_STATE,0);
                            else if (weight_count != 482 || param_committed != 4'b1011 || param_written != 0)
                                reply(E_INCOMPLETE,0);
                            else begin model_valid <= 1; model_loading <= 0; reply(0,0); end
                        end
                        INPUT_BEGIN: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else if (!model_valid) reply(E_STATE,0);
                            else begin input_valid <= 0; input_loading <= 1; input_count <= 0; reply(0,0); end
                        end
                        INPUT_COMMIT: begin
                            if (locked) reply(owned ? E_BUSY : done ? E_RESULT_PENDING : E_STATE,0);
                            else if (!input_loading) reply(E_STATE,0);
                            else if (input_count != 80) reply(E_INCOMPLETE,0);
                            else begin input_valid <= 1; input_loading <= 0; reply(0,0); end
                        end
                        START: begin
                            if (owned) reply(E_BUSY,0);
                            else if (done) reply(E_RESULT_PENDING,0);
                            else if (faulted || !model_valid || !input_valid) reply(E_STATE,0);
                            else if (!core_ready_i) reply(E_NOT_READY,0);
                            else begin
                                input_valid <= 0; job_active <= 1; elapsed <= 0; job_id <= job_id + 1;
                                reply(0,job_id+1);
                            end
                        end
                        ACK: begin
                            // Do not allow ACK to erase a simultaneous completion.
                            if (owned) reply(E_BUSY,0);
                            else begin done <= 0; result_logits <= 0; reply(0,0); end
                        end
                        CLEAR_ERROR: begin error_code <= 0; error_addr <= 0; reply(0,0); end
                        SOFT_RESET: begin
                            core_reset_o <= 1; job_active <= 0; done <= 0; faulted <= 0;
                            model_valid <= 0; input_valid <= 0; model_loading <= 0; input_loading <= 0;
                            weight_count <= 0; input_count <= 0; param_written <= 0; param_committed <= 0;
                            bias_stage <= 0; mult_stage <= 0; shift_stage <= 0; weight_low <= 0;
                            result_logits <= 0; elapsed <= 0; job_id <= 0; completed_id <= 0;
                            error_code <= 0; error_addr <= 0; class_count_o <= 4;
                            timeout_limit <= DEFAULT_TIMEOUT; irq_enable <= 1; reply(0,0);
                        end
                        MODULE_READ, MODULE_WRITE: begin
                            if (locked || model_loading || input_loading) reply(owned ? E_BUSY : E_STATE,0);
                            else if (!ENABLE_EXTENSION) reply(E_UNSUPPORTED,0);
                            else begin state <= EXT_SEND; ext_elapsed <= 0; end
                        end
                        default: reply(E_OPCODE,0);
                    endcase
                end
                PACK: begin
                    if (pack_byte == 3) begin input_count <= input_count + 1; reply(0,0); end
                    else pack_byte <= pack_byte + 1;
                end
                EXT_SEND, EXT_WAIT: begin
                    ext_elapsed <= ext_elapsed + 1;
                    if (state == EXT_SEND && ext_ready_i) state <= EXT_WAIT;
                    if (state == EXT_WAIT && ext_rsp_valid_i) reply(ext_rsp_error_i,ext_rsp_data_i);
                    else if (ext_elapsed >= timeout_limit - 1) begin
                        faulted <= 1; core_reset_o <= 1; reply(E_TIMEOUT,0);
                    end
                end
                default: begin faulted <= 1; core_reset_o <= 1; reply(E_STATE,0); end
            endcase
        end
    end
endmodule
