.section .text.init
.global _start
_start:
    la sp, __stack_top
    .option push
    .option arch, +zicsr
    csrw 0x320, zero
    .option pop
    la t0, __stack_bottom
    li t2, 0xa5a5a5a5
1:  bgeu t0, sp, 2f
    sw t2, 0(t0)
    addi t0, t0, 4
    j 1b
2:  call main
3:  j 3b
