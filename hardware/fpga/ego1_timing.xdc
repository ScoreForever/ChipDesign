# The Clocking Wizard XDC creates the 100 MHz input clock on fpga_clk_i.
# Avoid redeclaring it here; Vivado otherwise reports an overwritten clock.
create_clock -period 1000.000 -name tck_i -waveform {0.000 500.000} [get_ports tck_i]
set_clock_groups -asynchronous -group [get_clocks tck_i] -group [get_clocks -include_generated_clocks fpga_clk_i]

# FT232H direct-wire timing profile, nominal TCK <= 1 MHz, rising TDO sample.
# FTDI MPSSE data: CLKOUT-to-output 0..7.5 ns, input setup 11 ns, hold 0 ns.
# Cable/board propagation 0..5 ns and 20 ns external edge guard are explicit
# engineering assumptions; see docs/JTAG_IO_TIMING.md.
set_input_delay -clock [get_clocks tck_i] -clock_fall -min -25.000 [get_ports {td_i tms_i}]
set_input_delay -clock [get_clocks tck_i] -clock_fall -max 32.500 [get_ports {td_i tms_i}]
set_output_delay -clock [get_clocks tck_i] -min -20.000 [get_ports td_o]
set_output_delay -clock [get_clocks tck_i] -max 41.000 [get_ports td_o]

# Equivalent half-cycle references are needed for the inverted TCK launch
# network. These express the same external windows, not additional events.
set_input_delay -clock [get_clocks tck_i] -min 475.000 -add_delay [get_ports {td_i tms_i}]
set_input_delay -clock [get_clocks tck_i] -max 532.500 -add_delay [get_ports {td_i tms_i}]
set_output_delay -clock [get_clocks tck_i] -clock_fall -min 480.000 -add_delay [get_ports td_o]
set_output_delay -clock [get_clocks tck_i] -clock_fall -max 541.000 -add_delay [get_ports td_o]

# The reset button is asynchronous; its effects are checked with reset recovery
# and board reset tests. Indicator LEDs have no synchronous receiving device.
set_false_path -from [get_ports rst_ni]
set_false_path -to [get_ports {led_locked_o led_heartbeat_o led_tck_o}]
