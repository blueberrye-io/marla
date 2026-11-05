# MARLA Phase 1 — Bauen & Flashen für Arduino Uno Rev3 (Anhang E)
#
#   make            -> build/blink.hex
#   make host       -> build/blink_host (PC-Attrappe, druckt statt zu blinken)
#   make flash      -> auf den Uno brennen (PORT ggf. anpassen)
#   make size       -> Flash-/RAM-Verbrauch (Frühwarnsystem gegen versteckte Kosten)

PYTHON  ?= python3
SRC     ?= examples/blink.mar
NAME    := $(basename $(notdir $(SRC)))
BUILD   := build

MCU     := atmega328p
F_CPU   := 16000000UL
PORT    ?= /dev/cu.usbmodem*
BAUD    := 115200

all: $(BUILD)/$(NAME).hex

$(BUILD)/$(NAME).c: $(SRC) marla/*.py
	@mkdir -p $(BUILD)
	$(PYTHON) -m marla.cli compile $(SRC) -o $@

$(BUILD)/$(NAME).elf: $(BUILD)/$(NAME).c runtime/hal_avr.c runtime/hal_avr.h runtime/marla_runtime.c
	avr-gcc -mmcu=$(MCU) -DF_CPU=$(F_CPU) -Os -std=c99 \
	        -Iruntime -o $@ $(BUILD)/$(NAME).c runtime/hal_avr.c runtime/marla_runtime.c

$(BUILD)/$(NAME).hex: $(BUILD)/$(NAME).elf
	avr-objcopy -O ihex -R .eeprom $< $@

flash: $(BUILD)/$(NAME).hex
	avrdude -c arduino -p m328p -P $(PORT) -b $(BAUD) -U flash:w:$<:i

size: $(BUILD)/$(NAME).elf
	avr-size $<

host: $(BUILD)/$(NAME).c runtime/hal_host.c runtime/marla_runtime.c
	cc -std=c99 -Iruntime -o $(BUILD)/$(NAME)_host $(BUILD)/$(NAME).c \
	   runtime/hal_host.c runtime/marla_runtime.c
	@echo "starten mit: ./$(BUILD)/$(NAME)_host"

test:
	.venv/bin/pytest

clean:
	rm -rf $(BUILD)

.PHONY: all flash size host test clean
