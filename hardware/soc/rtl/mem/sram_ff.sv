// sram_ff.sv - 修改后的 SRAM 行为模型
module sram_ff #(
    parameter AddrWidth = 11,
    parameter DataWidth = 32,
    parameter INIT_FILE = "xxx" // not available now
)(
    input  wire                     clk_i,
    input  wire                     req_i,
    input  wire [3:0]               wen_i,  // 字节使能
    input  wire [AddrWidth-1:0]     addr_i,
    input  wire [DataWidth-1:0]     data_i,
    output wire [DataWidth-1:0]     data_o  // 改为 wire 类型
);

    // 内存数组
    reg [DataWidth-1:0] memory [0:(1<<AddrWidth)-1];
    reg [DataWidth-1:0] data_out_reg;
    
    // 输出赋值
    assign data_o = data_out_reg;
    
    // 初始化内存
    initial begin
        if (INIT_FILE != "") begin
            $readmemh(INIT_FILE, memory);
        end
    end
    
    // 读写操作
    always @(posedge clk_i) begin
        if (req_i) begin
            // 读取操作
            data_out_reg <= memory[addr_i];
            
            // 写入操作（基于字节使能）
            if (wen_i[0]) memory[addr_i][7:0]   <= data_i[7:0];
            if (wen_i[1]) memory[addr_i][15:8]  <= data_i[15:8];
            if (wen_i[2]) memory[addr_i][23:16] <= data_i[23:16];
            if (wen_i[3]) memory[addr_i][31:24] <= data_i[31:24];
        end
    end

endmodule
