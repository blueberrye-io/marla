#include "hal_avr.h"
#include <avr/io.h>
#include <util/delay.h>

void hal_init(void)          { DDRB |= (1 << DDB5); }          /* PB5 = Ausgang */
void hal_led_write(bool on)  { if (on) PORTB |= (1 << PORTB5);
                               else    PORTB &= ~(1 << PORTB5); }
void hal_delay_ms(uint16_t ms) {
    while (ms--) { _delay_ms(1); }   /* _delay_ms braucht KONSTANTE -> 1er-Schleife */
}
