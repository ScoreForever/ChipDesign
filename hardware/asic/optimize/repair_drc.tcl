# Preserve every check and keep the router's violation markers intact.
# Innovus 21.16 ecoRoute -fix_drc operates on existing markers and refuses
# open nets. Recheck all signal rules after each repair; never waive a short.
proc repair_signal_drc {} {
    for {set attempt 0} {$attempt < 3} {incr attempt} {
        set report signal_drc_attempt_${attempt}.rpt
        verify_drc -check_only default -limit 100000 -report $report
        set fp [open $report r];set text [read $fp];close $fp
        if {[string first "No DRC violations were found" $text]>=0} {return}
        if {![regexp {Total Violations\s*:\s*[1-9][0-9]* Viols\.} $text]} {
            error "Unrecognized signal DRC report; refusing repair"
        }
        if {$attempt==2} {error "Signal DRC remains after two marker-based ECO repairs"}
        ecoRoute -fix_drc
    }
}
