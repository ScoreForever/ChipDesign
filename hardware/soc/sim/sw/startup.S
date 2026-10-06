#################################################################################
# Description: Startup code for cv32e40p SoC (rv32imc, runs from SRAM @ 0x80000000)
#################################################################################

.section .text.init
.global _start
.extern main

_start:
    # Stack pointer at top of SRAM (grows downward, magic region above)
    la   sp, __stack_top
    # Return address points to halt loop in case main returns
    la   ra, loop

    # Zero .bss
    la   t0, _bss_start
    la   t1, _bss_end
bss_loop:
    bgeu t0, t1, bss_done
    sw   zero, 0(t0)
    addi t0, t0, 4
    j    bss_loop
bss_done:

    call main

loop:
    j loop
