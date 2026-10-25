# Bounded, identical ECO policy for every candidate; never waive violations.
proc repair_process_antenna {} {
    for {set attempt 0} {$attempt < 3} {incr attempt} {
        set report antenna_attempt_${attempt}.rpt
        verifyProcessAntenna -report $report
        set fp [open $report r];set text [read $fp];close $fp
        if {[string first "No Violations Found" $text]>=0} {return}
        if {$attempt==2} {error "Process antenna violations remain after two selected-net reroutes"}
        set nets {}
        foreach line [split $text \n] {
            if {[regexp {^(\S+)\s+\([0-9]+\)\s*$} $line whole net]} {lappend nets $net}
        }
        set nets [lsort -unique $nets]
        if {[llength $nets]==0} {error "Unrecognized antenna report: refusing an unbounded reroute"}
        setNanoRouteMode -drouteFixAntenna true -routeInsertAntennaDiode true
        foreach net $nets {
            # Variable substitution is deliberately not eval: bus brackets and
            # hierarchy characters must remain part of the literal net name.
            editDelete -net $net -type Signal
            deselectAll
            selectNet $net
            routeDesign -selected
        }
        deselectAll
    }
}
