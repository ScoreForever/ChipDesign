#include "kws.h"
#include "npu_mmio.h"
#include "workload_data.h"

typedef struct {
    int32_t raw[4];
    uint32_t prediction, input_cycles, compute_cycles, read_cycles, perf_total, total_cycles;
} sample_result;
typedef struct {
    uint32_t count, model_cycles, error, stack_used;
    sample_result samples[SAMPLE_COUNT];
} workload_result;
extern workload_result __results;
extern uint32_t __stack_bottom[], __stack_top[];
static inline uint32_t cycles(void) {
    uint32_t value;
    __asm__ volatile(".option push\n.option arch, +zicsr\ncsrr %0, 0xb00\n.option pop"
                     : "=r"(value) :: "memory");
    return value;
}
int main(void) {
    volatile uint32_t *magic = (volatile uint32_t *)0x80001fe0;
    workload_result *r = &__results;
    magic[0] = 0x12345678;
    magic[1] = (uint32_t)(uintptr_t)r;
    magic[2] = 1; /* Result schema version. */
    r->count = SAMPLE_COUNT;
    r->error = 0;
    uint32_t begin = cycles();
    int error = kws_load_model(&workload_model);
    r->model_cycles = cycles() - begin;
    for (unsigned i = 0; !error && i < SAMPLE_COUNT; ++i) {
        sample_result *s = &r->samples[i];
        uint32_t total = cycles();
        begin = cycles();
        error = kws_load_input(workload_inputs[i]);
        s->input_cycles = cycles() - begin;
        if (error) break;
        begin = cycles();
        error = kws_start_wait(1000000);
        s->compute_cycles = cycles() - begin;
        if (error) break;
        s->perf_total = npu_read(NPU_PERF_TOTAL);
        if (npu_read(NPU_PERF_STATUS) != 1) { error = KWS_BAD_PROFILE; break; }
        begin = cycles();
        error = kws_read_result(&workload_model, s->raw, &s->prediction);
        s->read_cycles = cycles() - begin;
        s->total_cycles = cycles() - total;
    }
    uint32_t *watermark = __stack_bottom;
    while (watermark < __stack_top && *watermark == 0xa5a5a5a5) ++watermark;
    r->stack_used = (uint32_t)((uintptr_t)__stack_top - (uintptr_t)watermark);
    if (*__stack_bottom != 0xa5a5a5a5) error = KWS_STACK_OVERFLOW;
    r->error = error;
    magic[3] = npu_read(NPU_ERROR_CODE);
    __asm__ volatile("fence rw, rw" ::: "memory");
    magic[0] = error ? 0xdeadbeef : 0xc0dec0de;
    return 0;
}
