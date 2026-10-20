`timescale 1ns/1ps
// One physical synchronous 32-bit port. Up to 64-bit logical rows are
// assembled over two accesses; no shadow copy of the weight array exists.
// Reads return in order and are consumed on read_valid && read_response_ready.
// A stalled response holds both ports. The next request may be accepted on
// the consumption edge. Writers must obey write_ready.
module npu_weight_store #(
    parameter DATA_WIDTH=64,
    parameter WORDS=256,
    parameter ADDR_WIDTH=16
) (
    input wire clk, rst,
    input wire write_valid, output wire write_ready,
    input wire [ADDR_WIDTH-1:0] write_addr,
    input wire [DATA_WIDTH-1:0] write_data,
    input wire read_request, output wire read_ready,
    input wire [ADDR_WIDTH-1:0] read_addr,
    output wire read_valid, input wire read_response_ready,
    output wire [DATA_WIDTH-1:0] read_data
);
    localparam HALVES=(DATA_WIDTH>32) ? 2 : 1;
    localparam IDLE=0, READ_HIGH=1, RETURN=2, WRITE_HIGH=3;
    reg [1:0] state;
    reg [10:0] saved_addr;
    reg [31:0] low_data, high_write;
    wire available=(state==IDLE || (state==RETURN && read_response_ready)) && !rst;
    assign write_ready=available;
    assign read_ready=available && !write_valid;
    assign read_valid=(state==RETURN) && !rst;
    wire write_fire=write_valid && write_ready;
    wire read_fire=read_request && read_ready;
    wire [63:0] padded_write={{(64-DATA_WIDTH){1'b0}},write_data};
    reg mem_enable, mem_write;
    reg [10:0] mem_addr;
    reg [31:0] mem_wdata;
    wire [31:0] mem_q;

    generate if (DATA_WIDTH<1 || DATA_WIDTH>64 || WORDS*HALVES>2048)
    begin : invalid_geometry
        initial $fatal(1,"weight store does not fit RA1SHD_2048x32M8");
    end endgenerate

    always @* begin
        mem_enable=0; mem_write=0; mem_addr=0; mem_wdata=0;
        if (!rst) begin
            if (state==READ_HIGH || state==WRITE_HIGH) begin
                mem_enable=1; mem_write=(state==WRITE_HIGH);
                mem_addr=saved_addr+1'b1; mem_wdata=high_write;
            end else if (write_fire) begin
                mem_enable=1; mem_write=1;
                mem_addr=write_addr*HALVES; mem_wdata=padded_write[31:0];
            end else if (read_fire) begin
                mem_enable=1; mem_addr=read_addr*HALVES;
            end
        end
    end

`ifdef NPU_USE_RA1SHD_WEIGHT
    // The ASIC build must provide the matching Liberty/LEF/GDS/CDL and
    // simulation model. Do not include foundry IP in the public source tree.
    RA1SHD_2048x32M8 i_sram (
        .CLK(clk),.CEN(~mem_enable),.OEN(1'b0),
        .WEN({4{!mem_write}}),.A(mem_addr),.D(mem_wdata),.Q(mem_q)
    );
`else
    // Portable functional/FPGA model of the same synchronous port. This is
    // NOT a claim of ASIC SRAM inference; the macro define is required there.
    reg [31:0] mem[0:WORDS*HALVES-1];
    reg [31:0] q;
    always @(posedge clk) if (mem_enable) begin
        if (mem_write) mem[mem_addr] <= mem_wdata;
        else q <= mem[mem_addr];
    end
    assign mem_q=q;
`endif

    generate if (HALVES==2) begin : g_wide
        assign read_data={mem_q,low_data};
    end else begin : g_narrow
        assign read_data=mem_q[DATA_WIDTH-1:0];
    end endgenerate

    always @(posedge clk) begin
        if (rst) begin
            state<=IDLE; saved_addr<=0;
            // Payload and SRAM contents need no reset; state owns validity.
        end else begin
            case (state)
                READ_HIGH: begin low_data<=mem_q; state<=RETURN; end
                WRITE_HIGH: state<=IDLE;
                default: if (state!=RETURN || read_response_ready) begin
                    state<=IDLE;
                    if (write_fire) begin
                        saved_addr<=write_addr*HALVES;
                        high_write<=padded_write[63:32];
                        state<=(HALVES==2) ? WRITE_HIGH : IDLE;
                    end else if (read_fire) begin
                        saved_addr<=read_addr*HALVES;
                        state<=(HALVES==2) ? READ_HIGH : RETURN;
                    end
                end
            endcase
        end
    end
endmodule
