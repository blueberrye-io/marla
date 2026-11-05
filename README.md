# MARLA

**M**essages **A**nd **R**egisters: **L**anguage with **A**ctors. Eine kleine Programmiersprache
für Mikrocontroller: Python-Gefühl, C-Semantik, kein Heap, Aktoren statt Threads.

Homepage: <https://marla-lang.de> · Lizenz: MIT (siehe `LICENSE`)

**Stand:** Phase 1 ist fertig (Blink-Programm für den Arduino Uno). Phase 2 (structs, Enums mit
`match`, `Result`, Arrays, Strings, Aktoren, Board-Konfiguration) ist in Arbeit; Zephyr als
zweites Target fehlt noch. Experimentell: Syntax und Semantik können sich noch ändern.

Transpiler von `.mar` nach portablem C99, geschrieben in Python. Sprachdefinition
und alle Design-Beschlüsse: [sprachreferenz_rev2.md](sprachreferenz_rev2.md)
(aktuell; [sprachreferenz.md](sprachreferenz.md) ist die Erstfassung).

Phase-1-Umfang (Rev. 2): Integer-Typen + `bool`, `let`/`let mut` mit Inferenz,
`const` auf oberster Ebene (Constant Folding), Arithmetik/Vergleiche/Logik/Bits,
`as`-Casts, `if`/`elif`/`else`, `while`, `loop`, `break`, `continue`, `return`,
Funktionen, `extern fn` (FFI). Targets: Arduino Uno (ATmega328P) und Host (PC);
der Codegen selbst erzeugt nur portables C99.

## Benutzung

```bash
python3 -m marla.cli compile examples/blink.mar -o build/blink.c

# oder installiert (pip install -e .):
marla compile examples/blink.mar -o build/blink.c
```

## Host-Lauf ohne Board

```bash
make host SRC=examples/blink.mar
./build/blink_host        # druckt: LED AN / warte 500 ms / ...
```

## Arduino Uno bauen & flashen

Braucht `avr-gcc`, `avr-libc`, `avrdude` (macOS: `brew tap osx-cross/avr && brew install avr-gcc avrdude`).

```bash
make                      # build/blink.hex
make flash PORT=/dev/cu.usbmodem1101
make size                 # Flash-/RAM-Verbrauch im Blick behalten
```

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install pytest
.venv/bin/pytest
```

`tests/programs/ok/` muss kompilieren; `tests/programs/err/` trägt in Zeile 1
`# ERWARTET: <Fragment>` — die Fehlermeldungen sind Teil der Testsuite.

## Projektstruktur

```
marla/          Compiler: tokens, lexer, parser (rek. Abstieg), checker, codegen, cli
tests/          pytest-Suite + Programm-Korpus (ok/ und err/)
runtime/        hal_avr.h/.c (Uno), hal_host.c (PC-Attrappe)
examples/       blink.mar (Referenzprogramm Phase 1)
```

## Implementierungs-Festlegungen (über die Referenz hinaus)

Rev. 2 der Referenz hat die früheren Abweichungen übernommen (`on` frei,
Faltungs-Semantik, unsigned-Operanden-Casts gegen die Integer-Promotion-Falle).
Zusätzlich legt die Implementierung fest:

- **Kein Schatten von Konstanten:** `let`/Parameter mit dem Namen einer `const`
  ist ein Compile-Fehler (nötig, weil Konstanten als `#define ml_NAME` erzeugt
  werden — ein gleichnamiges Lokal würde vom Präprozessor zerstört).
- **Casts in Konstanten sind bereichsgeprüft**, kein stilles Abschneiden:
  `0x1234 as u8` ist ein Fehler; Abschneiden explizit per `& 0xFF`.
- **Zyklische Konstanten** (`const A := B`, `const B := A`) sind ein Fehler
  mit Kettenangabe.
- Der `(unsigned)`-Operanden-Cast gilt nur für `u8`/`u16` — `u32` hat bereits
  Integer-Rang; ein `(unsigned)`-Cast würde auf AVR nach 16 Bit abschneiden.
