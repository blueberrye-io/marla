"""Die Prelude: automatisch verfügbare, vordefinierte Typen (Abschnitt 11.3).

Feste Bewohner:
- StrbufError (Overflow) — Fehlertyp von strbuf.append (6.3)
- MailboxPolicy (Drop | DropOldest | Panic) — Aktor-Kopf (Abschnitt 9)
- TimerMsg (Tick) — Standard-Interface des Timer-Aktors (Abschnitt 10)
- UartMsg (ByteReceived(b: u8)) — Standard-Interface des UART-Empfangs

Dogfooding: die vordefinierten Enums benutzen die eigene Enum-Maschinerie.
"""

from __future__ import annotations

from . import ast_nodes as ast


def _variant(spec: str) -> ast.VariantDef:
    """'Tick' oder 'ByteReceived(b:u8)'"""
    if "(" not in spec:
        return ast.VariantDef(spec, [], 1, 1)
    name, rest = spec.split("(", 1)
    params = []
    for part in rest[:-1].split(","):
        pname, ptyp = part.split(":")
        params.append(ast.Param(pname.strip(), ptyp.strip(), 1, 1))
    return ast.VariantDef(name, params, 1, 1)


def _enum(name: str, *variants: str) -> ast.EnumDef:
    return ast.EnumDef(name, [_variant(v) for v in variants], 1, 1)


PRELUDE_ENUMS: list[ast.EnumDef] = [
    _enum("StrbufError", "Overflow"),
    _enum("MailboxPolicy", "Drop", "DropOldest", "Panic"),
    _enum("TimerMsg", "Tick"),
    _enum("UartMsg", "ByteReceived(b:u8)"),
]
