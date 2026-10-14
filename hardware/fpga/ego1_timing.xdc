create_clock -period 10.000 -name fpga_clk_i -waveform {0.000 5.000} [get_ports fpga_clk_i]
create_clock -period 1000.000 -name tck_i -waveform {0.000 500.000} [get_ports tck_i]
set_clock_groups -asynchronous -group [get_clocks tck_i] -group [get_clocks -include_generated_clocks fpga_clk_i]
