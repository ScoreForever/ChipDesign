# Export net aliases and primitive driver classes for activity mapping diagnostics.
proc export_activity_nets {path} {
    set f [open $path w]
    puts $f "name\tcanonical\tdriver_refs\tsequential"
    foreach net [get_nets -hierarchical -segments] {
        set pins [get_pins -quiet -leaf -of_objects $net -filter {DIRECTION == OUT}]
        set refs ""
        set seq ""
        if {[llength $pins]} {
            set cells [get_cells -quiet -of_objects $pins]
            if {[llength $cells]} {
                set refs [join [lsort -unique [get_property REF_NAME $cells]] ,]
                set seq [join [lsort -unique [get_property IS_SEQUENTIAL $cells]] ,]
            }
        }
        puts $f "[get_property NAME $net]\t[get_property PARENT $net]\t$refs\t$seq"
    }
    close $f
}

if {[llength $argv] == 3 && [lindex $argv 2] eq "export_only"} {
    open_checkpoint [lindex $argv 0]
    export_activity_nets [lindex $argv 1]
    close_design
    exit 0
}
