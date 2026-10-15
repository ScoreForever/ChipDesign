#ifndef NPU_MMIO_H
#define NPU_MMIO_H
#include <stdint.h>

#define NPU_BASE 0x70000000u
enum {
    NPU_CONTROL = 0x00, NPU_STATUS = 0x04, NPU_CLASS_COUNT = 0x08,
    NPU_ERROR_CODE = 0x0c, NPU_LOGIT = 0x10, NPU_VERSION = 0x30,
    NPU_PERF_TOTAL = 0x40, NPU_PERF_STATUS = 0x6c, NPU_INPUT_PENDING = 0x70,
    NPU_DMA_SRC = 0x400, NPU_DMA_DST = 0x404, NPU_DMA_LEN = 0x408,
    NPU_DMA_CTRL = 0x40c, NPU_DMA_STATUS = 0x410,
    NPU_INPUT_PACKED = 0x1800,
    NPU_INPUT = 0x1000, NPU_WEIGHT = 0x2000, NPU_PARAMETER = 0x3000
};
enum { NPU_READY = 1, NPU_BUSY = 2, NPU_DONE = 4, NPU_ERROR = 8 };
enum { NPU_START = 1, NPU_IRQ_ENABLE = 2, NPU_CLEAR_DONE = 4, NPU_CLEAR_ERROR = 8 };
enum { NPU_DMA_BUSY = 1, NPU_DMA_DONE = 2 };
static inline void npu_write(uint32_t offset, uint32_t value) {
    *(volatile uint32_t *)(uintptr_t)(NPU_BASE + offset) = value;
}
static inline uint32_t npu_read(uint32_t offset) {
    return *(volatile uint32_t *)(uintptr_t)(NPU_BASE + offset);
}
static inline void npu_write_weight(unsigned index, uint32_t low, uint32_t high) {
    npu_write(NPU_WEIGHT + 8 * index, low);
    npu_write(NPU_WEIGHT + 8 * index + 4, high);
}
static inline void npu_write_input(unsigned index, int8_t value) {
    npu_write(NPU_INPUT + 4 * index, (uint8_t)value);
}
/* Packed input: 0x70001800 + 4*k, k=0..79; low byte is input[4*k].
 * At least four clocks between writes; wait INPUT_PENDING==0 before start.
 * The single-outstanding DMA naturally provides this spacing. */
static inline void npu_dma_clear_done(void) {
    npu_write(NPU_DMA_STATUS, NPU_DMA_DONE);
}
static inline void npu_dma_start(const void *source, uint32_t destination, uint32_t words) {
    npu_dma_clear_done();
    npu_write(NPU_DMA_SRC, (uint32_t)(uintptr_t)source);
    npu_write(NPU_DMA_DST, destination);
    npu_write(NPU_DMA_LEN, words);
#if defined(__riscv)
    __asm__ volatile("fence rw, rw" ::: "memory");
#endif
    npu_write(NPU_DMA_CTRL, 1); /* Polling: startup has no IRQ vector. */
}
static inline int npu_dma_wait(uint32_t poll_limit) {
    while (poll_limit--) {
        if (npu_read(NPU_DMA_STATUS) & NPU_DMA_DONE) {
            npu_dma_clear_done();
            return 0;
        }
    }
    return -1;
}
#endif
