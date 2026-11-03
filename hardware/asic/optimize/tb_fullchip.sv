`timescale 1ns/1ps
// Black-box physical JTAG regression. No RTL hierarchy or memory backdoors.
module tb_fullchip;
 reg clk=0,rst_n=0,tck=1,tms=1,tdi=0;
 wire tdo,irq;
 my_soc_top dut(.clk_i(clk),.rst_ni(rst_n),.tck_i(tck),.tms_i(tms),.td_i(tdi),.td_o(tdo),.irq_o(irq));
 always #10 clk=~clk;
 reg [7:0] op;reg [31:0] addr,data,mask,expected,value;
 reg [4:0] expected_error;
 integer fd,rc,commands=0,waits=0;
 string trace_path,sdf_path;
 reg dummy;
 task tick(input reg m,input reg d,output reg q);
 begin tms=m;tdi=d;#250;tck=0;#500;q=tdo;tck=1;#250;end endtask
 task scan(input [71:0] request,output reg [71:0] response);
 integer n;reg q;
 begin response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(n=0;n<72;n=n+1)begin tick(n==71,request[n],q);response[n]=q;end
 tick(1,0,dummy);tick(0,0,dummy);end endtask
 // Burst IRs 13h/14h move one 32-bit word per scan; a read returns the
 // previous read, so expected values are compared one scan later.
 reg [4:0] cur_ir=5'h12;reg have_prev=0;reg [31:0] prev_expected,prev_mask,burst_reply;integer bursts=0;
 task ir_select(input [4:0] code);
 integer k;
 begin if(cur_ir!==code)begin
 tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(k=0;k<5;k=k+1)tick(k==4,code[k],dummy);
 tick(1,0,dummy);tick(0,0,dummy);cur_ir=code;end end endtask
 task scan32(input [31:0] request,output reg [31:0] response);
 integer n;reg q;
 begin response=0;tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(n=0;n<32;n=n+1)begin tick(n==31,request[n],q);response[n]=q;end
 tick(1,0,dummy);tick(0,0,dummy);end endtask
 task exchange;
 reg [71:0] reply;integer n;
 begin ir_select(5'h12);scan({op,addr,data},reply);
 if(reply[69]!==0)$fatal(1,"JTAG burst fault before cmd=%0d",commands);
 if(have_prev && (reply[31:0]&prev_mask)!==(prev_expected&prev_mask))
 $fatal(1,"Burst read tail got=%h expected=%h",reply[31:0],prev_expected);
 have_prev=0;n=0;reply=0;
 while(reply[71]!==1'b1 || reply[70]!==1'b0)begin
 scan(0,reply);n=n+1;if(n>100)$fatal(1,"JTAG timeout cmd=%0d reply=%h",commands,reply);end
 if(reply[69]!==0 || reply[68:64]!==expected_error || reply[63:32]!==addr)
 $fatal(1,"JTAG response cmd=%0d reply=%h error=%h",commands,reply,expected_error);
 value=reply[31:0];end endtask
 initial begin
 // SDF is annotated at elaboration via xrun -sdf_cmd_file (scope tb_fullchip.dut);
 // Xcelium ignores a run-time $sdf_annotate with a non-constant file name.
 if(!$value$plusargs("SDF=%s",sdf_path))$fatal(1,"SDF required");
 if(!$value$plusargs("TRACE=%s",trace_path))$fatal(1,"TRACE required");
 fd=$fopen(trace_path,"r");if(!fd)$fatal(1,"Trace missing");
 #1;rst_n=1;#1;rst_n=0;#503;rst_n=1;
 repeat(6)tick(1,0,dummy);tick(0,0,dummy);
 tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(rc=0;rc<5;rc=rc+1)tick(rc==4,(5'h12>>rc)&1,dummy);
 tick(1,0,dummy);tick(0,0,dummy);
 while(!$feof(fd))begin
 rc=$fscanf(fd,"%h %h %h %h %h %h\n",op,addr,data,expected_error,mask,expected);
 if(rc!=6)$fatal(1,"Malformed trace");
 if(op=='hf0)begin waits=0;while(irq!==1'b1)begin
 @(negedge clk);waits=waits+1;if(waits>2000000)$fatal(1,"IRQ timeout");end end
 else if(op=='hf1)repeat(data)@(negedge clk);
 else if(op=='hf2);
 else if(op=='h80)begin ir_select(5'h13);scan32(data,burst_reply);bursts=bursts+1;end
 else if(op=='h81)begin ir_select(5'h14);scan32(0,burst_reply);repeat(4)tick(0,0,dummy);bursts=bursts+1;
 if(have_prev && (burst_reply&prev_mask)!==(prev_expected&prev_mask))
 $fatal(1,"Burst read got=%h expected=%h",burst_reply,prev_expected);
 have_prev=1;prev_expected=expected;prev_mask=mask;end
 else begin exchange;if((value&mask)!==(expected&mask))$fatal(1,"Mismatch cmd=%0d addr=%h got=%h expected=%h mask=%h",commands,addr,value,expected,mask);commands=commands+1;end
 end
 $fclose(fd);
 if(have_prev)begin op=1;addr=4;data=0;expected_error=0;exchange;end
 // Physical reset must return the host/controller to a usable ABI-4 INT4 state.
 @(negedge clk);rst_n=0;repeat(6)tick(1,0,dummy);
 @(negedge clk);rst_n=1;repeat(6)tick(1,0,dummy);tick(0,0,dummy);cur_ir=5'h12;
 tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(rc=0;rc<5;rc=rc+1)tick(rc==4,(5'h12>>rc)&1,dummy);
 tick(1,0,dummy);tick(0,0,dummy);
 op=1;addr=0;data=0;expected_error=0;exchange;
 if(value!==32'h40000 || irq!==0)$fatal(1,"Reset recovery failed");
 $display("PNR_OPT_JTAG_PASS commands=%0d bursts=%0d",commands,bursts);$finish;
 end
 initial begin #2000000000;$fatal(1,"SDF global timeout");end
endmodule
