set sdc_version 2.0
set_units -capacitance 1000fF
set_units -time 1000ps
current_design my_soc_top

create_clock -name "core_clk" -period 20.0 -waveform {0.0 10.0} [get_ports clk_i]
create_clock -name "jtag_clk" -period 1000.0 [get_ports tck_i]
set_clock_groups -asynchronous -group [get_clocks core_clk] -group [get_clocks jtag_clk]
set_clock_gating_check -setup 0.0
set_clock_uncertainty -setup 0.2 [get_clocks core_clk]
set_clock_uncertainty -hold 0.2 [get_clocks core_clk]
set_input_delay -clock [get_clocks core_clk] -add_delay 2.0 [get_ports rst_ni]
set_input_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports tms_i]
set_input_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports td_i]
set_output_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports td_o]
set_output_delay -clock [get_clocks core_clk] -add_delay 2.0 [get_ports irq_o]
set_wire_load_mode "segmented"
