/* Readable source corresponding to the program emitted by gen_hex.py.
 * It loads an all-zero TinyCNN-8 model through the production MMIO windows,
 * runs one four-class inference, and checks the complete-NPU interface.
 */

    .section .text
    .globl _start
_start:
    lui     t0, 0x70000          /* NPU base */
    lui     t6, 0x80002          /* magic base + 0x20 */
    li      t1, 0x12345678
    sw      t1, -32(t6)          /* RUNNING */

    lui     t2, 0x70001          /* input window */
    li      t3, 320
clear_input:
    sw      zero, 0(t2)
    addi    t2, t2, 4
    addi    t3, t3, -1
    bnez    t3, clear_input

    lui     t2, 0x70002          /* weight window */
    li      t3, 512              /* 256 x 64-bit words */
clear_weights:
    sw      zero, 0(t2)
    addi    t2, t2, 4
    addi    t3, t3, -1
    bnez    t3, clear_weights

    lui     t2, 0x70003          /* parameter windows */
    sw      zero, 0x060(t2)      /* commit Conv1 */
    sw      zero, 0x160(t2)      /* commit Conv2 */
    sw      zero, 0x260(t2)      /* commit GAP */
    sw      zero, 0x360(t2)      /* commit FC */

    li      t1, 4
    sw      t1, 0x008(t0)        /* CLASS_COUNT */
    li      t1, 3
    sw      t1, 0x000(t0)        /* start + IRQ enable */

poll_done:
    lw      t1, 0x004(t0)        /* STATUS */
    andi    t2, t1, 8
    bnez    t2, fail
    andi    t2, t1, 4
    beqz    t2, poll_done

    lw      t2, 0x010(t0)
    bnez    t2, fail
    lw      t2, 0x014(t0)
    bnez    t2, fail
    lw      t2, 0x018(t0)
    bnez    t2, fail
    lw      t2, 0x01c(t0)
    bnez    t2, fail

    li      t1, 6
    sw      t1, 0x000(t0)        /* IRQ enable + clear done */
    lw      t1, 0x004(t0)
    andi    t1, t1, 4
    bnez    t1, fail

    li      t1, 0xc0dec0de
    sw      t1, -32(t6)
done:
    j       done

fail:
    li      t1, 0xdeadbeef
    sw      t1, -32(t6)
    j       done
