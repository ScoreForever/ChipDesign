# Tool-rendered layout views of a finished candidate (read-only).
# Innovus needs a display for the GUI; on the headless course node run:
#   cp -a <candidate>/final.enc.dat <shots>/
#   cd <shots> && SHOTS_DIR=$PWD xvfb-run -a -s "-screen 0 1800x1800x24" \
#       innovus -files <repo>/hardware/asic/optimize/screenshots.tcl
# Works on a copy of the database so the candidate directory is untouched.
set B $env(SHOTS_DIR)
restoreDesign $B/final.enc.dat my_soc_top
win
uiSet main -geometry 1500x1500+0+0
set metals {METAL1 METAL2 METAL3 METAL4 METAL5 METAL6}
set vias {CONT VIA12 VIA23 VIA34 VIA45 VIA56 POLY1 POLY2}
proc vis {names v} {foreach n $names {if {[catch {setLayerPreference $n -isVisible $v} m]} {puts "PREF_FAIL $n $m"}}}
proc shot {name} {
    global B
    redraw
    if {[catch {gui_dump_picture $B/$name.png -format PNG} m]} {puts "SHOT_FAIL $name $m"} else {puts "SHOT_OK $name"}
}
fit
# Cells, SRAMs and PADs only.
vis [concat $metals $vias metalFill] 0
shot 1_placement
# Power network: core/SRAM rings, stripes and row rails.
vis {METAL1 METAL4 METAL5 METAL6} 1
vis {net} 0
shot 2_power
# Signal routing without fill.
vis {net} 1
vis [concat $metals $vias] 1
vis {power ground specialNet} 0
shot 3_signal_routing
# Everything as streamed out, fill included.
vis [concat $metals $vias metalFill] 1
vis {net power ground specialNet} 1
shot 5_all
# South-west corner: corner cell, IO fillers, signal PADs, bond openings, SRAM ring.
vis {metalFill} 0
zoomBox 0 0 420 420
shot 6_corner_zoom
# Same 80 um window of M5/M6 without and with metal fill.
vis {METAL1 METAL2 METAL3 METAL4 CONT VIA12 VIA23 VIA34 POLY1 POLY2 stdCell instName pinName} 0
zoomBox 1250 1250 1330 1330
shot 4a_m56_no_fill
vis {metalFill} 1
shot 4b_m56_with_fill
puts SHOTS_DONE
exit
