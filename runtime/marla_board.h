/* Default-Board-Konfiguration (Host / moderne Targets): direct + halt.
 * `marla compile --board <datei.toml>` schreibt eine eigene marla_board.h
 * neben die Ausgabedatei; sie gewinnt über die Include-Reihenfolge
 * (-Ibuild vor -Iruntime). */
#ifndef MARLA_BOARD_H
#define MARLA_BOARD_H
/* keine Defines: strings=direct, panic=halt */
#endif
