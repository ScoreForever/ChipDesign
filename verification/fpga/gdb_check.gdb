set pagination off
set confirm off
set remotetimeout 20
set architecture riscv:rv32
target remote localhost:3333
monitor halt
if *(unsigned int *)0x00010000 != 0x800002b7
  echo FPGA_GDB_BOOTROM=FAIL\n
  quit 1
end
set $saved_pc = $pc
set $saved_t0 = $t0
set $saved_scratch = *(unsigned int *)0x80001f00
set {unsigned int}0x80001f00 = 0x00128293
if *(unsigned int *)0x80001f00 != 0x00128293
  echo FPGA_GDB_MEMORY=FAIL\n
  quit 1
end
set $t0 = 41
set $pc = 0x80001f00
stepi
if $t0 != 42
  echo FPGA_GDB_STEP=FAIL\n
  quit 1
end
set {unsigned int}0x80001f00 = $saved_scratch
set $t0 = $saved_t0
set $pc = $saved_pc
echo FPGA_GDB_CHECK=PASS\n
disconnect
quit 0
