# MARLA — Sprachreferenz & Design-Beschlüsse

> **MARLA** — **M**essages **A**nd **R**egisters: **L**anguage with **A**ctors.
> Arbeitsdokument für eine neue Embedded-Programmiersprache.
> Stand: Juli 2026 · Status: Design-Phase, vor Implementierungsbeginn

---

## 1. Ziel und Philosophie

**Zielplattformen:** Embedded-Mikrocontroller, konkret zwei Targets:

- **AVR** (8-Bit, z. B. ATmega328P / Arduino Uno) — Phase 1
- **Zephyr RTOS** (ARM/RISC-V) — ab Phase 2
  - Konkrete Boards u. a. **Teensy 4.1** (NXP i.MX RT1062, Cortex-M7 @ 600 MHz, 1 MB RAM, FPU). **Beschluss:** Der Teensy wird als Zephyr-Board angebunden (Zephyr hat Board-Support), *nicht* über Teensyduino — dessen C++-Bibliotheken passen nicht zum C-FFI. Er ist damit kein drittes Target, sondern ein Eintrag in der Board-Konfiguration (Abschnitt 10). Eigenheit: Flashen über PJRC-Bootloader (`teensy_loader_cli`). Die FPU des M7 ist der erwartete Anlass, später Floats einzuführen (dafür ist `/` reserviert).

**Implementierungsweg:** Transpiler zu **portablem C99**, geschrieben in **Python** (läuft auf dem PC, nicht auf dem Zielgerät). Kompilierung dann per avr-gcc bzw. Zephyr-Toolchain (west/CMake).

**Leitprinzipien:**

1. **Keine versteckten Kosten.** Jedes Sprachfeature muss eine offensichtliche, billige C-Übersetzung haben. Kein Heap, kein Garbage Collector, keine Laufzeit-Magie.
2. **Fehler zur Compile-Zeit statt zur Laufzeit.** Statische Typen, Pflicht zur Fehlerbehandlung, keine impliziten Umwandlungen.
3. **Python-Gefühl, C-Semantik.** Die Sprache fühlt sich beim Schreiben Python-artig an, verhält sich aber wie C: Speicher auf Stack oder global, Typen stehen zur Compile-Zeit fest.
4. **Der Codegen erzeugt nur portables C99.** Alles Hardwarenahe geht durch FFI bzw. eine Target-spezifische HAL-Schicht. Der Compiler selbst bleibt target-agnostisch.
5. **Lesbares C erzeugen:** mit Kommentaren und `#line`-Direktiven auf den Original-Quelltext, damit GDB-Fehler auf die eigene Sprache zeigen.

---

## 2. Paradigmen-Profil

| Paradigma | Entscheidung |
|---|---|
| Imperativ/prozedural | **Kern der Sprache** — Hardware denkt imperativ |
| Statische Typisierung | **Ja**, mit Typinferenz (`let x := 5`) |
| Unveränderlichkeit als Standard | **Ja** — `let` ist konstant, `let mut` explizit veränderlich |
| OOP: structs mit Methoden | **Ja** (Phase 2) — `motor.set_speed(50)` → `Motor_set_speed(&motor, 50)`, null Overhead |
| OOP: Vererbung, vtables | **Nein** — versteckte Kosten, in Embedded fast nie das richtige Werkzeug |
| Datentragende Enums + Pattern Matching | **Ja** (Phase 2) — Rückgrat von Datenmodellierung und Fehlerbehandlung |
| Fehlerbehandlung | **Result-Typen** (`Ok`/`Err`) statt Exceptions; `?`-Operator zum Durchreichen (Phase 2) |
| Funktionen als Werte | **Ja** — transpiliert zu C-Funktionszeigern (Interrupts, Callbacks) |
| Closures | **Nein** — brauchen Speicher für eingefangene Variablen |
| Aktoren / Message Passing | **Ja, statische Variante** (Phase 2) — siehe Abschnitt 9 |
| Async/await (Zustandsmaschinen) | **Phase 3** — Protothread-artige Compiler-Transformation |
| Garbage Collection, dynamische Typen | **Nein** |
| Exceptions | **Nein** — brauchen Stack-Unwinding, unvorhersagbares Timing |
| Eigene Threads/Scheduler | **Nein** — Zephyr-Threads / AVR-Interrupts via FFI |

Kurzformel: **„Rust light mit Python-Gemüt"** — prozeduraler Kern, statische Typen mit Inferenz, structs statt Klassen, Enums mit Matching, Results statt Exceptions, statische Aktoren.

---

## 3. Syntax-Grundsätze

- **Blöcke durch Einrückung** (Python-Stil). Der Lexer erzeugt INDENT/DEDENT-Tokens über einen Stack von Einrückungstiefen (wie CPython).
- **Kommentare mit `#`** (dadurch bleibt `//` als Operator frei).
- **Typannotationen als `name: Typ`** (Python/Rust-Stil, leichter zu parsen als Cs `Typ name`).
- **Zuweisung ist ein Statement, kein Ausdruck.** Kein `x := y := 5`, kein `while (x := ...)`.
- **Senden ist ein Statement, kein Ausdruck** (analog zur Zuweisung).

---

## 4. Operatoren (beschlossen)

| Zweck | Operator(en) | Anmerkung |
|---|---|---|
| Zuweisung | `:=` | Pascal-Erbe; nur als Statement |
| Kombinierte Zuweisung | `+=`, `-=`, … | erlaubt, ebenfalls nur Statement |
| Inkrement/Dekrement | — | **kein** `++`/`--` |
| Gleichheit / Ungleichheit | `=` / `<>` | Pascal-Stil; `==` existiert nicht (Compiler gibt Hinweis „meintest du `=`?") |
| Identität (Sichten/Referenzen) | `is` | reserviert (Python-Vorbild); `=` bleibt Inhaltsvergleich — auch bei Strings (siehe 6.5) |
| Vergleiche | `<`, `<=`, `>`, `>=` | |
| Logik | `and`, `or`, `not` | Wortoperatoren (Schnittmenge Pascal/Python) |
| Arithmetik | `+`, `-`, `*` | |
| Ganzzahldivision | `//` | Python-Erbe; hält `/` für spätere Float-Division frei |
| Divisionsrest | `mod` | selbsterklärender als `%` |
| Bitoperationen | `&`, `\|`, `^`, `<<`, `>>` | C-Stil; frei, weil Logik Wortoperatoren nutzt |
| Nachricht senden | `->>` | siehe Abschnitt 9; eigenes Token, getrennt von `->` |
| Rückgabetyp | `->` | nur im Funktionskopf: `fn name(...) -> Typ:` |
| Expliziter Cast | `as` | `x as u16` |
| Fehler durchreichen | `?` | Phase 2, mit Result-Typen |

**Division/Modulo bei negativen Zahlen: C-Semantik** (Rundung zur Null, `-7 // 2 = -3`, Rest hat Vorzeichen des Dividenden). Begründung: direkter 1:1-Codegen zu Cs `/` und `%`, keine Korrektur-Instruktionen auf dem AVR. Optionale Compiler-Warnung bei `//`/`mod` auf vorzeichenbehafteten Typen denkbar.

**Lexer-Regel:** längster Match gewinnt — `->>` vor `->` vor `-`; `:=` vor `:`; `<=` vor `<` usw.

**Keine impliziten Typumwandlungen.** `u8 + u16` ist ein Compile-Fehler; Casts nur explizit mit `as`. Erspart die komplette C-Promotion-Problematik und böse Überraschungen auf AVR (dort ist `int` nur 16 Bit).

---

## 5. Typsystem

**Phase 1:** nur feste Integer-Typen und `bool`:

```
u8  i8  u16  i16  u32  i32  bool
```

- Generiertes C verwendet konsequent `<stdint.h>`-Typen (`uint8_t` …) → identisches Verhalten auf AVR und ARM.
- **Keine Floats** in Phase 1 (auf AVR nur Software-Emulation; `/` bleibt dafür reserviert).
- Keine Strings, keine Arrays in Phase 1. (String-Design ist beschlossen — Abschnitt 6 — Implementierung ab Phase 2.)

**Variablen:**

```
let x: u16 := 500        # unveränderlich, expliziter Typ
let y := 5               # unveränderlich, Typ inferiert (Pflicht-Annotation bei Mehrdeutigkeit)
let mut counter: u16 := 0  # veränderlich
```

Zuweisung an nicht-`mut` = Compile-Fehler. Bonus auf AVR: nachweislich Konstantes kann automatisch in den Flash (`PROGMEM`) statt ins knappe RAM — perspektivisch ohne manuelles `pgm_read_byte()`.

---

## 6. Strings (beschlossen, Implementierung Phase 2)

### 6.1 Der Typ `str` — unveränderliche Sicht

- `str` ist intern ein struct aus **`{ Zeiger, u16 Länge }`** (4 Bytes auf AVR, 8 auf ARM) — Rust/Go-Modell, *nicht* Cs null-terminierte Zeiger. Länge abfragen ist gratis, Teilstrings sind neue Sichten ohne Kopieren.
- Ein `str` ist **immer eine unveränderliche Sicht** auf Daten, die woanders liegen (Literal im Flash oder `strbuf` im RAM). Es gibt keine Operation, die durch ein `str` schreibt — Schreibschutz per Typsystem, keine Laufzeitprüfung nötig.
- **FFI-Höflichkeit:** Hinter *Literalen* legt der Compiler still ein `\0` ab (kostet 1 Byte). `str` als Typ verspricht aber **keine** Null-Terminierung — Übergabe an C-Funktionen, die das erwarten, nur über explizite Konvertierung in einen Puffer.

### 6.2 Speicherort der Literale — der Tag-Bit-Beschluss (Option B)

- **ARM/Zephyr (inkl. Teensy):** Literale liegen im Flash, der normal adressierbar ist. `str` ist ein nackter Zeiger, null Kosten.
- **AVR klassisch (Harvard):** Literale wandern automatisch in den Flash (`PROGMEM`), der Nutzer sieht davon **nichts**. Da ein Zeiger allein nicht verrät, ob er in Flash oder RAM zeigt, markiert das **oberste Bit des Zeigers** „Flash": Lesezugriff wird `if (ptr & 0x8000) pgm_read_byte(...) else *ptr` — ein Bittest, 2–3 Zyklen pro Byte. Bewusste, kleine, dokumentierte Ausnahme vom „keine versteckten Kosten"-Prinzip; sie kauft die unsichtbare Lösung des hässlichsten AVR-Alltagsproblems.
- **Reichweite des Tricks:** Der Compiler legt alle Literale in eine eigene Linker-Section (z. B. `.marla_strings`), die das Linker-Skript **an den Flash-Anfang unter 0x8000 pinnt**. Damit lautet die Einschränkung nicht „Chip ≤ 32 KB Flash", sondern nur „≤ 32 KB String-Literale pro Programm" (Verstoß = harter Linker-Fehler beim Bauen).
  - ✅ ATmega328P & Geschwister, klassische ATtinys — trivial abgedeckt
  - ✅ **ATmega2560**: funktioniert dank Section-Kniff (Literale im unteren Flash, „nahes" `pgm_read_byte` reicht; RAM endet bei 0x21FF). **Nicht unterstützt:** externes RAM via XMEM oberhalb 0x8000 (dort würde das Tag-Bit lügen; Nutzung nur via FFI auf eigene Verantwortung).
  - ✅ Moderne ATtiny 0/1/2-Serie & AVR-Dx: Flash ist dort **in den Datenadressraum eingeblendet** — kein PROGMEM-Theater, Strategie `direct` statt `tagged`.
  - ❌ XMega: gestrichen (eigenwilliges Speicherlayout, einziger echter Sonderfall).
- **Board-Konfigurationsdatei** (Abschnitt 10) erhält einen Schalter für die Lesestrategie: `tagged` (klassische AVRs) / `direct` (moderne AVRs, ARM). Pro AVR-Target gehört ein Linker-Skript-Fragment zur Infrastruktur.

### 6.3 `strbuf[N]` — der veränderliche Puffer

```
let mut line: strbuf[64]       # 64 Bytes Kapazität, Länge 0; Stack oder global, nie Heap

line.append("Temp: ")?
line.append_u16(temp)?
```

- **Operationen (Startumfang):** `append(s: str)`, `append_u8/u16/…(n)`, `clear()`, `len()`. Mehr erst bei realem Bedarf.
- **Überlauf ist nie still:** `append` gibt `Result` zurück; `?` reicht durch. Explizites Ignorieren braucht ein sichtbares Idiom (wird mit dem Result-Kapitel festgelegt).
- **Sicht-Regel:** Ein `strbuf` kann überall stehen, wo `str` erwartet wird — der Compiler baut die Sicht automatisch (analog `bytearray` → `bytes` in Python). Achtung: Die Sicht ist ein **Längen-Schnappschuss**; wächst der Puffer danach, weiß eine alte Sicht nichts davon.
- Entspricht Rusts `heapless::String` / Adas bounded strings — bewährtes Embedded-Muster.

### 6.4 Zeichen-Semantik: Bytes, ehrlich

- **Strings sind Byte-Folgen. Literale sind UTF-8-kodiert. `len` zählt Bytes.** `"Grüße"` funktioniert (6 Bytes) und erscheint auf UTF-8-fähigen Displays korrekt.
- **Kein `char`-Typ.** Das Element eines Strings ist ein `u8`.
- **Byte-Literale** in einfachen Anführungszeichen: `'A'` = `u8` 65, `'\n'` = 10. Erlaubt: ASCII + Escapes (`\n \r \t \0 \xFF`). `'ü'` = Compile-Fehler mit Erklärung („2 Bytes in UTF-8").
- **Nicht angeboten:** Zeichen-Indexierung, Groß-/Kleinschreibung, Normalisierung. Zielanwendungen (UART-Protokolle, LCDs, Logs) brauchen das nicht.
- Eselsbrücke für Python-Kenner: unser `str` verhält sich wie Pythons **`bytes`** — nur mit bequemen UTF-8-Literalen.
- Indexierung `s[i] -> u8` kommt mit den Arrays (gleiche Bereichsprüfungs-Regel, dort zu entscheiden).

### 6.5 Vergleiche

- **`=` vergleicht Inhalte** — auch bei Strings (Byte-für-Byte; bei typischen Embedded-Strings spottbillig). Konsistent mit Python-Erwartung.
- **Kein `==`.** Der Compiler behält seine Fehlermeldung „meintest du `=`?" — sie bleibt wertvoll für C/Python-Umsteiger. Ein `==` mit Zeiger-Semantik (JavaScript-Muster) wäre eine stille Falle und ist **abgelehnt**.
- **`is`** (Python-Vorbild) ist ab sofort **reserviertes Keyword** für Identitätsvergleich („dieselbe Sicht": gleicher Zeiger und gleiche Länge). Implementierung unkritisch/später — Nischenfall.

### 6.6 Ausblick (nicht Phase 2)

- **f-Strings als reiner Compile-Zeit-Zucker** (Phase-3-Kandidat): `f"Temp: {temp} C"` → Folge von `append`-Aufrufen; keine Formatstring-Interpretation zur Laufzeit, nur benötigte Zahl-zu-Text-Routinen werden eingebunden. Kein `printf` (kostet auf AVR >1 KB Flash).
- Denkbare Frühform vor dem vollen Feature: Literal-only-Strings als Debug-Helfer („String an UART-Aktor").

---

## 7. Structs & Parameter-Regeln (beschlossen, Implementierung Phase 2)

### 7.1 Syntax

```
struct Point:
    x: i16
    y: i16 := 0          # Default-Wert, gleiche :=-Regel wie überall

    fn dist_to(self, other: Point) -> u16:
        ...

    fn reset(mut self):
        self.x := 0
        self.y := 0
```

- **Felder** wie Variablen: `name: Typ`, optional Default per `:=`.
- **Konstruktion** mit derselben Syntax wie Enum-Konstruktoren: `Point(10, 20)` positional oder `Point(x: 10, y: 20)` benannt; Felder mit Default sind weglassbar. *Eine* Regel für Enums und structs.
- **Methoden** im struct-Block, erster Parameter `self` (Python-Vorbild). Transpiliert zu `Point_dist_to(&p, &other)` — normaler C-Aufruf, null Overhead.
- **Keine Vererbung** (früher Beschluss), **keine Sichtbarkeits-Keywords** in Phase 2 (alles sichtbar; nur Aktor-Zustand ist speziell geschützt).

### 7.2 Die mut-Regel — eine Regel für alles

> **Parameter sind Lese-Sichten. Wer schreiben will, deklariert `mut` — auf beiden Seiten.**

- Methoden: `fn dist_to(self, …)` liest nur; `fn reset(mut self)` darf schreiben.
- Funktionen: `fn parse(line: str)` kann nichts verändern; `fn fill(buf: mut strbuf[64])` darf füllen.
- **Aufrufseite ebenfalls ehrlich:** `fill(mut line)` — jeder Aufrufstelle sieht man an, dass verändert wird, ohne die Definition nachzuschlagen. Compiler prüft nur: `mut`-Argument verlangt `mut`-Variable (Rust-Anleihe **ohne** Borrow-Checker).
- **C-Übersetzung:** structs/strbufs immer als Zeiger übergeben (Kopieren wäre auf AVR teuer) — Lese-Parameter als `const*`, `mut`-Parameter als normaler Zeiger. Integer/`bool` werden schlicht kopiert wie in C.

### 7.3 Wertsemantik bei Zuweisung

- **`p2 := p1` kopiert alle Felder** (wie C-struct-Zuweisung). ⚠️ **Größte Abweichung vom Python-Gefühl:** In Python legt `p2 = p1` nur einen zweiten Namen fürs selbe Objekt an — bei uns entsteht eine echte Kopie.
- Angenehme Folge: Die Python-Unterscheidung `copy`/`deepcopy` **existiert nicht** — ohne verschachtelte Referenzen ist jede Kopie automatisch „tief". Keine Aliasing-Überraschungen.

---

## 8. Kontrollfluss & Funktionen

**Phase 1:**

- `if` / `elif` / `else`
- `while`, `loop` (Endlosschleife), `break`, `return`
- **kein** `for` (braucht Ranges/Arrays → Phase 2), **kein** `match` (braucht Enums → Phase 2)

**Beschlossen für Phase 2: `repeat`/`until`** (fußgesteuerte Schleife, Pascal-Erbe):

```
repeat:
    status := hal_read_status()
until status & READY_BIT <> 0
```

- Körper läuft mindestens einmal; **`until` ist eine Abbruchbedingung** (Schleife läuft, *bis* sie wahr ist — Pascal-Semantik, nicht Cs Fortsetzungsbedingung).
- Typischer Embedded-Fall: „lies Statusregister, bis Ready-Bit gesetzt" — erst lesen, dann prüfen.
- C-Übersetzung: `do { ... } while (!(bedingung));` — das `until` wird negiert.
- Parser-Hinweis: erste Konstruktion, bei der nach dem eingerückten Block noch eine Zeile zur Schleife gehört (`until` auf Ausgangsebene, analog zum `else` beim `if`).

**Funktionen:**

```
fn read_temp(channel: u8) -> i16:
    ...

fn main():
    ...
```

**FFI (Phase 1, essenziell):**

```
extern fn hal_led_write(on: bool)
extern fn hal_delay_ms(ms: u16)
```

Deklariert eine C-Funktion, die der Linker findet. Alles, was die Sprache (noch) nicht kann, lebt in einer handgeschriebenen C-HAL pro Target.

---

## 9. Aktoren & Nachrichten (beschlossen, Implementierung Phase 2)

**Zweck:** primär **Hardware-Abstraktion**. Ein Peripheriegerät ist ein Aktor (`Uart`, `Motor`, `Led`, `Display`); sein Nachrichten-Enum ist sein Interface. Die Implementierung dahinter darf pro Target verschieden sein (AVR: Register; Zephyr: API-Aufrufe) — der aufrufende Code bleibt identisch.

**Modell (die „statische Embedded-Variante"):**

1. **Alle Aktoren stehen zur Compile-Zeit fest.** Kein `spawn` zur Laufzeit. Jeder Aktor wird ein globales C-struct.
2. **Genau ein Nachrichten-Enum pro Aktor.** Der Compiler prüft statisch, dass nur passende Nachrichten gesendet werden. Mailbox-Speicher ist berechenbar: größte Variante × Queue-Tiefe.
3. **Mailbox mit fester, deklarierter Größe** — ein Ringpuffer-Array, kein Heap.
4. **Queue voll = definiertes, pro Aktor deklariertes Verhalten:** Nachricht verwerfen oder ältestes überschreiben (`drop_oldest` — nützlich bei „nur der neueste Sensorwert zählt"). Kein implizites Blockieren.
5. **Fire-and-forget.** Senden gibt nichts zurück, keine Futures (die kämen erst mit der Phase-3-Zustandsmaschinen-Transformation). Wer eine Antwort will, bekommt sie als eigene Nachricht zurück (Absender schickt seine „Adresse" mit — Erlang-Muster).
6. **Kein selektiver Empfang, keine Supervisor** (bewusst weggelassen: unberechenbare Queues bzw. setzt dynamische Aktoren voraus).
7. **Isolation:** Nur der Aktor selbst darf an seinen Zustand. Compiler erzwingt das (Aktoren als Sprachkonstrukt, nicht als Bibliotheksmuster).
8. **Scheduling:** AVR → Hauptschleife fragt reihum die Mailboxen ab (kooperativ); Zephyr → optional echter Thread + `k_msgq` pro Aktor. **Sprachcode identisch, nur der Transpiler-Unterbau unterscheidet sich.**

**Syntax:**

```
enum MotorMsg:
    Stop
    SetSpeed(rpm: u16)
    Move(x: i16, y: i16)

actor Motor:
    mailbox: 8 drop_oldest
    current_rpm: u16 := 0          # privater Zustand

    on msg: MotorMsg:
        match msg:
            Stop:
                self.current_rpm := 0
            SetSpeed(rpm):
                self.current_rpm := rpm
            Move(x, y):
                ...

# Senden — Statement, Pfeil zeigt zum Empfänger:
Stop ->> Motor
SetSpeed(120) ->> Motor
Move(x: -40, y: 200) ->> Motor    # benannte Argumente erlaubt (ab 2 Parametern sinnvoll)
```

- `SetSpeed(120)` ist **kein Funktionsaufruf**, sondern der Enum-Konstruktor — er baut einen Wert, der in die Mailbox kopiert wird.
- Datenlose Varianten ohne Klammern (`Stop`, nicht `Stop()`).
- **C-Übersetzung:** globales struct (Zustand) + Ringpuffer-Array (Mailbox) + Funktion mit `switch` (Handler); `->>` = „Element in Ringpuffer schreiben", auf AVR mit kurz gesperrten Interrupts → ISRs können sicher senden, ohne dass der Nutzer je über `volatile` nachdenkt.

**Bekannte Grenze:** Auf AVR (kooperativ, ein Stack) gilt „ein blockierender Handler blockiert alle". Handler müssen bis Phase 3 nicht-blockierend sein.

---

## 10. Vordefinierte Peripherie-Aktoren & Board-Konfiguration (beschlossen)

**Grundidee:** Die Sprache liefert pro Peripherietyp ein *Standard-Nachrichten-Enum* mit — das plattformneutrale „Datenblatt-Interface". Dahinter liegen pro Target zwei Implementierungen (AVR: direkte Registerzugriffe; Zephyr: API-Aufrufe). Anwendungscode ist auf beiden Targets identisch.

**Gestufter Ausbau — nicht jede Peripherie ist sofort ein guter Aktor:**

| Peripherie | Phase 2 | Phase 3 |
|---|---|---|
| **Timer** | vordefinierter Aktor: schickt periodisch `Tick` an einen konfigurierten Ziel-Aktor | — |
| **UART (Empfang)** | vordefinierter Aktor: schickt `ByteReceived(b)` | — |
| **I2C, SPI** | synchrone FFI-Schnittstelle (ehrlich blockierende Funktionen wie `i2c_write_read(...)`) | Beförderung zum Aktor, sobald Handler nicht-blockierend „warten" können |

Begründung der Stufung: Das Aktormodell glänzt bei *asynchron eintreffenden Ereignissen* (Ticks, Bytes, Tasten). Request/Response-Peripherie wie I2C/SPI würde im reinen Fire-and-forget-Modell zu umständlichem Nachrichten-Pingpong zerrissen — elegant wird das erst mit den Phase-3-Zustandsmaschinen.

**Damit beschlossen (vormals offene Frage Nr. 1):** Zeit und Interrupts kommen **als Nachrichten** ins System. Es gibt im Programm nur *einen* Mechanismus für „etwas ist passiert"; das Race-Condition-Problem zwischen ISR und Hauptschleife ist strukturell gelöst.

**Board-Konfigurationsdatei:** Pro Board existiert eine kleine deklarative Konfigurationsdatei: welche Peripherie-Instanzen es gibt (`I2c0`, `Uart1`, `Timer0` …) und worauf sie abbilden.

- **AVR:** Zuordnung zu Registern und Pins.
- **Zephyr:** Zuordnung zu Devicetree-Knoten; das Werkzeug generiert daraus die Aktor-Instanzen **und** das nötige Devicetree-Overlay. Devicetree verschwindet damit nicht (Zephyr braucht ihn), wird aber auf eine einmalige Board-Einrichtung reduziert, die der Anwendungsentwickler nie wieder anfasst — er sieht nur fertige Sprachobjekte wie `I2c0`.

Diese Datei **ist** zugleich die Antwort auf die notierte Idee „deklarative Hardware-Konfiguration".

---

## 11. Phasenplan

### Phase 1 — Der komplette Durchstich
*Ziel: vom eigenen Quelltext bis zur blinkenden LED auf echter AVR-Hardware. Lieber winzige Sprache, die durchgängig funktioniert, als eine halbe große.*

**Sprachumfang:** Integer-Typen + `bool` · `let` / `let mut` mit Inferenz · Arithmetik/Vergleiche/Logik/Bits · keine impliziten Casts, `as` · `if`/`elif`/`else`, `while`, `loop`, `break`, `return` · Funktionen · `extern fn` (FFI)

**Compiler (Python, CLI-Tool `marla compile blink.mar -o blink.c`):**
1. Lexer, handgeschrieben, mit INDENT/DEDENT-Stack
2. Parser, rekursiver Abstieg, handgeschrieben (bessere Fehlermeldungen, mehr Lerneffekt als Lark)
3. AST als Python-Dataclasses
4. Semantische Analyse: Symboltabelle/Scopes, Typprüfung, `mut`-Prüfung, „alle Pfade returnen"
5. C-Codegen: eine lesbare `.c`-Datei, `<stdint.h>`-Typen, `#line`-Direktiven

**Dazu von Anfang an:**
- **Gute Fehlermeldungen** (Datei, Zeile, Spalte, markierter Ausschnitt) — nachrüsten ist elend
- **Testsuite:** Programme, die kompilieren müssen + Programme, die mit *bestimmter* Fehlermeldung abgelehnt werden müssen
- **Keywords für später schon reservieren:** u. a. `struct`, `enum`, `match`, `actor`, `on`, `send`, `self`, `as`, `mod`, `is`, `repeat`, `until` sowie die Typnamen `str`, `strbuf`

**Drumherum:** nur AVR, ein Board (Arduino Uno / ATmega328P) · handgeschriebenes `hal_avr.c` · Makefile (marla → avr-gcc → avrdude). Zephyr explizit **nicht** in Phase 1.

**Definition of Done:** Blink-Programm blinkt auf echter Hardware · Testsuite grün · **zehn absichtlich falsche Programme liefern zehn verständliche Fehlermeldungen** (keine Python-Tracebacks).

**Referenzprogramm Phase 1:**

```
extern fn hal_led_write(on: bool)
extern fn hal_delay_ms(ms: u16)

fn main():
    let mut counter: u16 := 0
    loop:
        hal_led_write(true)
        hal_delay_ms(500)
        hal_led_write(false)
        hal_delay_ms(500)
        counter := counter + 1
        if counter = 100:
            counter := 0
        if counter mod 10 <> 0:
            hal_delay_ms(100)
```

### Phase 2 — Datenmodellierung & Aktoren
- structs mit Methoden und mut-Parameter-Regel (Abschnitt 7)
- Enums mit Daten, `match` (mit Vollständigkeitsprüfung!)
- `Result<T, E>` + `?`-Operator
- Arrays fester Größe, `for`
- **Strings:** `str` + `strbuf[N]` inkl. Tag-Bit-Mechanik auf AVR (Abschnitt 6)
- `repeat`/`until` (siehe Abschnitt 8)
- **Aktoren** wie in Abschnitt 9
- **Vordefinierte Peripherie-Aktoren:** Timer (`Tick`) und UART-Empfang (`ByteReceived`); I2C/SPI zunächst als synchrones FFI (siehe Abschnitt 10)
- **Board-Konfigurationsdatei** (Registerzuordnung / Devicetree-Generierung)
- **Zephyr als zweites Target** (CMake-Snippet + HAL-Header auf Zephyr-APIs; deckt versteckte AVR-Annahmen auf)

### Phase 3 — Zustandsmaschinen (die Krönung)
- Compiler-Transformation „Funktion → Zustandsmaschine" (das Protothread-Prinzip, aber sauber: der Compiler rettet automatisch die Locals, die eine Wartestelle überleben müssen, in ein Zustands-struct)
- Damit: blockierend *aussehende*, nicht-blockierende Aktor-Handler (`wait_ms(100)` mitten im Handler; die Hauptschleife läuft weiter)
- Auf Zephyr wahlweise echte Threads für dieselben Tasks — Sprachcode identisch
- **I2C/SPI werden zu vollwertigen Aktoren befördert** (Request/Response ohne Blockieren wird jetzt bequem)
- **f-Strings als Compile-Zeit-Zucker** (siehe 6.6)
- Anspruchsvollstes Compiler-Feature; Design-Vorleistung: Aktorzustand lebt ohnehin schon in einem struct, gerettete Locals passen dort hinein

---

## 12. Offene Fragen

1. ~~Timer & Interrupts als Nachrichten?~~ **Beschlossen: ja** — siehe Abschnitt 10 (Timer-Aktor schickt `Tick`, UART schickt `ByteReceived`).
2. **Benannte Argumente** bei Enum-Konstruktoren: erlaubt (aktueller Stand), aber erzwingen ab n Parametern?
3. **Antwort-Muster im Detail:** Wie sieht die „Absenderadresse" syntaktisch aus, wenn Aktor A eine Antwort von B will?
4. **PROGMEM-Automatik** auf AVR: Ab wann legt der Compiler Konstanten automatisch in den Flash? (Erst relevant, wenn es Arrays/Strings gibt.)
5. ~~Deklarative Hardware-Konfiguration~~ **Beschlossen: ja** — die Board-Konfigurationsdatei aus Abschnitt 10.
6. **Compiler-Warnung** bei `//`/`mod` auf vorzeichenbehafteten Typen — ja/nein.
7. ~~Name der Sprache~~ **Beschlossen: MARLA** („Messages And Registers — Language with Actors"). Dateiendung `.mar`, CLI-Kommando `marla`. Hübscher Zufall: das `ml_`-Codegen-Präfix (Anhang D) buchstabiert sich als **M**ar**L**a.
8. **Genaues Format der Board-Konfigurationsdatei** (eigene Syntax? TOML? Teil der Sprache?) und Umfang der Standard-Nachrichten-Enums pro Peripherietyp.
9. **Überlaufsemantik bei Arithmetik:** Für unsigned garantiert C sauberes Wrap-around; **signed-Überlauf ist in C undefiniertes Verhalten**. Beschlussbedarf (Kandidat: Codegen rechnet intern unsigned und castet zurück → definiertes Wrap auch für signed). Bis dahin: signed-Überlauf gilt als Programmfehler.

---

## 13. Nächste Schritte

1. ~~Phase-1-**Grammatik schriftlich fixieren**~~ **Erledigt — siehe Anhang A**
2. Implementierung nach dem **Meilenstein-Fahrplan in Anhang F** (M1–M7), Board: **Arduino Uno Rev3 / ATmega328P**
3. Architektur: Anhang B · Lexer: Anhang C · Codegen: Anhang D · Toolchain/HAL: Anhang E

---

## Anhang A: Phase-1-Grammatik (EBNF, beschlossen)

Notation nach Wirth: `{ }` = 0..n Wiederholungen, `[ ]` = optional, `|` = Alternative, `" "` = Terminal.
`NEWLINE`, `INDENT`, `DEDENT` sind Tokens des Lexers (Einrückungs-Stack); Leerzeilen und reine Kommentarzeilen erzeugen keine Tokens.

```ebnf
programm     = { toplevel } EOF .
toplevel     = extern_decl | fn_def .

extern_decl  = "extern" "fn" IDENT "(" [ paramliste ] ")" [ "->" typ ] NEWLINE .
fn_def       = "fn" IDENT "(" [ paramliste ] ")" [ "->" typ ] ":" block .
paramliste   = param { "," param } .
param        = IDENT ":" typ .
typ          = "u8" | "i8" | "u16" | "i16" | "u32" | "i32" | "bool" .

block        = NEWLINE INDENT anweisung { anweisung } DEDENT .

anweisung    = let_anw | zuw_oder_aufruf | if_anw | while_anw
             | loop_anw | break_anw | return_anw .

let_anw      = "let" [ "mut" ] IDENT [ ":" typ ] ":=" ausdruck NEWLINE .
zuw_oder_aufruf
             = IDENT ( zuw_rest | aufruf_rest ) NEWLINE .
zuw_rest     = ( ":=" | "+=" | "-=" | "*=" | "//=" | "&=" | "|=" | "^=" ) ausdruck .
aufruf_rest  = "(" [ argliste ] ")" .
argliste     = ausdruck { "," ausdruck } .

if_anw       = "if" ausdruck ":" block
               { "elif" ausdruck ":" block }
               [ "else" ":" block ] .
while_anw    = "while" ausdruck ":" block .
loop_anw     = "loop" ":" block .
break_anw    = "break" NEWLINE .
return_anw   = "return" [ ausdruck ] NEWLINE .

(* Ausdrücke: eine Regel pro Rangstufe, schwächste zuerst.       *)
(* Rangfolge wie Python; Korrektur gegenüber C: Bits binden      *)
(* STÄRKER als Vergleiche -> status & MASKE <> 0 tut das Gemeinte. *)

ausdruck     = und_ausdr { "or" und_ausdr } .
und_ausdr    = not_ausdr { "and" not_ausdr } .
not_ausdr    = "not" not_ausdr | vergleich .
vergleich    = bitor_ausdr [ vgl_op bitor_ausdr ] .        (* KEIN Chaining *)
vgl_op       = "=" | "<>" | "<" | "<=" | ">" | ">=" .
bitor_ausdr  = bitxor_ausdr { "|" bitxor_ausdr } .
bitxor_ausdr = bitand_ausdr { "^" bitand_ausdr } .
bitand_ausdr = shift_ausdr { "&" shift_ausdr } .
shift_ausdr  = add_ausdr { ( "<<" | ">>" ) add_ausdr } .
add_ausdr    = mul_ausdr { ( "+" | "-" ) mul_ausdr } .
mul_ausdr    = unaer { ( "*" | "//" | "mod" ) unaer } .
unaer        = "-" unaer | cast .
cast         = primaer { "as" typ } .
primaer      = ZAHL | "true" | "false"
             | IDENT [ "(" [ argliste ] ")" ]
             | "(" ausdruck ")" .
```

**Lexikalische Regeln:**

```ebnf
IDENT   = buchstabe { buchstabe | ziffer } .        (* buchstabe schließt _ ein *)
ZAHL    = dezimal | hex | binaer .
dezimal = ziffer { ziffer | "_" } .
hex     = "0x" hexziffer { hexziffer | "_" } .
binaer  = "0b" ( "0" | "1" ) { "0" | "1" | "_" } .   (* 0b0010_0000 für Masken *)
```

**Festlegungen, die die Grammatik trifft:**

1. **Rangfolge (schwächste → stärkste):** `or` · `and` · `not` · Vergleiche · `|` · `^` · `&` · `<<`/`>>` · `+`/`-` · `*`/`//`/`mod` · unäres `-` · `as` · Primär. Bewusste C-Korrektur: Bits über Vergleichen (wie Go).
2. **Kein Vergleichs-Chaining:** `0 <= x < 10` ist ein Compile-Fehler mit Hinweis auf `0 <= x and x < 10` (daher `[ ]` statt `{ }` in der `vergleich`-Regel).
3. **Zahlliterale sind typlos** und nehmen ihren Typ aus dem Kontext (Parameter, Zieltyp der Zuweisung). Ohne Kontext (`let x := 5`) verlangt der Compiler eine Annotation. Keine Suffixe (`5u8`) in Phase 1.
4. **Aufruf als Anweisung und als Ausdruck:** beides erlaubt; die gemeinsame Regel `zuw_oder_aufruf` löst die Mehrdeutigkeit nach dem IDENT mit einem Token Lookahead.
5. Kombinierte Zuweisungen inkl. Bit-Varianten (`&=`, `|=`, `^=`) — für Registerarbeit unverzichtbar; `<<=`/`>>=` bei Bedarf später.
6. Der Lexer wandelt Einrückung in `INDENT`/`DEDENT`; Tabs oder Spaces konsequent (Mischung = Fehler, wie Python 3).

---

## Anhang B: Compiler-Architektur & Projektstruktur

Der Compiler ist ein reines Python-Programm auf dem PC. Er liest `.mar`, schreibt `.c` — mehr nicht. Kompilieren und Flashen übernehmen avr-gcc und avrdude (Anhang E).

### B.1 Die Pipeline (mehrpassig, jeder Pass einzeln testbar)

```
blink.mar ──Lexer──▶ Tokens ──Parser──▶ AST ──Checker──▶ geprüfter AST ──Codegen──▶ blink.c
```

Anders als bei Wirths einpassigen Compilern ist jeder Schritt ein eigener Durchlauf mit klarer Schnittstelle. Vorteil: Man kann jeden Pass isoliert verstehen, testen und mit Claude Code einzeln bauen.

### B.2 Projektstruktur

```
marla/
├── marla/
│   ├── __init__.py
│   ├── tokens.py        # TokenKind (Enum) + Token (Dataclass mit Zeile/Spalte)
│   ├── lexer.py         # Quelltext -> Tokenliste, inkl. INDENT/DEDENT
│   ├── ast_nodes.py     # AST-Knoten als Dataclasses
│   ├── parser.py        # rekursiver Abstieg, 1 Funktion pro Grammatikregel
│   ├── checker.py       # Symboltabelle, Typprüfung, mut-Prüfung
│   ├── codegen.py       # AST -> C-Quelltext
│   ├── errors.py        # CompileError + hübsche Ausgabe mit Code-Ausschnitt
│   └── cli.py           # Kommandozeile: marla compile blink.mar -o blink.c
├── tests/
│   ├── test_lexer.py
│   ├── test_parser.py
│   ├── test_checker.py
│   ├── test_codegen.py
│   └── programs/        # ok/*.mar müssen kompilieren, err/*.mar müssen scheitern
├── runtime/
│   ├── hal_avr.h        # Schnittstelle der HAL (Anhang E)
│   ├── hal_avr.c        # Uno-Implementierung
│   └── hal_host.c       # PC-Attrappe für Host-Tests (printf statt LED)
└── examples/
    └── blink.mar
```

### B.3 AST als Dataclasses — das Rückgrat

Jede Grammatikregel bekommt einen Knoten. Alle Knoten tragen Zeile/Spalte (für Fehlermeldungen). Beispiele:

```python
@dataclass
class BinaryOp:
    op: str            # "+", "and", "=", ...
    left: "Expr"
    right: "Expr"
    line: int
    col: int
    typ: str | None = None   # füllt der Checker aus ("u16", "bool", ...)

@dataclass
class LetStmt:
    name: str
    mutable: bool
    declared_type: str | None   # None = Inferenz verlangt Kontext
    value: "Expr"
    line: int
    col: int
```

Der Parser baut die Knoten, der Checker füllt `typ` und meldet Fehler, der Codegen liest nur noch.

### B.4 Checker — was geprüft wird (Phase 1 komplett)

1. **Namen:** jede Verwendung ist deklariert; keine Doppel-Deklaration im selben Scope; Scopes = Funktion + jeder Block.
2. **Typen:** beide Operanden gleich (keine impliziten Casts!); Vergleiche liefern `bool`; `and/or/not` verlangen `bool`.
3. **Bedingungen sind `bool`:** `if x:` mit `x: u8` ist ein Fehler („meintest du `x <> 0`?") — konsequent zu „keine impliziten Umwandlungen".
4. **Literale:** bekommen den Typ aus dem Kontext; Wertebereich wird geprüft (`hal_x(300)` bei `u8`-Parameter = Fehler).
5. **mut:** Zuweisung nur an `let mut`; Parameter sind in Phase 1 immer nur lesbar (Integer/bool werden kopiert — die mut-Regel aus Abschnitt 7 wird erst mit structs/strbuf relevant).
6. **return:** Funktion mit Rückgabetyp muss auf allen Pfaden returnen; `return x` passt zum deklarierten Typ; Funktion ohne Rückgabetyp: nur nacktes `return`.
7. **break** nur innerhalb `while`/`loop`.
8. **main:** genau eine `fn main()` ohne Parameter und Rückgabetyp muss existieren.

---

## Anhang C: Token-Inventar & Lexer-Hinweise

### C.1 Vollständige Token-Liste Phase 1

| Kategorie | Tokens |
|---|---|
| Struktur | `NEWLINE`, `INDENT`, `DEDENT`, `EOF` |
| Keywords | `fn` `extern` `let` `mut` `if` `elif` `else` `while` `loop` `break` `return` `true` `false` `and` `or` `not` `mod` `as` |
| reserviert (Fehler „reserviert für später") | `struct` `enum` `match` `actor` `on` `send` `self` `is` `repeat` `until` `str` `strbuf` `const` `for` `in` `Ok` `Err` |
| Typnamen | `u8` `i8` `u16` `i16` `u32` `i32` `bool` |
| Literale | `ZAHL` (dezimal, `0x…`, `0b…`, `_` erlaubt), Wert als Python-int im Token |
| Namen | `IDENT` |
| 3 Zeichen | `->>` |
| 2 Zeichen | `->` `:=` `<>` `<=` `>=` `<<` `>>` `//` `+=` `-=` `*=` `//=` `&=` `\|=` `^=` `==` (nur für die Fehlermeldung!) |
| 1 Zeichen | `:` `,` `(` `)` `=` `<` `>` `+` `-` `*` `&` `\|` `^` `#`(startet Kommentar) |

**Längster Match zuerst:** beim Lesen von Symbolen immer erst 3-Zeichen-, dann 2-Zeichen-, dann 1-Zeichen-Kandidaten prüfen. `==` wird bewusst als eigenes Token erkannt, damit Parser/Checker die freundliche Meldung „meintest du `=`?" geben können — dasselbe Muster lohnt sich für `!=` (→ „meintest du `<>`?") und `%` (→ „meintest du `mod`?").

### C.2 INDENT/DEDENT-Algorithmus (das einzige Neue gegenüber Wirth)

Der Lexer hält einen **Stack von Einrückungstiefen**, Start `[0]`. Pro *logischer* Zeile (nicht leer, nicht nur Kommentar):

```
tiefe = Anzahl führender Leerzeichen
wenn tiefe >  Stack-Spitze:  push(tiefe); erzeuge INDENT
wenn tiefe <  Stack-Spitze:  solange Spitze > tiefe: pop(); erzeuge DEDENT
                             wenn Spitze != tiefe: FEHLER „Einrückung passt zu keiner offenen Ebene"
wenn tiefe == Stack-Spitze:  nichts
```

Danach die Zeile normal tokenisieren, am Zeilenende `NEWLINE`. Bei `EOF`: für jede noch offene Ebene ein `DEDENT` nachliefern, dann `EOF`.

Regeln: Leer- und Kommentarzeilen erzeugen **gar nichts** (auch kein NEWLINE). Tabs und Spaces mischen = Fehler (wie Python 3); intern am einfachsten: Tab am Zeilenanfang ist schlicht verboten, Meldung „bitte Leerzeichen verwenden".

**Test-Idee:** Der Lexer ist perfekt tabellengetrieben testbar — Eingabestring rein, erwartete Token-Kind-Liste raus. 20 solcher Fälle (inkl. der Fehlerfälle) und der Lexer ist wasserdicht.

---

## Anhang D: C-Codegen — die Abbildungstabelle

### D.1 Namens-Präfix (wichtig!)

**Alle Nutzer-Symbole bekommen im C das Präfix `ml_`.** Grund: Sonst kollidiert eine Funktion `main` mit Cs `main`, eine Variable `switch` mit dem C-Keyword, ein `abs` mit der libc. Das echte C-`main()` schreibt der Codegen selbst und ruft `ml_main()` auf. `extern fn`-Namen bleiben **unpräfixiert** (sie sollen ja die HAL-Funktionen treffen).

### D.2 Abbildung Sprachkonstrukt → C

| Sprache | C |
|---|---|
| `u8 i8 u16 i16 u32 i32` | `uint8_t int8_t uint16_t int16_t uint32_t int32_t` (`<stdint.h>`) |
| `bool`, `true`, `false` | `bool`, `true`, `false` (`<stdbool.h>`) |
| `let x: u16 := 5` | `uint16_t x = 5;` |
| `let mut x: u16 := 5` | `uint16_t x = 5;` (mut ist reine Compile-Zeit-Info) |
| `let x: u16 := 5` (nicht mut) | optional `const uint16_t x = 5;` — schöner, aber Zusatz-Feinschliff |
| `x := e` / `x += e` | `x = e;` / `x += e;` |
| `=` `<>` | `==` `!=` |
| `and` `or` `not` | `&&` `\|\|` `!` |
| `//` `mod` | `/` `%` (C-Rundung ist beschlossen — passt 1:1) |
| `x as u16` | `(uint16_t)(x)` |
| `if/elif/else` | `if / else if / else` mit `{}`-Blöcken |
| `while c:` | `while (c) { }` |
| `loop:` | `for (;;) { }` |
| `fn f(a: u8) -> u16:` | `static uint16_t ml_f(uint8_t a) { }` (+ Prototyp oben in der Datei) |
| `extern fn hal_x(ms: u16)` | `extern void hal_x(uint16_t ms);` |
| Kommentarzeile im Quelltext | darf der Codegen verwerfen |
| jede Anweisung | davor `#line <zeile> "blink.mar"` — GDB & gcc-Fehler zeigen auf die Sprache |

### D.3 Gerüst der erzeugten Datei

```c
/* erzeugt von marla aus blink.mar -- nicht von Hand editieren */
#include <stdint.h>
#include <stdbool.h>
#include "hal_avr.h"

/* Prototypen aller ml_-Funktionen */
static void ml_main(void);

/* ... Funktionsdefinitionen mit #line-Direktiven ... */

int main(void) {
    hal_init();          /* stellt die HAL bereit (Pins, Timer) */
    ml_main();
    for (;;) {}          /* falls ml_main je zurückkehrt */
}
```

**Klammern-Strategie:** Der Codegen darf großzügig Klammern um jeden Teilausdruck setzen (`((a)+(b))*(c)`). Nicht schön, aber garantiert korrekt — die Rangfolge hat *unser* Parser entschieden, C soll sie nur nicht umdeuten. Hübscher machen kann man später.

---

## Anhang E: Toolchain Arduino Uno Rev3

### E.1 Board-Fakten

ATmega328P · 16 MHz Quarz · 32 KB Flash · 2 KB RAM · Bord-LED an **PB5** (Arduino-Pin 13) · USB erscheint als serieller Port (Linux: `/dev/ttyACM0`, macOS: `/dev/cu.usbmodem…`, Windows: `COMx`) · Bootloader Optiboot, 115200 Baud.

### E.2 Die HAL (komplett für Phase 1)

`runtime/hal_avr.h`:
```c
#ifndef HAL_AVR_H
#define HAL_AVR_H
#include <stdint.h>
#include <stdbool.h>
void hal_init(void);
void hal_led_write(bool on);
void hal_delay_ms(uint16_t ms);
#endif
```

`runtime/hal_avr.c`:
```c
#include "hal_avr.h"
#include <avr/io.h>
#include <util/delay.h>

void hal_init(void)          { DDRB |= (1 << DDB5); }          /* PB5 = Ausgang */
void hal_led_write(bool on)  { if (on) PORTB |= (1 << PORTB5);
                               else    PORTB &= ~(1 << PORTB5); }
void hal_delay_ms(uint16_t ms) {
    while (ms--) { _delay_ms(1); }   /* _delay_ms braucht KONSTANTE -> 1er-Schleife */
}
```

Der `_delay_ms`-Kommentar ist eine klassische AVR-Falle: `util/delay.h` rechnet die Warteschleife zur *Compile-Zeit* aus dem Argument aus — mit einer Variablen als Argument wird der Code riesig oder falsch. Die Schleife über `_delay_ms(1)` ist das Standard-Idiom.

`runtime/hal_host.c` (für Tests auf dem PC — gleiche Schnittstelle, druckt statt zu blinken):
```c
#include "hal_avr.h"
#include <stdio.h>
void hal_init(void)            { }
void hal_led_write(bool on)    { printf("LED %s\n", on ? "AN" : "aus"); }
void hal_delay_ms(uint16_t ms) { printf("  warte %u ms\n", ms); }
```

### E.3 Werkzeuge installieren

```
Ubuntu/Debian:  sudo apt install gcc-avr avr-libc avrdude
macOS:          brew tap osx-cross/avr && brew install avr-gcc avrdude
Windows:        WinAVR oder die Toolchain aus der Arduino-IDE mitbenutzen
```

### E.4 Bauen & Flashen (die vier Kommandos, dann als Makefile)

```bash
python -m marla.cli compile examples/blink.mar -o build/blink.c

avr-gcc -mmcu=atmega328p -DF_CPU=16000000UL -Os -std=c99 \
        -Iruntime -o build/blink.elf build/blink.c runtime/hal_avr.c

avr-objcopy -O ihex -R .eeprom build/blink.elf build/blink.hex

avrdude -c arduino -p m328p -P /dev/ttyACM0 -b 115200 \
        -U flash:w:build/blink.hex:i
```

Zeile für Zeile: (1) unser Compiler erzeugt C. (2) avr-gcc kompiliert und linkt für den ATmega328P; `F_CPU` sagt der Delay-Rechnung die Quarzfrequenz, `-Os` optimiert auf Größe. (3) objcopy zieht aus der ELF-Datei das rohe Flash-Abbild im Intel-HEX-Format — das Format, das der Bootloader versteht. (4) avrdude redet über den seriellen Port mit dem Optiboot-Bootloader und brennt das HEX.

**Host-Variante** (ohne Board, für schnelle Runden):
```bash
gcc -std=c99 -Iruntime -o build/blink_host build/blink.c runtime/hal_host.c
./build/blink_host      # druckt LED AN / warte 500 ms / ...
```

Dass dasselbe generierte C auf PC **und** AVR kompiliert, ist nebenbei der Beweis, dass der Codegen wirklich portables C99 erzeugt — unser Zephyr-Versprechen für Phase 2.

### E.5 Größe im Blick behalten

```bash
avr-size build/blink.elf
```
zeigt Flash- (`text`+`data`) und RAM-Verbrauch (`data`+`bss`). Blink sollte deutlich unter 1 KB Flash liegen. Von Anfang an hinschauen — das ist das Frühwarnsystem gegen versteckte Kosten.

---

## Anhang F: Teststrategie & Meilenstein-Fahrplan

### F.1 Teststrategie

- **pytest** für Lexer/Parser/Checker: tabellengetrieben (Eingabe → erwartete Tokens / AST-Form / Fehlermeldung).
- **Programm-Tests:** `tests/programs/ok/*.mar` müssen fehlerfrei durchlaufen; `tests/programs/err/*.mar` tragen in Zeile 1 einen Kommentar mit dem erwarteten Fehlertext-Fragment (`# ERWARTET: meintest du ':='`) — der Testrunner prüft, dass genau dieser Fehler kommt. **Fehlermeldungen sind damit Teil der Testsuite.**
- **Codegen-Tests auf dem Host:** generiertes C mit gcc + `hal_host.c` bauen, Programm laufen lassen, stdout vergleichen. Kein Board nötig, sekundenschnell.
- Faustregel je Meilenstein: erst Tests mit Claude Code schreiben (dabei versteht man die Spezifikation), dann implementieren, bis grün.

### F.2 Meilensteine — je einer ist eine gute Claude-Code-Sitzung

| # | Meilenstein | fertig, wenn … |
|---|---|---|
| M1 | Projektgerüst + `tokens.py` + Lexer **ohne** Einrückung (Zahlen, Idents, Keywords, Symbole mit Längster-Match, Kommentare, Zeile/Spalte) | 15+ Lexer-Tests grün, inkl. `->>`/`->`-Abgrenzung und `0b0010_0000` |
| M2 | INDENT/DEDENT-Stack + Fehlerfälle (Tab, schiefe Ebene) | verschachtelte Blöcke tokenisieren korrekt; EOF liefert offene DEDENTs nach |
| M3 | AST-Dataclasses + Parser für **Ausdrücke** (die 11-Stufen-Kaskade aus Anhang A) | Rangfolge-Tests: `a & b <> 0` parst als `(a&b) <> 0`; Chaining-Verbot meldet den Hinweis |
| M4 | Parser für Anweisungen, Funktionen, `extern fn`, ganzes Programm | `examples/blink.mar` wird zum vollständigen AST |
| M5 | Checker komplett (Liste B.4) + `errors.py` mit Code-Ausschnitt-Ausgabe | alle `err/`-Programme liefern ihre erwartete Meldung; **darunter die zehn absichtlich falschen Programme aus der Definition of Done** |
| M6 | Codegen + CLI | Blink-C kompiliert auf dem **Host** und druckt die erwartete Sequenz |
| M7 | `hal_avr.c` + Makefile + Flashen | **LED auf dem Uno blinkt** — Phase 1 abgeschlossen 🎉 |
| M8 | Kür: Fehlermeldungen polieren, `avr-size` ins Makefile, README | — |

Reihenfolge-Tipp für die Arbeit mit Claude Code: pro Meilenstein zuerst dieses Dokument (v. a. Anhang A + den passenden Anhang) als Kontext geben, dann die Tests formulieren lassen und *lesen*, erst danach implementieren. So bleibt jeder Baustein verstanden statt nur vorhanden.

