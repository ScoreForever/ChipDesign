# chipdesign_npu_test.S — minimal end-to-end test for the integrated SoC.
#
# Tests the ChipDesign matrix_unit (default 4x8) with:
#   A[r]      = 1 for r=0..3
#   W[r][c]   = 1 for all r,c
#   P_in[c]   = 0 for all c
#   Expected OUT[c] = 4 for all c
#
# The program is loaded at SRAM_BASE (0x8000_0000) and bootram jumps here.
# It writes RUNNING, then configures the NPU via MMIO, polls status, reads
# OUT[0], and writes PASS/FAIL to the magic region at 0x8000_1FE0.

    .section .text.init
    .global _start

_start:
    # t0 = NPU_BASE = 0x7000_0000
    lui  t0, 0x70000

    # t6 = 0x8000_2000, used to reach magic region with -32 offset
    lui  t6, 0x80002

    # Write RUNNING = 0x12345678 to magic_status @ 0x8000_1FE0
    lui  t1, 0x12345
    addi t1, t1, 0x678
    sw   t1, -32(t6)

    # ------------------------------------------------------------------
    # Load all weights = 1 (8 words cover 4 rows of 64 bits)
    # ------------------------------------------------------------------
    addi t1, zero, 1
    sw   t1, 0x40(t0)       # MATRIX_WEIGHT[0]
    sw   t1, 0x44(t0)       # MATRIX_WEIGHT[1]
    sw   t1, 0x48(t0)       # MATRIX_WEIGHT[2]
    sw   t1, 0x4C(t0)       # MATRIX_WEIGHT[3]
    sw   t1, 0x50(t0)       # MATRIX_WEIGHT[4]
    sw   t1, 0x54(t0)       # MATRIX_WEIGHT[5]
    sw   t1, 0x58(t0)       # MATRIX_WEIGHT[6]
    sw   t1, 0x5C(t0)       # MATRIX_WEIGHT[7]

    # Trigger weight load: MATRIX_CTRL bit1 = 1
    addi t1, zero, 2
    sw   t1, 0x0(t0)

    # Poll until weights_loaded (MATRIX_STATUS bit1)
poll_wg:
    lw   t1, 0x4(t0)
    andi t1, t1, 2
    beq  t1, zero, poll_wg

    # ------------------------------------------------------------------
    # Activations A[r] = 1 for r=0..3 -> packed 0x01010101
    # ------------------------------------------------------------------
    lui  t1, 0x01010
    addi t1, t1, 0x101
    sw   t1, 0x100(t0)      # MATRIX_ACT

    # Partial sums = 0
    sw   zero, 0x110(t0)    # MATRIX_PSUM[0]
    sw   zero, 0x114(t0)
    sw   zero, 0x118(t0)
    sw   zero, 0x11C(t0)
    sw   zero, 0x120(t0)
    sw   zero, 0x124(t0)
    sw   zero, 0x128(t0)
    sw   zero, 0x12C(t0)

    # Trigger compute: MATRIX_CTRL bit0 = 1
    addi t1, zero, 1
    sw   t1, 0x0(t0)

    # Poll until out_valid (MATRIX_STATUS bit2)
poll_done:
    lw   t1, 0x4(t0)
    andi t1, t1, 4
    beq  t1, zero, poll_done

    # Read OUT[0]
    lw   t4, 0x200(t0)

    # Expected value is 4
    addi t5, zero, 4
    bne  t4, t5, fail

    # Write PASS = 0xC0DEC0DE to magic_status
    lui  t1, 0xC0DEC
    addi t1, t1, 0x0DE
    sw   t1, -32(t6)

done:
    j    done

fail:
    # Write FAIL = 0xDEADBEEF to magic_status
    lui  t1, 0xDEADC
    addi t1, t1, -0x111
    sw   t1, -32(t6)
    j    done
