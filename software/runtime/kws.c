#include "kws.h"
#include "npu_mmio.h"
#include <limits.h>

/* Mirrors tools/quantize.py, including negative rounding and saturation. */
int32_t kws_scale(int32_t value, int32_t multiplier, int shift) {
    if (shift > 0) {
        int64_t wide = (int64_t)value * ((int64_t)1 << shift);
        value = wide > INT32_MAX ? INT32_MAX : wide < INT32_MIN ? INT32_MIN : (int32_t)wide;
    }
    int32_t high;
    if (value == INT32_MIN && multiplier == INT32_MIN) high = INT32_MAX;
    else {
        int64_t product = (int64_t)value * multiplier;
        int64_t rounded = product + (product >= 0 ? 1073741824LL : -1073741823LL);
        high = rounded >= 0 ? (int32_t)(rounded >> 31) : -(int32_t)((-rounded) >> 31);
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
    for (unsigned i = 0; i < 320; ++i) npu_write_input(i, input[i]);
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
