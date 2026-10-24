OUTPUT_ARCH(riscv)
ENTRY(_start)
MEMORY { SRAM (rwx) : ORIGIN = 0x80000000, LENGTH = 8192 }
SECTIONS {
  .text : { *(.text.init) *(.text*) *(.rodata*) } > SRAM
  .data : { *(.sdata*) *(.data*) } > SRAM
  .bss (NOLOAD) : { *(.sbss*) *(.bss*) *(COMMON) } > SRAM
  ASSERT(. <= 0x80001000, "validation code exceeds 4 KiB")
}
