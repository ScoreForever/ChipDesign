# IO registration retains the fixed geometry; adapted from the course flow.
# Register IO sides from the actual fixed geometry without moving instances.
set io_die [lindex [dbGet top.fPlan.box] 0]
lassign $io_die io_dx0 io_dy0 io_dx1 io_dy1
set io_tol 0.001
set io_rows [dict create top {} bottom {} left {} right {}]
set io_corners [dict create]
set io_before [dict create]
foreach io_inst [dbGet top.insts] {
    set io_class [dbGet $io_inst.cell.baseClass]
    if {$io_class ni {pad corner}} {continue}
    set io_subclass [dbGet $io_inst.cell.subClass]
    if {[string match -nocase *spacer* $io_subclass]} {continue}
    set io_name [dbGet $io_inst.name]
    set io_cell [dbGet $io_inst.cell.name]
    if {$io_cell eq "PAD60LU_OBV"} {continue}
    if {[dbGet $io_inst.pStatus] ne "fixed"} {error "unfixed IO: $io_name"}
    set io_box [lindex [dbGet $io_inst.box] 0]
    lassign $io_box io_x0 io_y0 io_x1 io_y1
    set io_ori [dbGet $io_inst.orient]
    dict set io_before $io_name [list $io_box $io_ori]
    if {$io_class eq "corner"} {
        set h ""; set v ""
        if {abs($io_x0-$io_dx0) < $io_tol} {set h left}
        if {abs($io_x1-$io_dx1) < $io_tol} {set h right}
        if {abs($io_y0-$io_dy0) < $io_tol} {set v bottom}
        if {abs($io_y1-$io_dy1) < $io_tol} {set v top}
        if {$h eq "" || $v eq ""} {error "corner not on die: $io_name"}
        dict set io_corners ${v}${h} [list $io_name $io_cell $io_ori]
    } else {
        set distances [lsort -real -index 0 [list \
            [list [expr {abs($io_y0-$io_dy0)}] bottom] \
            [list [expr {abs($io_dy1-$io_y1)}] top] \
            [list [expr {abs($io_x0-$io_dx0)}] left] \
            [list [expr {abs($io_dx1-$io_x1)}] right]]]
        set indent [lindex $distances 0 0]
        set side [lindex $distances 0 1]
        if {$side in {top bottom}} {set offset [expr {$io_x0-$io_dx0}]} \
        else {set offset [expr {$io_y0-$io_dy0}]}
        dict lappend io_rows $side [list $offset $io_name $io_cell $indent $io_ori]
    }
}
if {[dict size $io_corners] != 4} {error "expected four IO corners"}
set io_tmp_path io_registration_tmp.io
set io_fd [open $io_tmp_path w]
puts $io_fd "(globals\n version = 3\n io_order = default\n)\n(iopad"
foreach side {topright top topleft left bottomleft bottom bottomright right} {
    puts $io_fd " ($side"
    if {[dict exists $io_corners $side]} {
        lassign [dict get $io_corners $side] name cell orient
        puts $io_fd [format {  (inst name="%s" cell="%s" orientation=%s place_status=fixed)} $name $cell $orient]
    } else {
        foreach row [lsort -real -index 0 [dict get $io_rows $side]] {
            lassign $row offset name cell indent orient
            puts $io_fd [format {  (inst name="%s" cell="%s" offset=%.6f indent=%.6f orientation=%s place_status=fixed)} $name $cell $offset $indent $orient]
        }
    }
    puts $io_fd " )"
}
puts $io_fd ")"
close $io_fd
loadIoFile $io_tmp_path -noAdjustDieSize -specifiedIosOnly
foreach io_name [dict keys $io_before] {
    set io_inst [dbGet top.insts.name $io_name -p]
    lassign [dict get $io_before $io_name] io_old_box io_old_ori
    set io_new_box [lindex [dbGet $io_inst.box] 0]
    foreach io_old_value $io_old_box io_new_value $io_new_box {
        if {abs($io_old_value-$io_new_value) > $io_tol} {
            error "IO registration moved fixed instance: $io_name"
        }
    }
    if {[dbGet $io_inst.orient] ne $io_old_ori} {
        error "IO registration changed orientation: $io_name"
    }
}
file delete $io_tmp_path
saveIoFile -locations -includeCellName my_soc_top_io_registered.io
defOut my_soc_top_io_registered.def

