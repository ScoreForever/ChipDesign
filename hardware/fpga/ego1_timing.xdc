create_clock -period 10.000 -name fpga_clk_i -waveform {0.000 5.000} [get_ports fpga_clk_i]
create_clock -period 1000.000 -name tck_i -waveform {0.000 500.000} [get_ports tck_i]
# FT232H short-direct-wire profile, nominal TCK <= 1 MHz, rising TDO sample.
# Derivation and scope: docs/JTAG_IO_TIMING.md. Delays referenced at FPGA TCK.
# FTDI DO clock-to-out 0..7.5 ns, DI setup 11 ns/hold 0 ns.
# Per-wire board/lead propagation envelope 0..5 ns; external edge guard 20 ns.
# FPGA TDI/TMS: min=0+0-5-20=-25; max=7.5+5-0+20=32.5 ns.
set_input_delay -clock [get_clocks tck_i] -clock_fall -min -25.000 [get_ports {td_i tms_i}]
set_input_delay -clock [get_clocks tck_i] -clock_fall -max 32.500 [get_ports {td_i tms_i}]
# FPGA TDO is launched on falling TCK, sampled on rising TCK at FT232H.
# max=11+5+5+20=41; min=0+0-0-20=-20 ns.
set_output_delay -clock [get_clocks tck_i] -min -20.000 [get_ports td_o]
set_output_delay -clock [get_clocks tck_i] -max 41.000 [get_ports td_o]
# The inverted TCK tree makes TIMING-18 inspect both reference edges.
# These half-period translations describe the SAME arrival/required windows,
# not additional physical launch/capture events. Preserve both with add_delay.
set_input_delay -clock [get_clocks tck_i] -min 475.000 -add_delay [get_ports {td_i tms_i}]
set_input_delay -clock [get_clocks tck_i] -max 532.500 -add_delay [get_ports {td_i tms_i}]
set_output_delay -clock [get_clocks tck_i] -clock_fall -min 480.000 -add_delay [get_ports td_o]
set_output_delay -clock [get_clocks tck_i] -clock_fall -max 541.000 -add_delay [get_ports td_o]
# LED indicators have no synchronous receiver or setup/hold specification.
# Their internal launching registers remain fully timed.
set_false_path -to [get_ports {led_locked_o led_heartbeat_o led_tck_o}]
# DMI crossings receive endpoint-specific max_delay constraints after synthesis
# in scripts/constrain_dmi_cdc.tcl. No blanket asynchronous clock-group cut:
# it would override the bundled-data delay requirements.
