OUTPUT_ARCH( "riscv" )
ENTRY( _start )

MEMORY
{
    SRAM (rwx) : ORIGIN = 0x80000000, LENGTH = 8K
}

__stack_top = 0x80001FE0;
_magic_base = 0x80001FE0;

SECTIONS
{
    .text : {
        *(.text.init)
        *(.text)
        *(.text.*)
    } > SRAM

    .rodata : {
        . = ALIGN(4);
        *(.rodata)
        *(.rodata.*)
    } > SRAM

    .data : {
        . = ALIGN(4);
        *(.data)
        *(.data.*)
    } > SRAM

    .bss : {
        . = ALIGN(4);
        _bss_start = .;
        *(.bss)
        *(.bss.*)
        *(COMMON)
        . = ALIGN(4);
        _bss_end = .;
    } > SRAM
}
