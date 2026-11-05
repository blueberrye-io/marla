"""Board-Konfigurationsdatei (Abschnitt 10; Format: TOML — offene Frage 9
pragmatisch entschieden, tomllib ist Python-Standard).

Beispiel boards/uno.toml:

    [board]
    name    = "Arduino Uno Rev3"
    mcu     = "atmega328p"
    f_cpu   = 16000000
    strings = "tagged"        # tagged | direct  (6.2)
    panic   = "halt"          # halt | reset     (7.5)

    [timer0]
    type      = "timer"
    period_ms = 100
    target    = "Blinker"     # Aktor mit fn receive(msg: TimerMsg)

    [uart0]
    type   = "uart"
    baud   = 9600
    target = "Echo"           # Aktor mit fn receive(msg: UartMsg)
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import ast_nodes as ast
from .errors import CompileError

PERIPHERAL_ENUMS = {"timer": "TimerMsg", "uart": "UartMsg"}


@dataclass
class Peripheral:
    name: str
    kind: str                  # "timer" | "uart"
    target: str                # Ziel-Aktor
    period_ms: int = 0         # timer
    baud: int = 0              # uart


@dataclass
class Board:
    name: str = "unbenannt"
    mcu: str = ""
    f_cpu: int = 16000000
    strings: str = "direct"    # direct | tagged
    panic: str = "halt"        # halt | reset
    peripherals: list[Peripheral] = field(default_factory=list)


def _err(message: str, filename: str) -> CompileError:
    return CompileError(message, 1, 1, filename)


def load_board(path: str | Path) -> Board:
    path = Path(path)
    filename = path.name
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _err(f"Board-Datei nicht lesbar: {exc}", filename) from exc
    except tomllib.TOMLDecodeError as exc:
        raise _err(f"ungültiges TOML: {exc}", filename) from exc

    board = Board()
    meta = data.pop("board", {})
    board.name = meta.get("name", board.name)
    board.mcu = meta.get("mcu", board.mcu)
    board.f_cpu = int(meta.get("f_cpu", board.f_cpu))
    board.strings = meta.get("strings", board.strings)
    board.panic = meta.get("panic", board.panic)
    if board.strings not in ("direct", "tagged"):
        raise _err("strings: erlaubt sind 'direct' und 'tagged' (6.2)", filename)
    if board.panic not in ("halt", "reset"):
        raise _err("panic: erlaubt sind 'halt' und 'reset' (7.5)", filename)

    for section, cfg in data.items():
        if not isinstance(cfg, dict) or "type" not in cfg:
            raise _err(f"[{section}]: 'type' fehlt (timer | uart)", filename)
        kind = cfg["type"]
        if kind not in PERIPHERAL_ENUMS:
            raise _err(
                f"[{section}]: unbekannter Typ '{kind}' — es gibt: "
                "timer, uart (I2C/SPI laufen in Phase 2 als synchrones "
                "FFI, Abschnitt 10)", filename)
        if "target" not in cfg:
            raise _err(f"[{section}]: 'target' (Ziel-Aktor) fehlt", filename)
        p = Peripheral(section, kind, cfg["target"])
        if kind == "timer":
            p.period_ms = int(cfg.get("period_ms", 0))
            if p.period_ms < 1:
                raise _err(f"[{section}]: period_ms >= 1 nötig", filename)
        else:
            p.baud = int(cfg.get("baud", 9600))
        board.peripherals.append(p)
    return board


def validate_against(board: Board, program: ast.Program, filename: str) -> None:
    """Ziel-Aktoren müssen existieren und das Standard-Enum empfangen."""
    actors = {t.name: t for t in program.toplevels
              if isinstance(t, ast.ActorDef)}
    for p in board.peripherals:
        actor = actors.get(p.target)
        if actor is None:
            raise _err(
                f"[{p.name}]: Ziel-Aktor '{p.target}' existiert nicht im "
                "Programm", filename)
        expected = PERIPHERAL_ENUMS[p.kind]
        if actor.msg_enum != expected:
            raise _err(
                f"[{p.name}]: Aktor '{p.target}' empfängt "
                f"'{actor.msg_enum}', ein {p.kind} schickt aber "
                f"'{expected}' (Abschnitt 10)", filename)


def board_header(board: Board) -> str:
    """Inhalt für marla_board.h — überschreibt den Default via Include-Pfad."""
    lines = [
        f"/* erzeugt von marla fuer Board: {board.name} -- nicht editieren */",
        "#ifndef MARLA_BOARD_H",
        "#define MARLA_BOARD_H",
    ]
    if board.strings == "tagged":
        lines.append("#define MARLA_STRINGS_TAGGED 1   /* PROGMEM + Tag-Bit (6.2) */")
    if board.panic == "reset":
        lines.append("#define MARLA_PANIC_RESET 1      /* Watchdog statt Halt (7.5) */")
    lines.append(f"#define MARLA_F_CPU {board.f_cpu}UL")
    lines.append("#endif")
    return "\n".join(lines) + "\n"
