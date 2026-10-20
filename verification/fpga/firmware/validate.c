#include <stdint.h>
#include "npu_mmio.h"

/* Fixed SRAM ABI shared with workflow.py. Golden results never enter firmware. */
typedef struct {
    uint32_t classes, mode, repeats, seed;
    uint32_t weights[482];
    int32_t bias[24], mult[16], shift[16];
    uint32_t input[80];
} config_t;
typedef struct {
    uint32_t magic, line, checks, jobs, version, status, error, perf;
    int32_t logits[8];
    uint32_t profile[12];
    uint32_t dma_checks, memory_checks, busy_checks, irq_checks;
} result_t;
#define CFG ((volatile config_t *)0x80001000u)
#define RES ((volatile result_t *)0x80001d00u)
#define CHECK(x) do { RES->checks++; if (!(x)) {RES->line=__LINE__; return -1;} } while (0)

/* Observe the real CPU interrupt inputs, with global interrupts disabled.
 * This verifies routing/pending/clear; it does not test trap entry or mret. */
static int irq_level(unsigned bit, unsigned asserted) {
    uint32_t pending=0;
    for(unsigned i=0;i<100;i++) {
        __asm__ volatile("csrr %0, mip" : "=r"(pending));
        if (((pending>>bit)&1u)==asserted) break;
    }
    CHECK(((pending>>bit)&1u)==asserted);
    RES->irq_checks++;
    return 0;
}

static int idle_ok(void) {
    return (npu_read(NPU_STATUS) & (NPU_READY|NPU_BUSY|NPU_ERROR)) == NPU_READY;
}
static int wait_done(unsigned reg, unsigned mask) {
    for (unsigned i=0;i<1000000;i++) if (npu_read(reg)&mask) return 0;
    return -1;
}
static int expect_error(unsigned code) {
    CHECK(npu_read(NPU_STATUS)&NPU_ERROR);
    CHECK(npu_read(NPU_ERROR_CODE)==code);
    npu_write(NPU_CONTROL,NPU_CLEAR_ERROR|NPU_IRQ_ENABLE);
    CHECK(!(npu_read(NPU_STATUS)&NPU_ERROR));
    return 0;
}
static int dma(const volatile void *src, unsigned dst, unsigned count) {
    npu_dma_start((const void *)src,dst,count);
    npu_write(NPU_DMA_CTRL,2); /* Enable IRQ without starting a second transfer. */
    CHECK(wait_done(NPU_DMA_STATUS,NPU_DMA_DONE)==0);
    CHECK(!(npu_read(NPU_DMA_STATUS)&NPU_DMA_BUSY));
    if(irq_level(17,1))return -1;
    npu_dma_clear_done();
    CHECK(!(npu_read(NPU_DMA_STATUS)&NPU_DMA_DONE));
    if(irq_level(17,0))return -1;
    RES->dma_checks++;
    return 0;
}
static int memory_test(void) {
    volatile uint32_t *scratch=(volatile uint32_t *)0x80001b00;
    uint32_t state=CFG->seed|1;
    for(unsigned pass=0;pass<8;pass++) {
        for(unsigned i=0;i<64;i++) {
            state^=state<<13; state^=state>>17; state^=state<<5;
            scratch[i]=state;
        }
        uint32_t expected=CFG->seed|1;
        for(unsigned p=0;p<=pass;p++) for(unsigned i=0;i<64;i++) {
            expected^=expected<<13; expected^=expected>>17; expected^=expected<<5;
            if(p==pass) {CHECK(scratch[i]==expected); RES->memory_checks++;}
        }
    }
    scratch[0]=0x12345678;
    ((volatile uint8_t *)scratch)[1]=0xab;
    CHECK(scratch[0]==0x1234ab78);
    ((volatile uint16_t *)scratch)[1]=0xcdef;
    CHECK(scratch[0]==0xcdefab78);
    return 0;
}
static int invalid_tests(void) {
    const unsigned invalid[]={0,9,15};
    for(unsigned i=0;i<3;i++) {npu_write(NPU_CLASS_COUNT,invalid[i]); if(expect_error(1))return -1;}
    npu_write(0x80,1); if(expect_error(6))return -1;
    npu_write(NPU_VERSION,1); if(expect_error(6))return -1;
    npu_write(NPU_PERF_TOTAL,1); if(expect_error(6))return -1;
    npu_write(NPU_WEIGHT+4,0); if(expect_error(4))return -1;
    npu_write(NPU_WEIGHT,0); npu_write(NPU_WEIGHT+12,0); if(expect_error(4))return -1;
    npu_write(NPU_PARAMETER+0x260,1); if(expect_error(6))return -1;
    /* Clear every staging lane before testing quantization rejects. */
    for(unsigned i=0;i<8;i++) {
        npu_write(NPU_PARAMETER+4*i,0);
        npu_write(NPU_PARAMETER+0x20+4*i,0x40000000);
        npu_write(NPU_PARAMETER+0x40+4*i,0);
    }
    npu_write(NPU_PARAMETER+0x20,0xffffffff);
    npu_write(NPU_PARAMETER+0x60,1); if(expect_error(5))return -1;
    npu_write(NPU_PARAMETER+0x20,0x40000000);
    npu_write(NPU_PARAMETER+0x40,0xffffffe0);
    npu_write(NPU_PARAMETER+0x60,1); if(expect_error(5))return -1;
    npu_write(NPU_PARAMETER+0x40,0);
    return 0;
}
static int load_model(void) {
    if(CFG->mode==1) {
        if(dma(CFG->weights,NPU_BASE+NPU_WEIGHT,482))return -1;
    } else {
        for(unsigned i=0;i<241;i++) npu_write_weight(i,CFG->weights[2*i],CFG->weights[2*i+1]);
    }
    for(unsigned g=0;g<3;g++) {
        unsigned base=NPU_PARAMETER+(g==2?3:g)*0x100;
        for(unsigned i=0;i<8;i++) {
            npu_write(base+4*i,(uint32_t)CFG->bias[g*8+i]);
            npu_write(base+0x20+4*i,g<2?(uint32_t)CFG->mult[g*8+i]:0);
            npu_write(base+0x40+4*i,g<2?(uint32_t)CFG->shift[g*8+i]:0);
        }
        npu_write(base+0x60,1); CHECK(idle_ok());
    }
    npu_write(NPU_CLASS_COUNT,CFG->classes);
    CHECK(npu_read(NPU_CLASS_COUNT)==CFG->classes);
    return 0;
}
static int inference(void) {
    const volatile uint8_t *bytes=(const volatile uint8_t *)CFG->input;
    for(unsigned job=0;job<CFG->repeats;job++) {
        if(CFG->mode==1) {
            if(dma(CFG->input,NPU_BASE+NPU_INPUT_PACKED,80))return -1;
        } else if(CFG->mode==2) {
            for(unsigned i=0;i<80;i++) npu_write(NPU_INPUT_PACKED+4*i,CFG->input[i]);
        } else {
            for(unsigned i=0;i<320;i++) npu_write_input(i,(int8_t)bytes[i]);
        }
        for(unsigned i=0;npu_read(NPU_INPUT_PENDING);i++) CHECK(i<10000);
        CHECK(idle_ok());
        npu_write(NPU_CONTROL,NPU_START|NPU_IRQ_ENABLE);
        if(CFG->mode==3) {
            /* Intentional writes while busy must be rejected and not alter model. */
            CHECK(npu_read(NPU_STATUS)&NPU_BUSY);
            switch(job%4) {
                case 0: npu_write(NPU_CLASS_COUNT,CFG->classes==8?1:8); break;
                case 1: npu_write(NPU_WEIGHT,0x55555555); break;
                case 2: npu_write(NPU_INPUT,127); break;
                default: npu_write(NPU_CONTROL,NPU_START|NPU_IRQ_ENABLE); break;
            }
            if(expect_error(2))return -1;
            RES->busy_checks++;
        }
        CHECK(wait_done(NPU_STATUS,NPU_DONE)==0);
        CHECK(!(npu_read(NPU_STATUS)&(NPU_BUSY|NPU_ERROR)));
        CHECK(npu_read(NPU_CLASS_COUNT)==CFG->classes);
        CHECK(npu_read(NPU_PERF_STATUS)==1);
        if(irq_level(16,1))return -1;
        for(unsigned i=0;i<8;i++) {
            int32_t got=(int32_t)npu_read(NPU_LOGIT+4*i);
            if(job) CHECK(got==RES->logits[i]);
            RES->logits[i]=got;
        }
        for(unsigned i=0;i<12;i++) RES->profile[i]=npu_read(0x40+4*i);
        CHECK(RES->profile[8]==RES->profile[9]); /* issues == retires */
        RES->perf=npu_read(NPU_PERF_TOTAL);
        CHECK(RES->perf>0);
        CHECK(npu_read(NPU_STATUS)&NPU_DONE); /* sticky until explicit clear */
        npu_write(NPU_CONTROL,NPU_CLEAR_DONE|NPU_IRQ_ENABLE);
        CHECK(!(npu_read(NPU_STATUS)&NPU_DONE));
        if(irq_level(16,0))return -1;
        RES->jobs++;
    }
    return 0;
}
int main(void) {
    for(unsigned i=0;i<sizeof(result_t)/4;i++) ((volatile uint32_t *)RES)[i]=0;
    RES->magic=0x12345678;
    RES->version=npu_read(NPU_VERSION);
    int error=0;
    if(RES->version!=0x00020002) {RES->line=__LINE__;error=-1;}
    npu_write(NPU_CONTROL,NPU_CLEAR_DONE|NPU_CLEAR_ERROR);
    if(!error)error=memory_test();
    if(!error && CFG->mode==3)error=invalid_tests();
    if(!error)error=load_model();
    if(!error)error=inference();
    RES->status=npu_read(NPU_STATUS);
    RES->error=npu_read(NPU_ERROR_CODE);
    __asm__ volatile("fence rw,rw" ::: "memory");
    RES->magic=error?0xdeadbeef:0xc0dec0de;
    return 0;
}
