// OBI instruction routing with in-order responses across region changes.
// Each destination must return exactly one response per grant, no earlier
// than the cycle after acceptance. OBI has no response backpressure.
module instr_fetch_router #(
    parameter logic [31:0] SRAM_BASE = 32'h8000_0000,
    parameter logic [31:0] SRAM_LENGTH = 32'h0000_2000,
    parameter int MAX_OUTSTANDING = 4
) (
    input logic clk_i, rst_ni,
    input logic req_i,
    input logic [31:0] addr_i,
    output logic gnt_o, rvalid_o,
    output logic [31:0] rdata_o,
    output logic imem_req_o,
    input logic imem_gnt_i, imem_rvalid_i,
    input logic [31:0] imem_rdata_i,
    output logic ext_req_o,
    input logic ext_gnt_i, ext_rvalid_i,
    input logic [31:0] ext_rdata_i
);
    localparam int COUNT_W = $clog2(MAX_OUTSTANDING + 1);
    logic [COUNT_W-1:0] outstanding_q;
    logic route_q, select_imem, drained, can_issue;

    assign select_imem = addr_i >= SRAM_BASE &&
                         addr_i < SRAM_BASE + SRAM_LENGTH;
    assign rvalid_o = rst_ni && (outstanding_q != 0) &&
                      (route_q ? imem_rvalid_i : ext_rvalid_i);
    assign rdata_o = route_q ? imem_rdata_i : ext_rdata_i;
    // The last old response can retire on the same edge as the first new
    // request. Otherwise hold the new region until all old responses retire.
    assign drained = outstanding_q == 0 ||
                     (outstanding_q == 1 && rvalid_o);
    assign can_issue = rst_ni && (drained || select_imem == route_q) &&
                       (outstanding_q < MAX_OUTSTANDING || rvalid_o);
    assign imem_req_o = req_i && can_issue && select_imem;
    assign ext_req_o = req_i && can_issue && !select_imem;
    assign gnt_o = (imem_req_o && imem_gnt_i) ||
                   (ext_req_o && ext_gnt_i);

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            outstanding_q <= '0;
            route_q <= 1'b0;
        end else begin
            case ({gnt_o, rvalid_o})
                2'b10: outstanding_q <= outstanding_q + 1'b1;
                2'b01: outstanding_q <= outstanding_q - 1'b1;
                default: ;
            endcase
            if (gnt_o) route_q <= select_imem;
        end
    end
endmodule
