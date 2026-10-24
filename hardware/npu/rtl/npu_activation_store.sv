`timescale 1ns/1ps
// Four byte banks, synchronous unaligned four-byte reads and masked writes.
// One outstanding response; a consumed response can be replaced on that edge.
// Payload is never reset. Validity and ownership belong to the controller.
module npu_activation_store #(parameter BYTES=4096) (
    input wire clk, rst,
    input wire read_valid, output wire read_ready, input wire [15:0] read_addr,
    output reg rsp_valid, input wire rsp_ready, output reg [31:0] rsp_data,
    input wire write_valid, input wire [15:0] write_addr,
    input wire [31:0] write_data, input wire [3:0] write_mask
);
    localparam DEPTH=BYTES/4;
    localparam AW=$clog2(DEPTH);
    wire [31:0] read_word;
    assign read_ready=!rst && (!rsp_valid || rsp_ready);
    genvar b;
    generate for(b=0;b<4;b=b+1) begin:g_bank
        reg [7:0] mem[0:DEPTH-1];
        // Rotate banks by incrementing only the word index, avoiding a full
        // byte-address adder and its low-bit carry chain on every RAM port.
        wire [AW-1:0] ra=read_addr[AW+1:2]+(b<read_addr[1:0]);
        wire [AW-1:0] wa=write_addr[AW+1:2]+(b<write_addr[1:0]);
        wire [1:0] ro=(b-read_addr[1:0])&3;
        wire [1:0] wo=(b-write_addr[1:0])&3;
        reg [7:0] q;
        always @(posedge clk) begin
            if(read_valid && read_ready) q<=mem[ra];
            if(!rst && write_valid && write_mask[wo])
                mem[wa]<=write_data[wo*8+:8];
        end
        // Rotating registered bank data uses the request's low address bits.
        assign read_word[b*8+:8]=q;
    end endgenerate
    reg [1:0] rotation;
    integer i;
    always @* for(i=0;i<4;i=i+1)
        rsp_data[i*8+:8]=read_word[((rotation+i)&3)*8+:8];
    always @(posedge clk) begin
        if(rst) begin rsp_valid<=0; rotation<=0; end
        else if(read_ready) begin
            rsp_valid<=read_valid;
            if(read_valid) rotation<=read_addr[1:0];
        end
    end
    initial if(BYTES<64 || BYTES>32768 || (BYTES&(BYTES-1))!=0)
        $fatal(1,"activation capacity must be a power of two in 64..32768");
endmodule
