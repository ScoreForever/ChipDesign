// Lab 3 / test 1 — 4x4 unsigned 4-bit GEMM self-check on simple_npu.
//   RTL: simple_npu/rtl/simple_npu_top.sv (MMIO shell) + your simple_npu_core
//   Layout (column-major): NPU_ACT(j*4+i) <- A[i][j], same for B; OUT(j*4+i) -> C[i][j].
//   STATUS bit[0] = DONE (set when the GEMM finishes).

#include <stdint.h>

#define NPU_BASE          0x70000000u
#define NPU_CONTROL       (*(volatile uint32_t *)(NPU_BASE + 0x0000))
#define NPU_STATUS        (*(volatile uint32_t *)(NPU_BASE + 0x0004))
#define NPU_ACT(i)        (*(volatile uint32_t *)(NPU_BASE + 0x0040 + 4*(i)))
#define NPU_WGT(i)        (*(volatile uint32_t *)(NPU_BASE + 0x1000 + 4*(i)))
#define NPU_OUT(i)        (*(volatile uint32_t *)(NPU_BASE + 0x2000 + 4*(i)))

#define NPU_CTRL_START    (1u << 0)
#define NPU_STATUS_DONE   (1u << 0)

#define MAGIC_BASE        0x80001FE0u
#define MAGIC_STATUS      (*(volatile uint32_t *)(MAGIC_BASE + 0x00))
#define MAGIC_FAIL_I      (*(volatile uint32_t *)(MAGIC_BASE + 0x04))
#define MAGIC_FAIL_J      (*(volatile uint32_t *)(MAGIC_BASE + 0x08))
#define MAGIC_HW_VAL      (*(volatile uint32_t *)(MAGIC_BASE + 0x0C))
#define MAGIC_REF_VAL     (*(volatile uint32_t *)(MAGIC_BASE + 0x10))

#define STATUS_RUNNING    0x12345678u
#define STATUS_PASS       0xC0DEC0DEu
#define STATUS_FAIL       0xDEADBEEFu

#define N 4

int main(void) {
    MAGIC_STATUS = STATUS_RUNNING;

    // Deterministic dense 4-bit inputs. Max element = 0xF, max product = 0xE1,
    // max accumulated cell = 4 * 0xE1 = 0x384 (10 bits), fits the OUT[9:0] slot.
    uint8_t A[N][N];
    uint8_t B[N][N];
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            A[i][j] = (uint8_t)((i * 3u + j * 5u) & 0xFu);
            B[i][j] = (uint8_t)((i * 7u + j * 2u) & 0xFu);
        }
    }

    // Stream into ACT/WGT in column-major order. dina[3:0] carries the element.
    for (int j = 0; j < N; j++) {
        for (int i = 0; i < N; i++) {
            NPU_ACT(j * N + i) = A[i][j];
            NPU_WGT(j * N + i) = B[i][j];
        }
    }

    NPU_CONTROL = NPU_CTRL_START;
    while ((NPU_STATUS & NPU_STATUS_DONE) == 0u) { }

    // OUT slot holds the 10-bit accumulator; mask off upper bits before compare.
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            uint32_t ref = 0;
            for (int k = 0; k < N; k++) {
                ref += (uint32_t)A[i][k] * (uint32_t)B[k][j];
            }
            uint32_t hw = NPU_OUT(j * N + i) & 0x3FFu;
            if (hw != ref) {
                MAGIC_FAIL_I  = (uint32_t)i;
                MAGIC_FAIL_J  = (uint32_t)j;
                MAGIC_HW_VAL  = hw;
                MAGIC_REF_VAL = ref;
                MAGIC_STATUS  = STATUS_FAIL;
                return 0;
            }
        }
    }

    MAGIC_STATUS = STATUS_PASS;
    return 0;
}
