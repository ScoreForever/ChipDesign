.section .text.init
.globl _start
_start:
    li sp, 0x80001d00
    .option push
    .option arch, +zicsr
    csrw 0x320, zero
    csrw mstatus, zero
    .option pop
    call main
.globl finished
finished:
    j finished
