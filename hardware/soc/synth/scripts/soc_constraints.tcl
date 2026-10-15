# Functional clock; JTAG is inactive for this scenario.
create_clock -name core_clk -period $::env(EDA_CLOCK_NS) [get_ports clk_i]
set_clock_uncertainty $::env(EDA_CLOCK_UNCERTAINTY_NS) [get_clocks core_clk]
set_input_delay $::env(EDA_IO_DELAY_NS) -clock core_clk [get_ports {rst_ni td_i}]
set_output_delay $::env(EDA_IO_DELAY_NS) -clock core_clk [get_ports td_o]
set_case_analysis 0 [get_ports {tck_i tms_i}]
# JTAG-active timing requires a separate scenario and board/system constraints.
