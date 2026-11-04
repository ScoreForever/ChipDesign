open_hw_manager
connect_hw_server
set targets [get_hw_targets *]
puts "HW_TARGET_COUNT=[llength $targets]"
foreach target $targets {
    puts "HW_TARGET=$target"
    report_property $target
}
disconnect_hw_server
close_hw_manager
exit
