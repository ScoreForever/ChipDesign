`timescale 1ns/1ps
module npu_control_subsystem #(
 parameter ACT_BYTES=4096,MODEL_BYTES=8192,COLS=8,MAX_OPS=16,MAX_K=1024,DEFAULT_TIMEOUT=10000000
)(
 input wire clk_i,rst_ni,cmd_valid_i,output wire cmd_ready_o,
 input wire [7:0] cmd_op_i,input wire [31:0] cmd_addr_i,cmd_data_i,
 output wire rsp_valid_o,input wire rsp_ready_i,output wire [4:0] rsp_error_o,
 output wire [31:0] rsp_data_o,output wire irq_o
);
 wire core_reset,validate,run,ready,busy,vdone,vok,done,running;
 // Only synchronously changing registers may drive inferred RAM controls.
 reg rst;
 always @(posedge clk_i) rst<=!rst_ni || core_reset;
 wire [4:0] error,error_op,op_count;wire input_i32;
 wire [15:0] model_bytes,input_base,input_bytes,output_base,output_bytes;
 wire [31:0] cycles,issues,model_reads,act_reads,act_writes;
 wire [24*32-1:0] perf_counters;
 wire trace_clear,trace_active,trace_read,trace_valid;
 wire [127:0] trace_sample;wire [3:0] trace_events;
 wire [31:0] trace_descriptor,trace_control,trace_offset,trace_data;
 wire [4:0] trace_error;wire [15:0] trace_addr;
 npu_trace_capture i_trace(.clk(clk_i),.clear(trace_clear),.active(trace_active),
  .sample(trace_sample),.events(trace_events),.descriptor(trace_descriptor),
  .control(trace_control),.offset(trace_offset),.read_request(trace_read),.read_addr(trace_addr),
  .read_valid(trace_valid),.read_data(trace_data),.read_error(trace_error));
 wire mw,mready,mv,mr,mrv; wire [15:0] mwa,ma; wire [31:0] mwd,md;
 wire host_aw,host_ar,host_arr,host_rsp_ready;
 wire [15:0] host_wa,host_ra; wire [31:0] host_wd; wire [3:0] host_wm;
 wire av,arv,arr,aw; wire [15:0] aa,wa; wire [31:0] wd,act_data; wire [3:0] wm;
 npu_command_controller #(.ACT_BYTES(ACT_BYTES),.MODEL_BYTES(MODEL_BYTES),.COLS(COLS),
  .MAX_OPS(MAX_OPS),.MAX_K(MAX_K),.DEFAULT_TIMEOUT(DEFAULT_TIMEOUT)) i_controller (
  .clk_i(clk_i),.rst_ni(rst_ni),.cmd_valid_i(cmd_valid_i),.cmd_ready_o(cmd_ready_o),
  .cmd_op_i(cmd_op_i),.cmd_addr_i(cmd_addr_i),.cmd_data_i(cmd_data_i),
  .rsp_valid_o(rsp_valid_o),.rsp_ready_i(rsp_ready_i),.rsp_error_o(rsp_error_o),.rsp_data_o(rsp_data_o),
  .irq_o(irq_o),.running_o(running),.core_reset_o(core_reset),.validate_o(validate),.run_o(run),
  .core_ready_i(ready),.core_busy_i(busy),.validation_done_i(vdone),.validation_ok_i(vok),.done_i(done),
  .core_error_i(error),.core_error_op_i(error_op),.cycles_i(cycles),.issues_i(issues),
  .model_reads_i(model_reads),.act_reads_i(act_reads),.act_writes_i(act_writes),
  .perf_counters_i(perf_counters),
  .trace_control_o(trace_control),.trace_offset_o(trace_offset),
  .trace_read_o(trace_read),.trace_addr_o(trace_addr),.trace_valid_i(trace_valid),.trace_data_i(trace_data),.trace_error_i(trace_error),
  .model_bytes_o(model_bytes),.input_base_o(input_base),.input_bytes_o(input_bytes),
  .output_base_o(output_base),.output_bytes_o(output_bytes),.op_count_o(op_count),.input_i32_o(input_i32),
  .model_we_o(mw),.model_addr_o(mwa),.model_data_o(mwd),.model_ready_i(mready),
  .act_we_o(host_aw),.act_addr_o(host_wa),.act_data_o(host_wd),.act_mask_o(host_wm),
  .read_valid_o(host_ar),.read_ready_i(host_arr),.read_addr_o(host_ra),
  .read_rsp_valid_i(arv && !running),.read_rsp_ready_o(host_rsp_ready),.read_data_i(act_data));
 npu_model_store #(.BYTES(MODEL_BYTES)) i_model_store (
  .clk(clk_i),.rst(rst),.write_valid(mw),.write_ready(mready),.write_addr(mwa),.write_data(mwd),
  .read_request(mv),.read_ready(mr),.read_addr(ma),.read_valid(mrv),.read_response_ready(1'b1),.read_data(md));
 // Preflight never accesses activations. A registered job owner cuts the
 // descriptor FSM out of the physical RAM address arbitration path.
 assign host_arr=arr && !running;
 npu_activation_store #(.BYTES(ACT_BYTES)) i_activation (
  .clk(clk_i),.rst(rst),.read_valid(running ? av : host_ar),.read_ready(arr),.read_addr(running ? aa : host_ra),
  .rsp_valid(arv),.rsp_ready(running ? 1'b1 : host_rsp_ready),.rsp_data(act_data),
  .write_valid(running ? aw : host_aw),.write_addr(running ? wa : host_wa),
  .write_data(running ? wd : host_wd),.write_mask(running ? wm : host_wm));
 npu_program_core #(.ACT_BYTES(ACT_BYTES),.MODEL_BYTES(MODEL_BYTES),.COLS(COLS),.MAX_OPS(MAX_OPS),.MAX_K(MAX_K)) i_npu (
  .clk(clk_i),.rst(rst),.validate(validate),.run(run),.ready(ready),.busy(busy),
  .model_bytes(model_bytes),.input_base(input_base),.input_bytes(input_bytes),
  .output_base(output_base),.output_bytes(output_bytes),.op_count(op_count),.input_i32(input_i32),
  .validation_done(vdone),.validation_ok(vok),.done(done),.error(error),.error_op(error_op),
  .cycles(cycles),.issues(issues),.model_reads(model_reads),.act_reads(act_reads),.act_writes(act_writes),
  .perf_counters(perf_counters),
  .trace_clear(trace_clear),.trace_active(trace_active),.trace_sample(trace_sample),
  .trace_events(trace_events),.trace_descriptor(trace_descriptor),
  .m_valid(mv),.m_ready(mr),.m_addr(ma),.m_rsp_valid(mrv),.m_rsp_ready(),.m_data(md),
  .a_valid(av),.a_ready(arr && running),.a_addr(aa),.a_rsp_valid(arv && running),.a_rsp_ready(),.a_data(act_data),
  .w_valid(aw),.w_addr(wa),.w_data(wd),.w_mask(wm));
endmodule
