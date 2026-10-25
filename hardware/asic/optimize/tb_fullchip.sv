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
 task exchange;
 reg [71:0] reply;integer n;
 begin scan({op,addr,data},reply);n=0;reply=0;
 while(reply[71]!==1'b1 || reply[70]!==1'b0)begin
 scan(0,reply);n=n+1;if(n>100)$fatal(1,"JTAG timeout cmd=%0d reply=%h",commands,reply);end
 if(reply[69]!==0 || reply[68:64]!==expected_error || reply[63:32]!==addr)
 $fatal(1,"JTAG response cmd=%0d reply=%h error=%h",commands,reply,expected_error);
 value=reply[31:0];end endtask
 initial begin
 if(!$value$plusargs("SDF=%s",sdf_path))$fatal(1,"SDF required");
 $sdf_annotate(sdf_path,dut,,"sdf_annotation.log","MAXIMUM");
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
 else begin exchange;if((value&mask)!==(expected&mask))$fatal(1,"Mismatch cmd=%0d addr=%h got=%h expected=%h mask=%h",commands,addr,value,expected,mask);commands=commands+1;end
 end
 $fclose(fd);
 // Physical reset must return the host/controller to a usable ABI-2 state.
 @(negedge clk);rst_n=0;repeat(6)tick(1,0,dummy);
 @(negedge clk);rst_n=1;repeat(6)tick(1,0,dummy);tick(0,0,dummy);
 tick(1,0,dummy);tick(1,0,dummy);tick(0,0,dummy);tick(0,0,dummy);
 for(rc=0;rc<5;rc=rc+1)tick(rc==4,(5'h12>>rc)&1,dummy);
 tick(1,0,dummy);tick(0,0,dummy);
 op=1;addr=0;data=0;expected_error=0;exchange;
 if(value!==32'h20000 || irq!==0)$fatal(1,"Reset recovery failed");
 $display("PNR_OPT_JTAG_PASS commands=%0d",commands);$finish;
 end
 initial begin #2000000000;$fatal(1,"SDF global timeout");end
endmodule
