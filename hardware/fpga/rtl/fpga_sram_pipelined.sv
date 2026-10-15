// fpga_sram_pipelined.sv - 修改后的 SRAM 行为模型
module fpga_sram_pipelined #(
    parameter AddrWidth = 11,
    parameter DataWidth = 32,
    parameter INIT_FILE = "xxx", // not available now
    parameter PIPELINED = 0
)(
    input  wire                     clk_i,
    input  wire                     req_i,
    input  wire [3:0]               wen_i,  // 字节使能
    input  wire [AddrWidth-1:0]     addr_i,
    input  wire [DataWidth-1:0]     data_i,
    output wire [DataWidth-1:0]     data_o,
    input  wire                     rst_ni
);

    // 内存数组
    reg [DataWidth-1:0] memory [0:(1<<AddrWidth)-1];
    reg [DataWidth-1:0] data_out_reg;
    
    // 输出赋值
    wire ram_req;
    wire [3:0] ram_wen;
    wire [AddrWidth-1:0] ram_addr;
    wire [DataWidth-1:0] ram_data;
    generate if (PIPELINED) begin : g_pipeline
        // No asynchronous reset is allowed on the BRAM-facing registers.
        // Reset masks new requests at the sampling edge; an already accepted
        // request may finish, and RAM contents survive reset.
        reg req_q;
        reg [3:0] wen_q;
        reg [AddrWidth-1:0] addr_q;
        reg [DataWidth-1:0] data_q;
        reg output_enable_q;
        reg [DataWidth-1:0] output_q;
        always @(posedge clk_i) begin
            req_q <= req_i && rst_ni;
            if (req_i && rst_ni) begin
                wen_q <= wen_i;
                addr_q <= addr_i;
                data_q <= data_i;
            end
            output_enable_q <= req_q;
            if (output_enable_q) output_q <= data_out_reg;
        end
        assign ram_req = req_q;
        assign ram_wen = wen_q;
        assign ram_addr = addr_q;
        assign ram_data = data_q;
        assign data_o = output_q;
    end else begin : g_legacy
        assign ram_req = req_i;
        assign ram_wen = wen_i;
        assign ram_addr = addr_i;
        assign ram_data = data_i;
        assign data_o = data_out_reg;
    end endgenerate
    
    // 初始化内存：只在仿真时用 $readmemh 从 hex 预载程序。
    // 综合（上板）时程序由 JTAG/OpenOCD 在运行时写入 SRAM，这里不能执行 $readmemh，
    // 否则 Vivado 会因相对路径找不到文件而报 [Synth 8-4445]。
    initial begin
`ifndef SYNTHESIS
        if (INIT_FILE != "") begin
            $readmemh(INIT_FILE, memory);
        end
`endif
    end

    // 读写操作
    always @(posedge clk_i) begin
        if (ram_req) begin
            // 读取操作
            data_out_reg <= memory[ram_addr];

            // 写入操作（基于字节使能）
            if (ram_wen[0]) memory[ram_addr][7:0]   <= ram_data[7:0];
            if (ram_wen[1]) memory[ram_addr][15:8]  <= ram_data[15:8];
            if (ram_wen[2]) memory[ram_addr][23:16] <= ram_data[23:16];
            if (ram_wen[3]) memory[ram_addr][31:24] <= ram_data[31:24];
        end
    end

endmodule
