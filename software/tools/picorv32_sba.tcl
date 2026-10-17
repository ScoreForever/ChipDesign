# Source in OpenOCD after configuring the board's adapter and JTAG TAP.
# Uses raw DMI scans, so no RISC-V hart examination/halt is required.
# Example: pico_sba_init <configured.tap.name>
#          pico_load_hex <path/to/workload.hex>
# Physical adapter/TAP operation still requires board validation.

proc pico_sba_init {tap {idle_cycles 64}} {
    if {$idle_cycles < 1} { error "idle_cycles must be positive" }
    set ::pico_tap $tap
    set ::pico_idle $idle_cycles
    # DTMCS.dmireset clears stale DMI errors without resetting the SoC.
    irscan $tap 0x10
    drscan $tap 32 0x00010000
    runtest $idle_cycles
    irscan $tap 0x11
    pico_dmi 2 0x10 1
    set sbcs [pico_dmi 1 0x38 0]
    if {($sbcs & 4) == 0} { error "Debug Module does not advertise 32-bit SBA" }
}

proc pico_dmi {op address data} {
    if {![info exists ::pico_tap]} { error "Call pico_sba_init first" }
    if {$op < 1 || $op > 2 || $address < 0 || $address > 127} { error "Invalid DMI request" }
    set packet [expr {(($address & 127) << 34) | (($data & 0xffffffff) << 2) | $op}]
    irscan $::pico_tap 0x11
    drscan $::pico_tap 41 [format %x $packet]
    runtest $::pico_idle
    set raw [string trim [drscan $::pico_tap 41 0]]
    if {![regexp {^[0-9a-fA-F]+$} $raw]} { error "Invalid DMI scan result: $raw" }
    set response [expr "0x$raw"]
    set status [expr {$response & 3}]
    if {$status != 0} {
        # Never automatically replay writes: a start/control write may
        # already have taken effect. Reinitialize with a longer idle time.
        error "DMI status=$status; reinitialize with longer idle_cycles; request not replayed"
    }
    return [expr {($response >> 2) & 0xffffffff}]
}

proc pico_sba_wait {} {
    for {set i 0} {$i < 1000} {incr i} {
        set sbcs [pico_dmi 1 0x38 0]
        if {($sbcs & 0x00407000) != 0} { error [format "SBA error: SBCS=%08x" $sbcs] }
        if {($sbcs & 0x00200000) == 0} { return }
    }
    error "SBA timeout"
}

proc pico_sba_address {address} {
    if {$address < 0 || $address > 0xffffffff || ($address & 3) != 0} {
        error "SBA requires a 32-bit aligned address"
    }
}

proc pico_sba_write {address data} {
    pico_sba_address $address
    pico_sba_wait
    pico_dmi 2 0x38 0x00040000
    pico_dmi 2 0x39 $address
    pico_dmi 2 0x3c $data
    pico_sba_wait
}

proc pico_sba_read {address} {
    pico_sba_address $address
    pico_sba_wait
    pico_dmi 2 0x38 0x00140000
    pico_dmi 2 0x39 $address
    pico_sba_wait
    return [pico_dmi 1 0x3c 0]
}

proc pico_cpu_stop {} {
    pico_sba_write 0x0001003c 0
    for {set i 0} {$i < 1000} {incr i} {
        if {([pico_sba_read 0x00010038] & 1) != 0} { return }
    }
    error "CPU did not reset and drain outstanding AXI"
}

proc pico_load_hex {path} {
    set f [open $path r]
    set text [read $f]
    close $f
    set words {}
    foreach line [split $text "\n"] {
        regsub {//.*$} $line {} line
        foreach word [regexp -all -inline {\S+} $line] {
            if {![regexp {^[0-9a-fA-F]{8}$} $word]} { error "Expected one 32-bit hex word: $word" }
            lappend words $word
        }
    }
    set count [llength $words]
    if {$count < 1 || $count > 2048} { error "Image must contain 1..2048 32-bit words" }
    pico_cpu_stop
    # CPU reset leaves accelerators running. Do not overwrite their SRAM
    # buffers until both engines have completed their accepted work.
    set idle 0
    for {set i 0} {$i < 1000} {incr i} {
        if {([pico_sba_read 0x70000004] & 2) == 0 &&
            ([pico_sba_read 0x70000410] & 1) == 0} { set idle 1; break }
    }
    if {!$idle} { error "NPU/DMA still busy; CPU remains reset and SRAM is unchanged" }
    set address 0x80000000
    foreach word $words {
        pico_sba_write $address [expr "0x$word"]
        incr address 4
    }
    # Verify the complete image before releasing CPU reset.
    set address 0x80000000
    foreach word $words {
        if {[pico_sba_read $address] != [expr "0x$word"]} {
            error [format "SRAM verification failed at %08x; CPU remains reset" $address]
        }
        incr address 4
    }
    pico_sba_write 0x0001003c 1
    echo "Loaded and verified $count words; PicoRV32 started from BootRAM"
}
