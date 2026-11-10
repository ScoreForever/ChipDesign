// Frozen pre-area-optimization lookup from 8c16462, used only by the cycle-equivalence test.
module npu_tensor_lookup_reference #(parameter MAX_OPS=128) (
    input wire clk,rst,seed,start,save,
    input wire [7:0] pc,
    input wire input_i32,src_i32,dst_i32,add_op,
    input wire [15:0] input_first,input_end,src,src_end,src2,src2_end,dst,dst_end,
    output reg done,source_known,source2_known
);
    localparam ADDR_BITS=$clog2(MAX_OPS+1);
    (* ram_style = "distributed" *) reg [33:0] records[0:(1<<ADDR_BITS)-1];
    reg [33:0] record;
    reg [7:0] index;
    reg active,have_record;
    wire [15:0] record_base=record[15:0],record_end=record[31:16];
    wire record_valid=record[33],record_i32=record[32];
    wire overlap=record_valid && dst<record_end && record_base<dst_end;
    wire write_enable=seed || save || (active && have_record && overlap);
    wire [ADDR_BITS-1:0] address=seed ? 8'd0 : save ? pc+8'd1 : index;
    wire [33:0] write_record=seed ? {1'b1,input_i32,input_end,input_first} :
        save ? {1'b1,dst_i32,dst_end,dst} : {1'b0,record[32:0]};
    always @(posedge clk) begin
        if(!rst && write_enable) records[address]<=write_record;
        if(active && !have_record) record<=records[address];
    end
    always @(posedge clk) begin
        if(rst) begin
            active<=0; have_record<=0; done<=0; index<=0;
            source_known<=0; source2_known<=0;
        end else begin
            done<=0;
            if(start) begin
                active<=1; have_record<=0; index<=0;
                source_known<=0; source2_known<=!add_op;
            end else if(active) begin
                have_record<=!have_record;
                if(have_record) begin
                    if(record_valid && record_i32==src_i32 && src>=record_base && src_end<=record_end)
                        source_known<=1;
                    if(record_valid && !record_i32 && src2>=record_base && src2_end<=record_end)
                        source2_known<=1;
                    if(index==pc) begin active<=0; done<=1; end
                    else index<=index+1'b1;
                end
            end
        end
    end
endmodule

