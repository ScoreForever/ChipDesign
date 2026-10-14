#include "kws.h"
#include "npu_mmio.h"
#include <limits.h>

/* Mirrors tools/quantize.py, including negative rounding and saturation. */
int32_t kws_scale(int32_t value, int32_t multiplier, int shift) {
    if (shift > 0) {
        if (value > (INT32_MAX >> shift)) value = INT32_MAX;
        else if (value < (INT32_MIN >> shift)) value = INT32_MIN;
        else value = (int32_t)((uint32_t)value << shift);
    }
    int32_t high;
    if (value == INT32_MIN && multiplier == INT32_MIN) high = INT32_MAX;
    else {
        uint32_t low, upper;
#if defined(__riscv) && __riscv_xlen == 32
        __asm__("mulh %0, %2, %3\n\tmul %1, %2, %3"
                : "=&r"(upper), "=r"(low) : "r"(value), "r"(multiplier));
#else
        uint64_t product = (uint64_t)((int64_t)value * multiplier);
        low = (uint32_t)product;
        upper = (uint32_t)(product >> 32);
#endif
        /* floor((product + 2^30) / 2^31) also implements the negative
         * nudge followed by truncation toward zero. Use unsigned halves
         * so the carry and doubled high word cannot overflow signed C. */
        uint32_t rounded = low + 0x40000000u;
        upper += rounded < low;
        high = (int32_t)((upper << 1) | (rounded >> 31));
    }
    if (shift < 0) {
        unsigned exponent = (unsigned)-shift;
        uint32_t mask = (1u << exponent) - 1;
        uint32_t threshold = (mask >> 1) + (high < 0);
        high = (high >> exponent) + (((uint32_t)high & mask) > threshold);
    }
    return high;
}

static int idle(void) {
    uint32_t status = npu_read(NPU_STATUS);
    if (status & NPU_ERROR) return KWS_HW_ERROR;
    return status & NPU_BUSY ? KWS_NOT_READY : KWS_OK;
}
int kws_load_model(const kws_model *model) {
    uint32_t version = npu_read(NPU_VERSION);
    if (version != 0x00020002 && version != 0x00020001) return KWS_BAD_VERSION;
    if (npu_read(NPU_STATUS) & NPU_BUSY) return KWS_NOT_READY;
    npu_write(NPU_CONTROL, NPU_CLEAR_DONE | NPU_CLEAR_ERROR);
    npu_write(NPU_CLASS_COUNT, 4);
    for (unsigned i = 0; i < 241; ++i)
        npu_write_weight(i, model->weights[i][0], model->weights[i][1]);
    for (unsigned group = 0; group < 3; ++group) {
        unsigned layer = group == 2 ? 3 : group;
        unsigned base = NPU_PARAMETER + layer * 0x100;
        const kws_parameters *p = &model->parameters[group];
        for (unsigned lane = 0; lane < 8; ++lane) {
            npu_write(base + 4 * lane, p->bias[lane]);
            npu_write(base + 0x20 + 4 * lane, p->mult[lane]);
            npu_write(base + 0x40 + 4 * lane, (uint32_t)(int32_t)p->shift[lane]);
        }
        npu_write(base + 0x60, 1);
        if (idle()) return KWS_HW_ERROR;
    }
    return idle();
}
int kws_load_input(const int8_t input[320]) {
    int error = idle();
    if (error) return error;
    if ((uintptr_t)input & 3u) {
        for (unsigned i = 0; i < 320; ++i) npu_write_input(i, input[i]);
    } else {
        const uint8_t *src = __builtin_assume_aligned(input, 4);
        volatile uint32_t *dst = (volatile uint32_t *)(uintptr_t)(NPU_BASE + NPU_INPUT);
        for (unsigned i = 0; i < 80; ++i) {
            uint32_t word;
            /* Fixed-size memcpy permits an aligned lw without violating
             * the effective type of the caller's INT8 array. RV32 is LE. */
            __builtin_memcpy(&word, src, sizeof word);
            dst[0] = word & 0xffu;
            dst[1] = (word >> 8) & 0xffu;
            dst[2] = (word >> 16) & 0xffu;
            dst[3] = word >> 24;
            src += 4;
            dst += 4;
        }
    }
    return idle();
}
int kws_start_wait(uint32_t poll_limit) {
    uint32_t status = npu_read(NPU_STATUS);
    if (status & NPU_ERROR) return KWS_HW_ERROR;
    if (!(status & NPU_READY)) return KWS_NOT_READY;
    npu_write(NPU_CONTROL, NPU_START);
    while (poll_limit--) {
        status = npu_read(NPU_STATUS);
        if (status & NPU_ERROR) return KWS_HW_ERROR;
        if (status & NPU_DONE) return KWS_OK;
    }
    return KWS_TIMEOUT;
}
int kws_read_result(const kws_model *model, int32_t raw[4], uint32_t *prediction) {
    uint32_t status = npu_read(NPU_STATUS);
    if (status & NPU_ERROR) return KWS_HW_ERROR;
    if (!(status & NPU_DONE)) return KWS_NOT_READY;
    int32_t best = INT32_MIN;
    *prediction = 0;
    for (unsigned c = 0; c < 4; ++c) {
        raw[c] = (int32_t)npu_read(NPU_LOGIT + 4 * c);
        int32_t score = kws_scale(raw[c], model->cmp_mult[c], model->cmp_shift[c]);
        if (score > best) { best = score; *prediction = c; }
    }
    npu_write(NPU_CONTROL, NPU_CLEAR_DONE);
    return idle();
}
