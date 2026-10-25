# Innovus renames nets when module ports move. Preserve an internal terminal
# object, not the old net's string name (which becomes the external net name).
proc connect_signal_pad {port pad direction} {
    set ext_net ${port}_pad
    addNet $ext_net
    set port_ptr [dbGet -p1 top.terms.name $port]
    if {$port_ptr eq "0x0"} {error "Missing signal port $port"}
    set core_net_ptr [dbGet $port_ptr.net]
    set core_term_ptr [lindex [dbGet $core_net_ptr.instTerms] 0]
    if {$core_term_ptr eq "0x0" || $core_term_ptr eq ""} {error "No core terminal on $port"}
    detachModulePort - $port
    attachModulePort - $port $ext_net
    set external [dbGet [dbGet -p1 top.terms.name $port].net.name]
    set internal [dbGet $core_term_ptr.net.name]
    if {$external eq $internal || $internal eq "0x0"} {error "PAD/core net separation failed for $port"}
    attachTerm $pad PAD $external
    if {$direction eq "input"} {
        attachTerm $pad C $internal
        foreach {pin bit} {OEN 1 IE 1 PE 0 DS 0 I 0} {attachTerm $pad $pin 1'b$bit}
    } elseif {$direction eq "output"} {
        attachTerm $pad I $internal
        foreach {pin bit} {OEN 0 IE 0 PE 0 DS 0} {attachTerm $pad $pin 1'b$bit}
    } else {error "Unsupported signal direction $direction"}
}
