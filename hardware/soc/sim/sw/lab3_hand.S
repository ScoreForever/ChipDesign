# lab3_hand.S —— 不经过 C，直接用汇编访问 NPU（Lab 3 Task 5）
#
# 程序直接放在 0x8000_0000，bootram 跳过来之后从第一条开始执行。
# 没有 startup.S、没有栈、没有 main，每一条访存指令都能在波形里一一对上。
#
# 做的事：A[0][0] = 3，B[0][0] = 5，其余元素保持复位值 0，
#         所以 C[0][0] 应为 3*5 = 15。算对就往 magic_status 写 PASS。
#
# 对应的机器码见 soc/sim/tb/lab3_hand.hex（每行右边有这里的汇编作注释）。

    lui  t0, 0x70000        # t0 = 0x7000_0000 (NPU_BASE)
    addi t1, zero, 3
    sw   t1, 0x40(t0)       # ACT[0] = A[0][0] = 3
    lui  t2, 0x70001        # t2 = 0x7000_1000 (WGT_BASE)
    addi t1, zero, 5
    sw   t1, 0(t2)          # WGT[0] = B[0][0] = 5
    lw   t3, 0x40(t0)       # 读回 ACT[0]，t3 应为 3
    addi t1, zero, 1
    sw   t1, 0(t0)          # CONTROL = 1 (START)
poll:
    lw   t1, 4(t0)          # 读 STATUS
    andi t1, t1, 1
    beq  t1, zero, poll     # DONE 还没到就回去再读
    lui  t2, 0x70002        # t2 = 0x7000_2000 (OUT_BASE)
    lw   t4, 0(t2)          # t4 = OUT[0] = C[0][0]，应为 15
    lui  t6, 0x80002        # t6 = 0x8000_2000
    addi t5, zero, 15
    bne  t4, t5, fail
    lui  t1, 0xC0DEC
    addi t1, t1, 0x0DE      # t1 = 0xC0DE_C0DE (PASS)
    sw   t1, -32(t6)        # 写 magic_status @ 0x8000_1FE0
done:
    j    done
fail:
    lui  t1, 0xDEADC
    addi t1, t1, -0x111     # t1 = 0xDEAD_BEEF (FAIL)
    sw   t1, -32(t6)
    j    done
