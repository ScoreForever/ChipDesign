# Program the explicitly selected EGo1 with an accepted build's bitstream.
set bitstream [file normalize [lindex $argv 0]]
set cable [lindex $argv 1]
set out [file normalize [lindex $argv 2]]
if {![file isfile $bitstream]} { error "Bitstream missing: $bitstream" }
if {$cable eq ""} { error "An explicit programming cable serial is required" }
set summary_file [open [file join $out summary.md] r]
set acceptance [read $summary_file]
close $summary_file
if {![regexp -line {^Result: PASS\s*$} $acceptance]} { error "Build acceptance is not PASS" }
open_hw_manager
connect_hw_server
set targets [get_hw_targets *$cable]
if {[llength $targets] != 1} { error "Expected exactly one programming cable matching $cable" }
open_hw_target [lindex $targets 0]
set devices [get_hw_devices -filter {PART =~ xc7a35t*}]
if {[llength $devices] != 1} { error "Expected exactly one xc7a35t FPGA" }
set device [lindex $devices 0]
set_property PROGRAM.FILE $bitstream $device
program_hw_devices $device
refresh_hw_device $device
report_property $device -file [file join $out programmed_board_properties.txt]
set done [get_property REGISTER.CONFIG_STATUS.BIT13_DONE_INTERNAL_SIGNAL_STATUS $device]
if {$done ne "1"} { error "FPGA DONE is not asserted after programming: $done" }
set crc [get_property REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR $device]
if {$crc ne "0"} { error "FPGA configuration CRC error: $crc" }
puts "LAB6_FPGA_DONE=$done"
puts "LAB6_FPGA_CRC_ERROR=$crc"
puts "LAB6_PROGRAM_DEVICE=$device"
puts "LAB6_PROGRAM_BITSTREAM=$bitstream"
puts "LAB6_PROGRAM_COMMAND=PASS"
close_hw_target
disconnect_hw_server
close_hw_manager
