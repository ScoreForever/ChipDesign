# EGo1 Lab 6 pin assignments (3.3 V I/O).
set_property -dict {PACKAGE_PIN P17 IOSTANDARD LVCMOS33} [get_ports fpga_clk_i]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33} [get_ports rst_ni]
# Local board: J5 pin 17/E15 did not respond in boundary SAMPLE tests.
# TCK is connected to J5 pin 21/H16; leave J5 pin 17 unconnected.
set_property -dict {PACKAGE_PIN H16 IOSTANDARD LVCMOS33} [get_ports tck_i]
set_property -dict {PACKAGE_PIN E16 IOSTANDARD LVCMOS33} [get_ports td_i]
set_property -dict {PACKAGE_PIN D15 IOSTANDARD LVCMOS33} [get_ports td_o]
set_property -dict {PACKAGE_PIN C15 IOSTANDARD LVCMOS33} [get_ports tms_i]
set_property -dict {PACKAGE_PIN K3 IOSTANDARD LVCMOS33} [get_ports led_locked_o]
set_property -dict {PACKAGE_PIN M1 IOSTANDARD LVCMOS33} [get_ports led_heartbeat_o]
set_property -dict {PACKAGE_PIN K1 IOSTANDARD LVCMOS33} [get_ports led_tck_o]
