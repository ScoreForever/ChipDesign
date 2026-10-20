create_clock -period 10.000 -name fpga_clk_i -waveform {0.000 5.000} [get_ports fpga_clk_i]
create_clock -period 1000.000 -name tck_i -waveform {0.000 500.000} [get_ports tck_i]
# DMI crossings receive endpoint-specific max_delay constraints after synthesis
# in scripts/constrain_dmi_cdc.tcl. No blanket asynchronous clock-group cut:
# it would override the bundled-data delay requirements.
