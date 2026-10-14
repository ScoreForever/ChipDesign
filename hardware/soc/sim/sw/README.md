# soc/sim/sw —— 测试程序是怎么变成 hex 的

当前TinyCNN-8 SoC回归使用 `gen_hex.py` 直接生成
`chipdesign_npu_test.hex`。该程序通过正式完整NPU MMIO接口装载零模型并运行
一次端到端推理，不依赖RISC-V GCC。生成结果必须与仓库中的hex逐行一致。

```powershell
D:\conda\envs\ml\python.exe hardware/soc/sim/sw/gen_hex.py `
  --output hardware/soc/sim/sw/chipdesign_npu_test.hex
```

`soc/sim/tb/` 里的 `lab3_test{1,2,3}.hex` 是助教用 RISC-V GCC 预先编译好的。
**本实验不需要安装工具链**，这个目录只是把编译过程中的中间产物留给你看，
配合讲义里"C 代码怎么变成 hex"那道思考题使用。

| 文件 | 是什么 |
|---|---|
| `startup.S` | 启动代码：设栈指针、清 `.bss`、跳进 `main`。hex 的第 1 行就是它的第一条指令 |
| `linker.ld` | 链接脚本：规定程序放在 `0x8000_0000`、主存只有 8 KB、栈顶在哪 |
| `lab3_test{1,2,3}.dump` | 反汇编，C 源码和对应的机器码交替排列（`objdump -d -S`） |

`.dump` 里每一行形如：

```
800000ba:	c390                	sw	a2,0(a5)
```

依次是**指令地址**、**机器码**、**汇编**。注意有些机器码只有 4 个十六进制位（16 bit），
那是 RISC-V 的压缩指令（C 扩展）。所以 hex 里的一行（32 bit）不一定正好是一条指令。

助教编译时用的命令（仅供参考，不要求你执行）：

```bash
riscv-none-elf-gcc -march=rv32imc -mabi=ilp32 -O1 -nostdlib -static \
    -Tlinker.ld -fno-builtin -fno-tree-loop-distribute-patterns \
    startup.S lab3_test1.c -o lab3_test1.elf
riscv-none-elf-objdump -d -S lab3_test1.elf > lab3_test1.dump
```

之后再用一个小脚本把 `.text` / `.rodata` / `.data` 段的内容按"一行一个 32-bit 字"
写成 `$readmemh` 能读的 `.hex`。

`soc/sim/tb/lab3_hand.hex` 和本目录的 `lab3_hand.S` 是另一条路：不经过 C 和 GCC，
直接手写汇编、手工转成机器码，见讲义 Task 5。
