# CPU-less system: both the functional and host JTAG clocks are active.
create_clock -name core_clk -period $::env(EDA_CLOCK_NS) [get_ports clk_i]
set jtag_period 1000
if {[info exists ::env(EDA_JTAG_CLOCK_NS)]} {set jtag_period $::env(EDA_JTAG_CLOCK_NS)}
create_clock -name jtag_clk -period $jtag_period [get_ports tck_i]
set_clock_groups -asynchronous -group core_clk -group jtag_clk
set_clock_uncertainty $::env(EDA_CLOCK_UNCERTAINTY_NS) [get_clocks core_clk]
set_input_delay $::env(EDA_IO_DELAY_NS) -clock core_clk [get_ports rst_ni]
set_input_delay $::env(EDA_IO_DELAY_NS) -clock jtag_clk [get_ports {tms_i td_i}]
set_output_delay $::env(EDA_IO_DELAY_NS) -clock jtag_clk [get_ports td_o]
set_output_delay $::env(EDA_IO_DELAY_NS) -clock core_clk [get_ports irq_o]
