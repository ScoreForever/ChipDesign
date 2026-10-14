# EGo1 V2.2 uses a 3.3 V SPI configuration interface (N25Q32).
# Board manual: https://e-elements.readthedocs.io/zh/ego1_v2.2/EGo1.html#fpga
# Apply only to the EGo1 xc7a35tcsg324-1 target.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
