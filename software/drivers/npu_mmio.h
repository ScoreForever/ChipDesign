#ifndef NPU_MMIO_H
#define NPU_MMIO_H
#include <stdint.h>

#define NPU_BASE 0x70000000u
enum {
    NPU_CONTROL = 0x00, NPU_STATUS = 0x04, NPU_CLASS_COUNT = 0x08,
    NPU_ERROR_CODE = 0x0c, NPU_LOGIT = 0x10, NPU_VERSION = 0x30,
    NPU_PERF_TOTAL = 0x40, NPU_PERF_STATUS = 0x6c,
    NPU_INPUT = 0x1000, NPU_WEIGHT = 0x2000, NPU_PARAMETER = 0x3000
};
enum { NPU_READY = 1, NPU_BUSY = 2, NPU_DONE = 4, NPU_ERROR = 8 };
enum { NPU_START = 1, NPU_IRQ_ENABLE = 2, NPU_CLEAR_DONE = 4, NPU_CLEAR_ERROR = 8 };
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
#endif
