# Source after synthesis/opt_design (or after opening a checkpoint).
# Keep DSP performance advisories visible in the full DRC report.
set critical_bram_checks [get_drc_checks {CFGBVS-1 REQP-1839 RBOR-1}]
if {[llength $critical_bram_checks] != 3} {
    error "Required configuration/BRAM DRC checks are unavailable"
}
# Vivado 2025.2 queries violations by report name and name patterns; it does
# not support get_drc_violations -of_objects. Scope the query to this report
# so previous full DRC reports cannot influence the gate.
report_drc -name chipdesign_bram_guard -checks $critical_bram_checks
set critical_bram_violations [get_drc_violations -name chipdesign_bram_guard]
if {[llength $critical_bram_violations] != 0} {
    error "Configuration/BRAM DRC failed: $critical_bram_violations"
}
puts "PASS configuration/BRAM DRC; DSP pipeline advisories remain visible"
