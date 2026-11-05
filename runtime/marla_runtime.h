/* MARLA Schicht-0-Runtime (Sprachreferenz Rev. 2, Abschnitte 6, 7.5 und 11).
 *
 * Panic-Hook: der EINE Mechanismus für Laufzeitfehler.
 *  - Host:  Meldung mit Datei/Zeile + Abbruch
 *  - AVR:   keine Ortsangabe; Verhalten per MARLA_PANIC_RESET (Board-Config)
 *
 * Strings (Abschnitt 6): ml_str ist die {Zeiger, Länge}-Sicht. Lesestrategie:
 *  - direct (Host, ARM, moderne AVRs): nackter Zeiger
 *  - tagged (klassische AVRs, -DMARLA_STRINGS_TAGGED): Literale in PROGMEM,
 *    oberstes Zeiger-Bit markiert "Flash", Lesen prüft das Bit (6.2).
 *    Gilt für Chips mit <= 32 KB Flash (Uno) ohne Linker-Kniff.
 */
#ifndef MARLA_RUNTIME_H
#define MARLA_RUNTIME_H
#include "marla_board.h"    /* Board-Schalter: MARLA_STRINGS_TAGGED, MARLA_PANIC_RESET */
#include <stdint.h>
#include <stdbool.h>

typedef struct { const uint8_t *ptr; uint16_t len; } ml_str;

/* Kurz gesperrte Interrupts beim Mailbox-Zugriff: ISRs dürfen senden,
 * ohne dass der Nutzer je über volatile nachdenkt (§9). */
#ifdef __AVR__
#include <avr/io.h>
#include <avr/interrupt.h>
#define ML_IRQ_LOCK()   uint8_t ml__sreg = SREG; cli()
#define ML_IRQ_UNLOCK() SREG = ml__sreg
#else
#define ML_IRQ_LOCK()   do {} while (0)
#define ML_IRQ_UNLOCK() do {} while (0)
#endif

#ifdef __AVR__

void marla_panic(void);
#define ML_PANIC(file, line) marla_panic()
#define ML_IDX(i, n, file, line) ml__idx((i), (n))
static inline uint16_t ml__idx(uint32_t i, uint16_t n) {
    if (i >= n) marla_panic();
    return (uint16_t)i;
}
#define ML_STR_AT(s, i, file, line) ml__str_at((s), (i))

#ifdef MARLA_STRINGS_TAGGED
#include <avr/pgmspace.h>
#define ML_PROGMEM PROGMEM
#define ML_STR_LIT(arr, n) \
    ((ml_str){ .ptr = (const uint8_t *)((uint16_t)(arr) | 0x8000u), .len = (n) })
static inline uint8_t marla_str_read(const uint8_t *p) {
    if ((uint16_t)p & 0x8000u)
        return pgm_read_byte((const uint8_t *)((uint16_t)p & 0x7FFFu));
    return *p;
}
#else
#define ML_PROGMEM
#define ML_STR_LIT(arr, n) ((ml_str){ .ptr = (arr), .len = (n) })
static inline uint8_t marla_str_read(const uint8_t *p) { return *p; }
#endif

static inline uint8_t ml__str_at(ml_str s, uint32_t i) {
    if (i >= s.len) marla_panic();
    return marla_str_read(s.ptr + (uint16_t)i);
}

#else /* Host */

void marla_panic(const char *file, int line);
#define ML_PANIC(file, line) marla_panic((file), (line))
#define ML_IDX(i, n, file, line) ml__idx((i), (n), (file), (line))
static inline uint16_t ml__idx(uint32_t i, uint16_t n, const char *file, int line) {
    if (i >= n) marla_panic(file, line);
    return (uint16_t)i;
}
#define ML_PROGMEM
#define ML_STR_LIT(arr, n) ((ml_str){ .ptr = (arr), .len = (n) })
static inline uint8_t marla_str_read(const uint8_t *p) { return *p; }
#define ML_STR_AT(s, i, file, line) ml__str_at((s), (i), (file), (line))
static inline uint8_t ml__str_at(ml_str s, uint32_t i, const char *file, int line) {
    if (i >= s.len) marla_panic(file, line);
    return s.ptr[(uint16_t)i];
}

#endif

/* Inhaltsvergleich (=) — Byte für Byte, tag-bewusst (6.5) */
bool marla_str_eq(ml_str a, ml_str b);

/* strbuf-Kern (6.3): false = Überlauf, Puffer bleibt unverändert */
bool marla_buf_append(uint8_t *data, uint16_t cap, uint16_t *len, ml_str s);
bool marla_buf_append_u32(uint8_t *data, uint16_t cap, uint16_t *len, uint32_t v);

#ifndef __AVR__
/* Host-Attrappen für Peripherie-Aktoren (Abschnitt 10) */
uint32_t marla_host_now_ms(void);
int marla_host_getchar(void);   /* nicht-blockierend; -1 = nichts da */
#endif

#endif
