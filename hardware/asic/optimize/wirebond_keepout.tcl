# Native bond-PAD metal needs 2.5 um clearance under the WB deck. Keep a
# 3 um margin for signal routing and floating fill on the two upper metals.
# PG routes and the electrical PAD instances are unchanged.
proc add_wirebond_keepouts {expected_bonds} {
    set bonds [dbGet -p2 top.insts.cell.name PAD60LU_OBV]
    if {$bonds eq "0x0" || [llength $bonds]!=$expected_bonds} {
        error "Bond PAD count does not match the configured electrical IOs"
    }
    lassign [lindex [dbGet top.fPlan.box] 0] dx0 dy0 dx1 dy1
    set fp [open wirebond_keepouts.tsv w]
    set count 0
    foreach inst $bonds {
        lassign [lindex [dbGet $inst.box] 0] x0 y0 x1 y1
        set box [list [expr {max($dx0,$x0-3.0)}] [expr {max($dy0,$y0-3.0)}] \
            [expr {min($dx1,$x1+3.0)}] [expr {min($dy1,$y1+3.0)}]]
        createRouteBlk -name WB_SIGNAL_$count -layer {METAL5 METAL6} -box $box -exceptpgnet
        createRouteBlk -name WB_FILL_$count -layer {METAL5 METAL6} -box $box -fills
        puts $fp "[lindex [dbGet $inst.name] 0]\t[lindex [dbGet $inst.box] 0]\t$box"
        incr count
    }
    close $fp
    return $count
}
