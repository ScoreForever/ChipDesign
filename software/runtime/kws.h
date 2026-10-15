#ifndef KWS_H
#define KWS_H
#include <stdint.h>
#ifndef KWS_USE_DMA
#define KWS_USE_DMA 1
#endif
#ifndef KWS_PIPELINE
#define KWS_PIPELINE 1
#endif

typedef struct {
    int32_t bias[8];
    int32_t mult[8];
    int8_t shift[8];
} kws_parameters;
typedef struct {
    uint32_t weights[241][2];
    kws_parameters parameters[3];
    int32_t cmp_mult[4];
    int8_t cmp_shift[4];
} kws_model;
enum { KWS_OK = 0, KWS_HW_ERROR = 1, KWS_TIMEOUT = 2,
       KWS_NOT_READY = 3, KWS_BAD_VERSION = 4, KWS_BAD_PROFILE = 5,
       KWS_STACK_OVERFLOW = 6 };
int kws_load_model(const kws_model *model);
int kws_load_input(const int8_t input[320]);
/* Pair begin/finish with the same input; do not start NPU before finish. */
int kws_input_begin(const int8_t input[320]);
int kws_input_finish(const int8_t input[320]);
int kws_start_wait(uint32_t poll_limit);
int kws_start_wait_timed(uint32_t poll_limit, uint32_t *start_cycle);
int kws_read_result(const kws_model *model, int32_t raw[4], uint32_t *prediction);
int32_t kws_scale(int32_t value, int32_t multiplier, int shift);
#endif
