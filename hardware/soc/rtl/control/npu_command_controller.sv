`timescale 1ns/1ps
// ABI 2.1 transactional program endpoint. Opcodes 1..10:
// READ WRITE MODEL_BEGIN MODEL_COMMIT INPUT_BEGIN INPUT_COMMIT START ACK RESET CLEAR.
module npu_command_controller #(
 parameter ACT_BYTES=8192,MODEL_BYTES=8192,COLS=8,MAX_OPS=16,MAX_K=1024,DEFAULT_TIMEOUT=10000000
)(
 input wire clk_i,rst_ni,input wire cmd_valid_i,output wire cmd_ready_o,
 input wire [7:0] cmd_op_i,input wire [31:0] cmd_addr_i,cmd_data_i,
 output reg rsp_valid_o,input wire rsp_ready_i,output reg [4:0] rsp_error_o,output reg [31:0] rsp_data_o,
 output wire irq_o,running_o,output reg core_reset_o,output wire validate_o,run_o,
 input wire core_ready_i,core_busy_i,validation_done_i,validation_ok_i,done_i,
 input wire [4:0] core_error_i,core_error_op_i,
 input wire [31:0] cycles_i,issues_i,model_reads_i,act_reads_i,act_writes_i,
 input wire [24*32-1:0] perf_counters_i,
 output reg [31:0] trace_control_o,trace_offset_o,
 output wire trace_read_o,output wire [15:0] trace_addr_o,
 input wire trace_valid_i,input wire [31:0] trace_data_i,input wire [4:0] trace_error_i,
 output reg [15:0] model_bytes_o,input_base_o,input_bytes_o,output_base_o,output_bytes_o,
 output reg [4:0] op_count_o,
 output reg input_i32_o,
 output wire model_we_o,output wire [15:0] model_addr_o,output wire [31:0] model_data_o,input wire model_ready_i,
 output wire act_we_o,output wire [15:0] act_addr_o,output wire [31:0] act_data_o,output wire [3:0] act_mask_o,
 input wire act_write_ready_i,act_idle_i,output wire act_flush_o,
 output wire read_valid_o,input wire read_ready_i,output wire [15:0] read_addr_o,
 input wire read_rsp_valid_i,output wire read_rsp_ready_o,input wire [31:0] read_data_i
);
 localparam IDLE=0,EXEC=1,VWAIT=2,RWAIT=3,TWAIT=4,IFLUSH=5;
 reg [2:0] state; reg [7:0] op; reg [31:0] addr,data;
 reg model_valid,input_valid,model_loading,input_loading,job_active,done,faulted;
 reg [4:0] error_code,error_op;
 reg [31:0] timeout_limit,elapsed,command_elapsed,job_id,completed_id;
 reg [15:0] model_count,input_count; reg [6:0] config_written;
 wire owned=job_active || core_busy_i || core_reset_o;
 wire locked=owned || done || faulted;
 wire config_ok=&config_written && model_bytes_o>=op_count_o*64 && model_bytes_o<=MODEL_BYTES &&
  model_bytes_o[1:0]==0 && op_count_o>0 && op_count_o<=MAX_OPS && input_bytes_o>0 && output_bytes_o>0 &&
  input_base_o[1:0]==0 && output_base_o[1:0]==0 &&
  {1'b0,input_base_o}+{1'b0,input_bytes_o}<=ACT_BYTES && {1'b0,output_base_o}+{1'b0,output_bytes_o}<=ACT_BYTES;
 wire model_write=state==EXEC && op==2 && !locked && model_loading && config_ok &&
  addr==32'h10000+model_count && model_count<model_bytes_o;
 wire input_write=state==EXEC && op==2 && !locked && input_loading &&
  addr==32'h20000+input_base_o+input_count && input_count<input_bytes_o;
 wire inspect=addr>=32'h20000 && addr<32'h20000+ACT_BYTES;
 wire commit_ok=state==EXEC && op==4 && addr==0 && data==0 && !locked &&
  model_loading && config_ok && model_count==model_bytes_o && core_ready_i;
 wire start_ok=state==EXEC && op==7 && addr==0 && data==0 && !locked && model_valid && input_valid && core_ready_i;
 assign cmd_ready_o=state==IDLE && !rsp_valid_o && !core_reset_o;
 assign irq_o=done || faulted; assign validate_o=commit_ok; assign run_o=start_ok;
 assign running_o=job_active;
 assign model_we_o=model_write; assign model_addr_o=model_count; assign model_data_o=data;
 assign act_we_o=input_write; assign act_addr_o=input_base_o+input_count; assign act_data_o=data;
 assign act_flush_o=state==IFLUSH;
 assign act_mask_o={input_count+3<input_bytes_o,input_count+2<input_bytes_o,input_count+1<input_bytes_o,1'b1};
 assign read_valid_o=state==EXEC && op==1 && inspect && !owned && addr[1:0]==0;
 assign read_addr_o=addr[15:0]; assign read_rsp_ready_o=state==RWAIT;
 wire trace_selected=(addr>='h180 && addr<'h1b4) || (addr>='h200 && addr<'h400) ||
  (addr>='h1000 && addr<'h5000);
 assign trace_read_o=state==EXEC && op==1 && trace_selected && !owned && addr[1:0]==0;
 assign trace_addr_o=addr[15:0];
 task automatic reply(input [4:0] code,input [31:0] value);
 begin rsp_valid_o<=1; rsp_error_o<=code; rsp_data_o<=value; state<=IDLE;
 if(code!=0) error_code<=code; end endtask
 task automatic invalidate;
 begin model_valid<=0; input_valid<=0; model_loading<=0; input_loading<=0;
 model_count<=0; input_count<=0; config_written<=0; end endtask
 // Synchronous payload/control reset prevents asynchronous paths into BRAM pins.
 always @(posedge clk_i) begin
  if(!rst_ni) begin
   state<=IDLE; op<=0; addr<=0; data<=0; rsp_valid_o<=0; rsp_error_o<=0; rsp_data_o<=0;
   core_reset_o<=1; job_active<=0; done<=0; faulted<=0; invalidate;
   model_bytes_o<=0; op_count_o<=0; input_base_o<=0; input_bytes_o<=0; output_base_o<=0; output_bytes_o<=0;input_i32_o<=0;
   error_code<=0; error_op<=0; timeout_limit<=DEFAULT_TIMEOUT; elapsed<=0; command_elapsed<=0; job_id<=0; completed_id<=0;
   trace_control_o<=1;trace_offset_o<=0;
  end else begin
   core_reset_o<=0; if(rsp_valid_o && rsp_ready_i) rsp_valid_o<=0;
   if(job_active) begin
    elapsed<=elapsed+1'b1;
    if(done_i) begin job_active<=0; completed_id<=job_id;
     if(core_error_i!=0) begin faulted<=1; error_code<=16+core_error_i; error_op<=core_error_op_i; end else done<=1;
    end else if(elapsed>=timeout_limit-1) begin job_active<=0; faulted<=1; core_reset_o<=1; error_code<=10; invalidate; end
   end
   if(state!=IDLE) command_elapsed<=command_elapsed+1'b1;
   case(state)
    IDLE:if(cmd_valid_i && cmd_ready_o) begin op<=cmd_op_i; addr<=cmd_addr_i; data<=cmd_data_i; state<=EXEC; command_elapsed<=0; end
    EXEC:begin
     if((op==1 || op==2) && addr[1:0]!=0) reply(3,0);
     else if(op!=1 && op!=2 && (addr!=0 || data!=0)) reply(8,0);
     else case(op)
      1:if(inspect) begin if(owned) reply(4,0); else if(read_ready_i) state<=RWAIT; end
      else if(addr>='h100 && addr<'h160) begin
       // Refuse live reads: a finished job remains stable across the bank.
       if(owned) reply(4,0);
       else reply(0,perf_counters_i[addr[6:2]*32+:32]);
      end else if(trace_selected)begin
       if(owned)reply(4,0);else state<=TWAIT;
      end else case(addr)
       'h00:reply(0,32'h00020001);
       'h04:reply(0,{25'd0,faulted,input_loading,model_loading,done,input_valid,model_valid,owned});
       'h08:reply(0,error_code); 'h0c:reply(0,'h1f);
       'h10:reply(0,model_bytes_o); 'h14:reply(0,op_count_o); 'h18:reply(0,input_base_o);
       'h1c:reply(0,input_bytes_o); 'h20:reply(0,output_base_o); 'h24:reply(0,output_bytes_o);
       'h28:reply(0,timeout_limit); 'h2c:reply(0,job_id); 'h30:reply(0,completed_id); 'h34:reply(0,error_op);
       'h38:reply(0,cycles_i); 'h3c:reply(0,issues_i); 'h40:reply(0,act_writes_i);
       'h44:reply(0,model_reads_i); 'h48:reply(0,act_reads_i); 'h4c:reply(0,elapsed);
       'h50:reply(0,ACT_BYTES); 'h54:reply(0,MODEL_BYTES); 'h58:reply(0,(4<<16)|COLS);
       'h5c:reply(0,MAX_K); 'h60:reply(0,MAX_OPS);'h64:reply(0,input_i32_o);
       'h68:reply(0,32'h00010018); // Performance extension v1, 24 counters.
       default:reply(2,0);
      endcase
      2:if(locked) reply(owned ? 4 : done ? 9 : 5,0);
      else if(addr=='h188)begin
       if((data & 32'hffffff0c)!=0)reply(8,0);else begin trace_control_o<=data;reply(0,0);end
      end else if(addr=='h18c)begin trace_offset_o<=data;reply(0,0);end
      else if(addr=='h28) begin if(data==0) reply(8,0); else begin timeout_limit<=data; reply(0,0); end end
      else if(addr=='h64)begin
       if(!model_loading || model_count!=0)reply(5,0);
       else if(data>1)reply(8,0);
       else begin input_i32_o<=data[0];config_written[6]<=1;reply(0,0);end
      end else if(addr>='h10 && addr<='h24) begin
       if(!model_loading || model_count!=0) reply(5,0);
       else if(data>65535 || (addr=='h14 && (data==0 || data>MAX_OPS))) reply(8,0);
       else begin
        case(addr)
         'h10:model_bytes_o<=data[15:0]; 'h14:op_count_o<=data[4:0]; 'h18:input_base_o<=data[15:0];
         'h1c:input_bytes_o<=data[15:0]; 'h20:output_base_o<=data[15:0]; 'h24:output_bytes_o<=data[15:0];
        endcase
        config_written[(addr-'h10)>>2]<=1; reply(0,0);
       end
      end else if(addr>='h10000 && addr<'h10000+MODEL_BYTES) begin
       if(!model_loading) reply(5,0); else if(!model_write) reply(6,0);
       else if(model_ready_i) begin model_count<=model_count+4; reply(0,0); end
      end else if(inspect) begin
       if(!input_loading) reply(5,0); else if(!input_write) reply(6,0);
       else if(act_write_ready_i) begin input_count<=input_count+4; reply(0,0); end
      end else reply(2,0);
      3:if(locked) reply(owned ? 4 : done ? 9 : 5,0); else begin invalidate; model_loading<=1; reply(0,0); end
      4:if(locked) reply(owned ? 4 : 9,0); else if(!model_loading) reply(5,0);
       else if(!config_ok || model_count!=model_bytes_o) reply(7,0);
       else if(!core_ready_i) reply(4,0); else state<=VWAIT;
      5:if(locked) reply(owned ? 4 : 9,0); else if(!model_valid) reply(5,0);
       else begin input_valid<=0; input_loading<=1; input_count<=0; reply(0,0); end
      6:if(locked || !input_loading) reply(5,0);
       else if(input_count!=(($signed({1'b0,input_bytes_o})+3)/4)*4) reply(7,0);
       else state<=IFLUSH;
      7:if(owned) reply(4,0); else if(done) reply(9,0); else if(!start_ok) reply(5,0);
       else begin input_valid<=0; job_active<=1; elapsed<=0; job_id<=job_id+1; reply(0,job_id+1); end
      8:if(owned) reply(4,0); else begin done<=0; reply(0,0); end
      9:begin core_reset_o<=1; job_active<=0; done<=0; faulted<=0; invalidate;
       trace_control_o<=1;trace_offset_o<=0;
       error_code<=0; error_op<=0; elapsed<=0; timeout_limit<=DEFAULT_TIMEOUT; reply(0,0); end
      10:begin error_code<=0; error_op<=0; reply(0,0); end
      default:reply(1,0);
     endcase
    end
    VWAIT:if(validation_done_i) begin
     if(validation_ok_i) begin model_valid<=1; model_loading<=0; reply(0,0); end
     else begin error_op<=core_error_op_i; reply(16+core_error_i,0); end
    end
    RWAIT:if(read_rsp_valid_i) reply(0,read_data_i);
    TWAIT:if(trace_valid_i)reply(trace_error_i,trace_data_i);
    IFLUSH:if(act_idle_i)begin input_valid<=1;input_loading<=0;reply(0,0);end
   endcase
   if(state!=IDLE && command_elapsed>=timeout_limit-1 && op!=9) begin
    core_reset_o<=1; job_active<=0; faulted<=1; invalidate; reply(10,0);
   end
  end
 end
endmodule
