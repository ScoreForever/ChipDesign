# Tool/IP smoke check only; does not synthesize or program hardware.
set out [file normalize [lindex $argv 0]]
file mkdir $out
create_project lab6_env [file join $out project] -part xc7a35tcsg324-1 -force
set_property target_language Verilog [current_project]
set_property target_simulator ModelSim [current_project]
create_ip -name clk_wiz -vendor xilinx.com -library ip -module_name clk_wiz_0
set_property -dict [list CONFIG.PRIM_IN_FREQ {100.000} CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} CONFIG.USE_RESET {false} CONFIG.USE_LOCKED {true}] [get_ips clk_wiz_0]
generate_target instantiation_template [get_ips clk_wiz_0]
puts "LAB6_PART=[get_property PART [current_project]]"
puts "LAB6_CLOCK_IP=[get_property IPDEF [get_ips clk_wiz_0]]"
puts "LAB6_CLOCK_MHZ=[get_property CONFIG.CLKOUT1_REQUESTED_OUT_FREQ [get_ips clk_wiz_0]]"
puts "LAB6_VIVADO_CHECK=PASS"
close_project
