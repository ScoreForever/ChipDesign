# ####################################################################

#  Created by Genus(TM) Synthesis Solution 19.17-s144_1 on Tue Nov 10 02:58:43 UTC 2026

# ####################################################################

set sdc_version 2.0

set_units -capacitance 1000fF
set_units -time 1000ps

# Set the current design
current_design my_soc_top

create_clock -name "core_clk" -period 20.0 -waveform {0.0 10.0} [get_ports clk_i]
create_clock -name "jtag_clk" -period 1000.0 -waveform {0.0 500.0} [get_ports tck_i]
set_false_path -from [get_pins {i_host/req_payload_reg[0]/Q}] -to [get_pins {i_host/cmd_data_o_reg[0]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[1]/Q}] -to [get_pins {i_host/cmd_data_o_reg[1]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[2]/Q}] -to [get_pins {i_host/cmd_data_o_reg[2]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[3]/Q}] -to [get_pins {i_host/cmd_data_o_reg[3]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[4]/Q}] -to [get_pins {i_host/cmd_data_o_reg[4]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[5]/Q}] -to [get_pins {i_host/cmd_data_o_reg[5]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[6]/Q}] -to [get_pins {i_host/cmd_data_o_reg[6]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[7]/Q}] -to [get_pins {i_host/cmd_data_o_reg[7]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[8]/Q}] -to [get_pins {i_host/cmd_data_o_reg[8]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[9]/Q}] -to [get_pins {i_host/cmd_data_o_reg[9]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[10]/Q}] -to [get_pins {i_host/cmd_data_o_reg[10]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[11]/Q}] -to [get_pins {i_host/cmd_data_o_reg[11]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[12]/Q}] -to [get_pins {i_host/cmd_data_o_reg[12]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[13]/Q}] -to [get_pins {i_host/cmd_data_o_reg[13]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[14]/Q}] -to [get_pins {i_host/cmd_data_o_reg[14]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[15]/Q}] -to [get_pins {i_host/cmd_data_o_reg[15]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[16]/Q}] -to [get_pins {i_host/cmd_data_o_reg[16]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[17]/Q}] -to [get_pins {i_host/cmd_data_o_reg[17]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[18]/Q}] -to [get_pins {i_host/cmd_data_o_reg[18]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[19]/Q}] -to [get_pins {i_host/cmd_data_o_reg[19]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[20]/Q}] -to [get_pins {i_host/cmd_data_o_reg[20]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[21]/Q}] -to [get_pins {i_host/cmd_data_o_reg[21]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[22]/Q}] -to [get_pins {i_host/cmd_data_o_reg[22]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[23]/Q}] -to [get_pins {i_host/cmd_data_o_reg[23]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[24]/Q}] -to [get_pins {i_host/cmd_data_o_reg[24]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[25]/Q}] -to [get_pins {i_host/cmd_data_o_reg[25]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[26]/Q}] -to [get_pins {i_host/cmd_data_o_reg[26]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[27]/Q}] -to [get_pins {i_host/cmd_data_o_reg[27]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[28]/Q}] -to [get_pins {i_host/cmd_data_o_reg[28]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[29]/Q}] -to [get_pins {i_host/cmd_data_o_reg[29]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[30]/Q}] -to [get_pins {i_host/cmd_data_o_reg[30]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[31]/Q}] -to [get_pins {i_host/cmd_data_o_reg[31]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[32]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[0]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[33]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[1]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[34]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[2]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[35]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[3]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[36]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[4]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[37]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[5]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[38]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[6]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[39]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[7]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[40]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[8]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[41]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[9]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[42]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[10]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[43]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[11]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[44]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[12]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[45]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[13]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[46]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[14]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[47]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[15]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[48]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[16]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[49]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[17]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[50]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[18]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[51]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[19]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[52]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[20]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[53]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[21]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[54]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[22]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[55]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[23]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[56]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[24]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[57]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[25]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[58]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[26]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[59]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[27]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[60]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[28]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[61]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[29]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[62]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[30]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[63]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[31]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[64]/Q}] -to [get_pins {i_host/cmd_op_o_reg[0]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[65]/Q}] -to [get_pins {i_host/cmd_op_o_reg[1]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[66]/Q}] -to [get_pins {i_host/cmd_op_o_reg[2]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[67]/Q}] -to [get_pins {i_host/cmd_op_o_reg[3]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[68]/Q}] -to [get_pins {i_host/cmd_op_o_reg[4]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[69]/Q}] -to [get_pins {i_host/cmd_op_o_reg[5]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[70]/Q}] -to [get_pins {i_host/cmd_op_o_reg[6]/D}] -hold
set_false_path -from [get_pins {i_host/req_payload_reg[71]/Q}] -to [get_pins {i_host/cmd_op_o_reg[7]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[0]/Q}] -to [get_pins {i_host/reply_data_reg[0]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[1]/Q}] -to [get_pins {i_host/reply_data_reg[1]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[2]/Q}] -to [get_pins {i_host/reply_data_reg[2]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[3]/Q}] -to [get_pins {i_host/reply_data_reg[3]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[4]/Q}] -to [get_pins {i_host/reply_data_reg[4]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[5]/Q}] -to [get_pins {i_host/reply_data_reg[5]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[6]/Q}] -to [get_pins {i_host/reply_data_reg[6]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[7]/Q}] -to [get_pins {i_host/reply_data_reg[7]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[8]/Q}] -to [get_pins {i_host/reply_data_reg[8]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[9]/Q}] -to [get_pins {i_host/reply_data_reg[9]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[10]/Q}] -to [get_pins {i_host/reply_data_reg[10]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[11]/Q}] -to [get_pins {i_host/reply_data_reg[11]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[12]/Q}] -to [get_pins {i_host/reply_data_reg[12]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[13]/Q}] -to [get_pins {i_host/reply_data_reg[13]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[14]/Q}] -to [get_pins {i_host/reply_data_reg[14]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[15]/Q}] -to [get_pins {i_host/reply_data_reg[15]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[16]/Q}] -to [get_pins {i_host/reply_data_reg[16]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[17]/Q}] -to [get_pins {i_host/reply_data_reg[17]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[18]/Q}] -to [get_pins {i_host/reply_data_reg[18]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[19]/Q}] -to [get_pins {i_host/reply_data_reg[19]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[20]/Q}] -to [get_pins {i_host/reply_data_reg[20]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[21]/Q}] -to [get_pins {i_host/reply_data_reg[21]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[22]/Q}] -to [get_pins {i_host/reply_data_reg[22]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[23]/Q}] -to [get_pins {i_host/reply_data_reg[23]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[24]/Q}] -to [get_pins {i_host/reply_data_reg[24]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[25]/Q}] -to [get_pins {i_host/reply_data_reg[25]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[26]/Q}] -to [get_pins {i_host/reply_data_reg[26]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[27]/Q}] -to [get_pins {i_host/reply_data_reg[27]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[28]/Q}] -to [get_pins {i_host/reply_data_reg[28]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[29]/Q}] -to [get_pins {i_host/reply_data_reg[29]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[30]/Q}] -to [get_pins {i_host/reply_data_reg[30]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_data_reg[31]/Q}] -to [get_pins {i_host/reply_data_reg[31]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_error_reg[0]/Q}] -to [get_pins {i_host/reply_error_reg[0]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_error_reg[1]/Q}] -to [get_pins {i_host/reply_error_reg[1]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_error_reg[2]/Q}] -to [get_pins {i_host/reply_error_reg[2]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_error_reg[3]/Q}] -to [get_pins {i_host/reply_error_reg[3]/D}] -hold
set_false_path -from [get_pins {i_host/rsp_mailbox_error_reg[4]/Q}] -to [get_pins {i_host/reply_error_reg[4]/D}] -hold
set_false_path -from [get_pins i_host/req_toggle_reg/Q] -to [get_pins {i_host/req_sync_reg[0]/D}]
set_false_path -from [get_pins i_host/ack_toggle_reg/Q] -to [get_pins {i_host/ack_sync_reg[0]/D}]
set_false_path -to [list \
  [get_pins {i_host/tck_reset_sync_reg[0]/CDN}]  \
  [get_pins {i_host/tck_reset_sync_reg[1]/CDN}]  \
  [get_pins {i_host/sys_reset_sync_reg[0]/CDN}]  \
  [get_pins {i_host/sys_reset_sync_reg[1]/CDN}] ]
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[0]/Q}] -to [get_pins {i_host/cmd_data_o_reg[0]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[1]/Q}] -to [get_pins {i_host/cmd_data_o_reg[1]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[2]/Q}] -to [get_pins {i_host/cmd_data_o_reg[2]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[3]/Q}] -to [get_pins {i_host/cmd_data_o_reg[3]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[4]/Q}] -to [get_pins {i_host/cmd_data_o_reg[4]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[5]/Q}] -to [get_pins {i_host/cmd_data_o_reg[5]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[6]/Q}] -to [get_pins {i_host/cmd_data_o_reg[6]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[7]/Q}] -to [get_pins {i_host/cmd_data_o_reg[7]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[8]/Q}] -to [get_pins {i_host/cmd_data_o_reg[8]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[9]/Q}] -to [get_pins {i_host/cmd_data_o_reg[9]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[10]/Q}] -to [get_pins {i_host/cmd_data_o_reg[10]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[11]/Q}] -to [get_pins {i_host/cmd_data_o_reg[11]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[12]/Q}] -to [get_pins {i_host/cmd_data_o_reg[12]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[13]/Q}] -to [get_pins {i_host/cmd_data_o_reg[13]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[14]/Q}] -to [get_pins {i_host/cmd_data_o_reg[14]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[15]/Q}] -to [get_pins {i_host/cmd_data_o_reg[15]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[16]/Q}] -to [get_pins {i_host/cmd_data_o_reg[16]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[17]/Q}] -to [get_pins {i_host/cmd_data_o_reg[17]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[18]/Q}] -to [get_pins {i_host/cmd_data_o_reg[18]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[19]/Q}] -to [get_pins {i_host/cmd_data_o_reg[19]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[20]/Q}] -to [get_pins {i_host/cmd_data_o_reg[20]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[21]/Q}] -to [get_pins {i_host/cmd_data_o_reg[21]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[22]/Q}] -to [get_pins {i_host/cmd_data_o_reg[22]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[23]/Q}] -to [get_pins {i_host/cmd_data_o_reg[23]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[24]/Q}] -to [get_pins {i_host/cmd_data_o_reg[24]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[25]/Q}] -to [get_pins {i_host/cmd_data_o_reg[25]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[26]/Q}] -to [get_pins {i_host/cmd_data_o_reg[26]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[27]/Q}] -to [get_pins {i_host/cmd_data_o_reg[27]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[28]/Q}] -to [get_pins {i_host/cmd_data_o_reg[28]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[29]/Q}] -to [get_pins {i_host/cmd_data_o_reg[29]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[30]/Q}] -to [get_pins {i_host/cmd_data_o_reg[30]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[31]/Q}] -to [get_pins {i_host/cmd_data_o_reg[31]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[32]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[0]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[33]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[1]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[34]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[2]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[35]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[3]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[36]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[4]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[37]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[5]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[38]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[6]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[39]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[7]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[40]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[8]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[41]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[9]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[42]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[10]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[43]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[11]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[44]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[12]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[45]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[13]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[46]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[14]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[47]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[15]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[48]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[16]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[49]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[17]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[50]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[18]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[51]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[19]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[52]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[20]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[53]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[21]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[54]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[22]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[55]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[23]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[56]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[24]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[57]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[25]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[58]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[26]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[59]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[27]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[60]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[28]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[61]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[29]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[62]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[30]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[63]/Q}] -to [get_pins {i_host/cmd_addr_o_reg[31]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[64]/Q}] -to [get_pins {i_host/cmd_op_o_reg[0]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[65]/Q}] -to [get_pins {i_host/cmd_op_o_reg[1]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[66]/Q}] -to [get_pins {i_host/cmd_op_o_reg[2]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[67]/Q}] -to [get_pins {i_host/cmd_op_o_reg[3]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[68]/Q}] -to [get_pins {i_host/cmd_op_o_reg[4]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[69]/Q}] -to [get_pins {i_host/cmd_op_o_reg[5]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[70]/Q}] -to [get_pins {i_host/cmd_op_o_reg[6]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/req_payload_reg[71]/Q}] -to [get_pins {i_host/cmd_op_o_reg[7]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[0]/Q}] -to [get_pins {i_host/reply_data_reg[0]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[1]/Q}] -to [get_pins {i_host/reply_data_reg[1]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[2]/Q}] -to [get_pins {i_host/reply_data_reg[2]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[3]/Q}] -to [get_pins {i_host/reply_data_reg[3]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[4]/Q}] -to [get_pins {i_host/reply_data_reg[4]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[5]/Q}] -to [get_pins {i_host/reply_data_reg[5]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[6]/Q}] -to [get_pins {i_host/reply_data_reg[6]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[7]/Q}] -to [get_pins {i_host/reply_data_reg[7]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[8]/Q}] -to [get_pins {i_host/reply_data_reg[8]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[9]/Q}] -to [get_pins {i_host/reply_data_reg[9]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[10]/Q}] -to [get_pins {i_host/reply_data_reg[10]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[11]/Q}] -to [get_pins {i_host/reply_data_reg[11]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[12]/Q}] -to [get_pins {i_host/reply_data_reg[12]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[13]/Q}] -to [get_pins {i_host/reply_data_reg[13]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[14]/Q}] -to [get_pins {i_host/reply_data_reg[14]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[15]/Q}] -to [get_pins {i_host/reply_data_reg[15]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[16]/Q}] -to [get_pins {i_host/reply_data_reg[16]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[17]/Q}] -to [get_pins {i_host/reply_data_reg[17]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[18]/Q}] -to [get_pins {i_host/reply_data_reg[18]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[19]/Q}] -to [get_pins {i_host/reply_data_reg[19]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[20]/Q}] -to [get_pins {i_host/reply_data_reg[20]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[21]/Q}] -to [get_pins {i_host/reply_data_reg[21]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[22]/Q}] -to [get_pins {i_host/reply_data_reg[22]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[23]/Q}] -to [get_pins {i_host/reply_data_reg[23]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[24]/Q}] -to [get_pins {i_host/reply_data_reg[24]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[25]/Q}] -to [get_pins {i_host/reply_data_reg[25]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[26]/Q}] -to [get_pins {i_host/reply_data_reg[26]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[27]/Q}] -to [get_pins {i_host/reply_data_reg[27]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[28]/Q}] -to [get_pins {i_host/reply_data_reg[28]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[29]/Q}] -to [get_pins {i_host/reply_data_reg[29]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[30]/Q}] -to [get_pins {i_host/reply_data_reg[30]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_data_reg[31]/Q}] -to [get_pins {i_host/reply_data_reg[31]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_error_reg[0]/Q}] -to [get_pins {i_host/reply_error_reg[0]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_error_reg[1]/Q}] -to [get_pins {i_host/reply_error_reg[1]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_error_reg[2]/Q}] -to [get_pins {i_host/reply_error_reg[2]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_error_reg[3]/Q}] -to [get_pins {i_host/reply_error_reg[3]/D}] -ignore_clock_latency
set_max_delay 20 -from [get_pins {i_host/rsp_mailbox_error_reg[4]/Q}] -to [get_pins {i_host/reply_error_reg[4]/D}] -ignore_clock_latency
set_clock_gating_check -setup 0.0 
set_input_delay -clock [get_clocks core_clk] -add_delay 2.0 [get_ports rst_ni]
set_input_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports tms_i]
set_input_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports td_i]
set_output_delay -clock [get_clocks jtag_clk] -add_delay 2.0 [get_ports td_o]
set_output_delay -clock [get_clocks core_clk] -add_delay 2.0 [get_ports irq_o]
set_wire_load_mode "segmented"
set_clock_uncertainty -setup 0.2 [get_clocks core_clk]
set_clock_uncertainty -hold 0.2 [get_clocks core_clk]
