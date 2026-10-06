// 作为 bootrom 的替代
module bootram(
    input logic clk_i,
    input logic rst_ni,
    input logic req_i,
    input logic wen_i,
    input logic [3:0] addr_i,
    input logic [31:0]data_i,
    output logic [31:0] data_o
    );
    logic [15:0][31:0] mem_q; // 16个32位的字，也就是 64B
    logic [15:0][31:0] mem_d;
    logic [31:0] data_d;
    
    always@ (posedge clk_i or negedge rst_ni) begin
	if(!rst_ni) begin
	    mem_q[0] <= 32'h800002b7; //lui t0,0x80000 base addr of sram_ff 
        mem_q[1] <= 32'h00028293;
	    //mem_q[1] <= 32'h10028313; //addi t1,t0,0x100
	    //mem_q[2] <= 32'h0062a023; //sw t1,0(t0)
	    //mem_q[3] <= 32'h0002a383; //lw t2,0(t0)
	    //mem_q[4] <= 32'h70000e37; //lui t3,0x70000 one of systolic addr
	    //mem_q[5] <= 32'h640e0e13; //addi t3,t3,0x640
	    //mem_q[6] <= 32'h006e2023; //sw t1,0(t3)
	    //mem_q[7] <= 32'h000e2e83; //lw t4,0(t3)
	    /*mem_q[4] <= 32'h00010e37; //lui t3,0x00010
	    mem_q[5] <= 32'h00002337; //lui t1,0x00002
	    mem_q[6] <= 32'h000e2e83; //lw t4,0(t3)
	    mem_q[7] <= 32'h006e8eb3; //add t4,t4,t1
	    mem_q[8] <= 32'h01de2023; //sw t4,0(t3)*/
	    //mem_q[8] <= 32'h00028067; //jr t0
        mem_q[2] <= 32'h00028067; //jr t0
	    for(int i=3;i<16;i=i+1) begin
		mem_q[i] <= '0;
	    end
	    
	    data_o <= '0;
	end
	else begin
	    mem_q <= mem_d;
	    data_o <= data_d;
	end
    end
	
    always_comb begin
	mem_d = mem_q;
	data_d = data_o;
	if(req_i) begin
	    if(wen_i) begin
		mem_d[addr_i] = data_i; 
	    end
	    data_d = mem_q[addr_i];
	end
	else begin
	    data_d = '0;
	end
    end
	    
endmodule
