# Read-only board inspection; never programs a bitstream.
set out [file normalize [lindex $argv 0]]
file mkdir $out
open_hw_manager
connect_hw_server
set targets [get_hw_targets]
if {[llength $targets] == 0} { error "No FPGA programming cable found" }
foreach target $targets {
    open_hw_target $target
    foreach device [get_hw_devices] {
        refresh_hw_device $device
        report_property $device -file [file join $out board_properties.txt]
        puts "LAB6_FPGA_DEVICE=$device"
    }
    close_hw_target $target
}
disconnect_hw_server
close_hw_manager
