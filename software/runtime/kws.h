#ifndef KWS_H
#define KWS_H
#include <stdint.h>

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
int kws_start_wait(uint32_t poll_limit);
int kws_read_result(const kws_model *model, int32_t raw[4], uint32_t *prediction);
int32_t kws_scale(int32_t value, int32_t multiplier, int shift);
#endif
