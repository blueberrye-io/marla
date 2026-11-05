/* PC-Attrappe für Host-Tests: gleiche Schnittstelle, druckt statt zu blinken. */
#include "hal_avr.h"
#include <stdio.h>

void hal_init(void)            { setvbuf(stdout, NULL, _IONBF, 0); }
void hal_led_write(bool on)    { printf("LED %s\n", on ? "AN" : "aus"); }
void hal_delay_ms(uint16_t ms) { printf("  warte %u ms\n", (unsigned)ms); }
