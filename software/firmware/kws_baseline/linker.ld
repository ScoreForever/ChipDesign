OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { SRAM (rwx) : ORIGIN = 0x80000000, LENGTH = 8K }
__stack_top = 0x80001fe0;
__stack_bottom = __stack_top - 256;
__results = __stack_bottom - (16 + SAMPLE_COUNT * 40);
SECTIONS {
    .text : { *(.text.init) *(.text .text.*) } > SRAM
    .rodata : { . = ALIGN(4); *(.rodata .rodata.*) } > SRAM
    .data : { . = ALIGN(4); *(.data .data.* .sdata .sdata.*) } > SRAM
    .bss (NOLOAD) : { . = ALIGN(4); *(.bss .bss.* .sbss .sbss.* COMMON) } > SRAM
    __image_end = ALIGN(., 4);
    ASSERT(__image_end <= __results, "image overlaps results/stack")
    ASSERT(SIZEOF(.bss) == 0, "startup requires no BSS")
    /DISCARD/ : { *(.comment) *(.riscv.attributes) *(.eh_frame*) }
}
