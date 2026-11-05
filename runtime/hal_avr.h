#ifndef HAL_AVR_H
#define HAL_AVR_H
#include <stdint.h>
#include <stdbool.h>
void hal_init(void);
void hal_led_write(bool on);
void hal_delay_ms(uint16_t ms);
#endif
