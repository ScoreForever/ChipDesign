`timescale 1ns/1ps
// Dedicated host TAP, NOT a RISC-V Debug Module. IR=12h shifts 72-bit packets
// {opcode[7:0], byte_address[31:0], data[31:0]}, LSB first. Opcode 0 polls.
// Response = {valid,busy,overrun,error[4:0], address[31:0], data[31:0]}.
// One outstanding request. CDC data is bundled with a two-flop toggle handshake:
// request payload stays stable until reply; reply stays stable until next job.
module npu_jtag_transport #(
    parameter [31:0] IDCODE = 32'h0000_0001
) (
    input wire clk_i, rst_ni,
    input wire tck_i, tms_i, td_i, output reg td_o,
    output reg cmd_valid_o, input wire cmd_ready_i,
    output reg [7:0] cmd_op_o,
    output reg [31:0] cmd_addr_o, cmd_data_o,
    input wire rsp_valid_i, output wire rsp_ready_o,
    input wire [4:0] rsp_error_i, input wire [31:0] rsp_data_i
);
    localparam TLR=0, RTI=1, SDR=2, CDR=3, SHDR=4, E1DR=5, PDR=6, E2DR=7,
        UDR=8, SIR=9, CIR=10, SHIR=11, E1IR=12, PIR=13, E2IR=14, UIR=15;
    reg [3:0] tap, tap_next;
    reg [4:0] ir, ir_shift;
    reg [71:0] dr;
    reg [71:0] req_payload;
    reg req_toggle, pending, have_reply, overrun;
    reg [31:0] reply_data;
    reg [4:0] reply_error;
    reg ack_toggle;
    reg [31:0] rsp_mailbox_data;
    reg [4:0] rsp_mailbox_error;
    (* ASYNC_REG = "TRUE" *) reg [1:0] req_sync, ack_sync;
    reg req_seen, waiting_reply;
    (* ASYNC_REG = "TRUE" *) reg [1:0] tck_reset_sync, sys_reset_sync;
    wire tck_rst_n = tck_reset_sync[1];
    wire sys_rst_n = sys_reset_sync[1];
    // Asynchronous assertion, independently synchronized release in both domains.
    always @(posedge tck_i or negedge rst_ni)
        if (!rst_ni) tck_reset_sync <= 0;
        else tck_reset_sync <= {tck_reset_sync[0],1'b1};
    always @(posedge clk_i or negedge rst_ni)
        if (!rst_ni) sys_reset_sync <= 0;
        else sys_reset_sync <= {sys_reset_sync[0],1'b1};

    always @* begin
        case (tap)
            TLR: tap_next = tms_i ? TLR : RTI;
            RTI: tap_next = tms_i ? SDR : RTI;
            SDR: tap_next = tms_i ? SIR : CDR;
            CDR: tap_next = tms_i ? E1DR : SHDR;
            SHDR: tap_next = tms_i ? E1DR : SHDR;
            E1DR: tap_next = tms_i ? UDR : PDR;
            PDR: tap_next = tms_i ? E2DR : PDR;
            E2DR: tap_next = tms_i ? UDR : SHDR;
            UDR: tap_next = tms_i ? SDR : RTI;
            SIR: tap_next = tms_i ? TLR : CIR;
            CIR: tap_next = tms_i ? E1IR : SHIR;
            SHIR: tap_next = tms_i ? E1IR : SHIR;
            E1IR: tap_next = tms_i ? UIR : PIR;
            PIR: tap_next = tms_i ? E2IR : PIR;
            E2IR: tap_next = tms_i ? UIR : SHIR;
            UIR: tap_next = tms_i ? SDR : RTI;
            default: tap_next = TLR;
        endcase
    end
    always @(negedge tck_i or negedge tck_rst_n)
        if (!tck_rst_n) td_o <= 0;
        else td_o <= tap == SHIR ? ir_shift[0] : dr[0];

    always @(posedge tck_i or negedge tck_rst_n) begin
        if (!tck_rst_n) begin
            tap <= TLR; ir <= 1; ir_shift <= 1; dr <= 0;
            req_payload <= 0; req_toggle <= 0; ack_sync <= 0;
            pending <= 0; have_reply <= 0; overrun <= 0; reply_data <= 0; reply_error <= 0;
        end else begin
            tap <= tap_next;
            ack_sync <= {ack_sync[0],ack_toggle};
            if (pending && ack_sync[1] == req_toggle) begin
                pending <= 0; have_reply <= 1;
                reply_data <= rsp_mailbox_data; reply_error <= rsp_mailbox_error;
            end
            // TMS reset only resets scan selection. It must not cancel a CDC
            // transaction or generate a duplicate request when TCK restarts.
            case (tap)
                TLR: begin ir <= 1; overrun <= 0; end
                CIR: ir_shift <= 5'b00101;
                SHIR: ir_shift <= {td_i,ir_shift[4:1]};
                UIR: ir <= ir_shift;
                CDR: begin
                    if (ir == 1) dr <= {40'd0,IDCODE};
                    else if (ir == 'h12) dr <= {have_reply,pending,overrun,reply_error,
                        req_payload[63:32],reply_data};
                    else dr <= 0;
                end
                SHDR: begin
                    if (ir == 'h12) dr <= {td_i,dr[71:1]};
                    else if (ir == 1) dr <= {40'd0,td_i,dr[31:1]};
                    else dr <= {71'd0,td_i};
                end
                UDR: if (ir == 'h12 && dr[71:64] != 0) begin
                    if (pending) overrun <= 1;
                    else begin
                        req_payload <= dr; req_toggle <= !req_toggle;
                        pending <= 1; have_reply <= 0; overrun <= 0;
                    end
                end
                default: ;
            endcase
        end
    end

    assign rsp_ready_o = waiting_reply;
    always @(posedge clk_i or negedge sys_rst_n) begin
        if (!sys_rst_n) begin
            req_sync <= 0; req_seen <= 0; ack_toggle <= 0; waiting_reply <= 0;
            cmd_valid_o <= 0; cmd_op_o <= 0; cmd_addr_o <= 0; cmd_data_o <= 0;
            rsp_mailbox_data <= 0; rsp_mailbox_error <= 0;
        end else begin
            req_sync <= {req_sync[0],req_toggle};
            if (req_sync[1] != req_seen && !cmd_valid_o && !waiting_reply) begin
                req_seen <= req_sync[1];
                {cmd_op_o,cmd_addr_o,cmd_data_o} <= req_payload;
                cmd_valid_o <= 1;
            end
            if (cmd_valid_o && cmd_ready_i) begin
                cmd_valid_o <= 0; waiting_reply <= 1;
            end
            if (rsp_valid_i && rsp_ready_o) begin
                rsp_mailbox_data <= rsp_data_i; rsp_mailbox_error <= rsp_error_i;
                ack_toggle <= req_seen; waiting_reply <= 0;
            end
        end
    end
endmodule
