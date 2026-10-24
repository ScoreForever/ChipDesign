###############################################################################
# Innovus MMMC setup missing from the distributed Lab 4 student template.
# Names are kept compatible with init_pnr_standard.tcl and Lab4.md.
###############################################################################

create_library_set -name lib_tt_1p80v_25c \
    -timing $proj(library_set,tt_1p80v_25c)
create_rc_corner -name rc_tt_1p80v_25c \
    -temperature $proj(tt_1p80v_25c,T) -cap_table $rm_captable_file
create_delay_corner -name tt_1p80v_25c \
    -library_set lib_tt_1p80v_25c -rc_corner rc_tt_1p80v_25c
create_constraint_mode -name func -sdc_files $proj(constraints,func)
create_analysis_view -name func_tt_1p80v_25c \
    -constraint_mode func -delay_corner tt_1p80v_25c
set_analysis_view -setup {func_tt_1p80v_25c} -hold {func_tt_1p80v_25c}
