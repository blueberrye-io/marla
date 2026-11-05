#include "marla_runtime.h"

#ifdef __AVR__

#include <avr/interrupt.h>
#include <avr/wdt.h>

void marla_panic(void) {
#ifdef MARLA_PANIC_RESET
    wdt_enable(WDTO_15MS);          /* Watchdog beißt in 15 ms -> Reset */
    for (;;) {}
#else
    cli();                          /* Interrupts aus + Halt (Default) */
    for (;;) {}
#endif
}

#else /* Host */

#include <stdio.h>
#include <stdlib.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

void marla_panic(const char *file, int line) {
    fprintf(stderr, "PANIC %s:%d: Bereichsfehler\n", file, line);
    abort();
}

uint32_t marla_host_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

int marla_host_getchar(void) {
    fd_set fds;
    struct timeval tv = { 0, 0 };
    FD_ZERO(&fds);
    FD_SET(0, &fds);
    if (select(1, &fds, NULL, NULL, &tv) > 0) {
        unsigned char b;
        if (read(0, &b, 1) == 1) return (int)b;
    }
    return -1;
}

#endif

bool marla_str_eq(ml_str a, ml_str b) {
    if (a.len != b.len) return false;
    for (uint16_t i = 0; i < a.len; i++) {
        if (marla_str_read(a.ptr + i) != marla_str_read(b.ptr + i)) return false;
    }
    return true;
}

bool marla_buf_append(uint8_t *data, uint16_t cap, uint16_t *len, ml_str s) {
    if ((uint16_t)(cap - *len) < s.len) return false;   /* Überlauf ist nie still */
    for (uint16_t i = 0; i < s.len; i++) {
        data[*len + i] = marla_str_read(s.ptr + i);
    }
    *len += s.len;
    return true;
}

bool marla_buf_append_u32(uint8_t *data, uint16_t cap, uint16_t *len, uint32_t v) {
    uint8_t digits[10];
    uint8_t n = 0;
    do {
        digits[n++] = (uint8_t)('0' + (v % 10u));
        v /= 10u;
    } while (v != 0u);
    if ((uint16_t)(cap - *len) < n) return false;
    for (uint8_t i = 0; i < n; i++) {
        data[*len + i] = digits[n - 1 - i];             /* Ziffern umdrehen */
    }
    *len += n;
    return true;
}
