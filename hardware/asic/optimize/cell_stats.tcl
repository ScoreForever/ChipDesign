# Geometry-based accounting avoids unsupported libCell.area attributes and
# distinguishes active logic from fillers/taps/decaps/antenna and IO cells.
proc capture_cell_stats {tag} {
    set active_count 0;set active_area 0;set macro_count 0;set macro_area 0
    set fp [open ${tag}_cells.tsv w]
    foreach inst [dbGet top.insts] {
        set cell [dbGet $inst.cell.name]
        set class [dbGet $inst.cell.baseClass]
        lassign [lindex [dbGet $inst.box] 0] x0 y0 x1 y1
        set area [expr {($x1-$x0)*($y1-$y0)}]
        if {$cell eq "RA1SHD_2048x32M8"} {
            set kind macro;incr macro_count;set macro_area [expr {$macro_area+$area}]
        } elseif {$class eq "core" && ![regexp {^(FILL|TAPCELL|DCAP|ANTENNA)} $cell]} {
            set kind active;incr active_count;set active_area [expr {$active_area+$area}]
        } else {set kind physical_or_io}
        puts $fp "[dbGet $inst.name]\t$cell\t$kind\t$x0\t$y0\t$x1\t$y1"
    }
    close $fp
    set fp [open ${tag}_cells.json w]
    puts $fp [format {"active_standard_instances":%d,"active_standard_area_um2":%.9f,"macro_count":%d,"macro_area_um2":%.9f} $active_count $active_area $macro_count $macro_area]
    close $fp
    set fp [open ${tag}_cells.json r];set data [read $fp];close $fp
    set fp [open ${tag}_cells.json w];puts $fp "\173$data\175";close $fp
    return [list $active_count $active_area $macro_count $macro_area]
}
