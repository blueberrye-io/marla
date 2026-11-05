"""TokenKind (Enum) + Token (Dataclass mit Zeile/Spalte). Inventar siehe Anhang C.1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokenKind(Enum):
    # Struktur
    NEWLINE = auto()
    INDENT = auto()
    DEDENT = auto()
    EOF = auto()

    # Literale und Namen
    NUMBER = auto()   # Wert als Python-int im Token
    BYTE = auto()     # 'A' -> u8-Literal (Wert als int)
    STRING = auto()   # "..." -> UTF-8-Bytes (Wert als bytes in .bytes_value)
    IDENT = auto()
    TYPE = auto()     # u8 i8 u16 i16 u32 i32 bool str strbuf (Name in .text)

    # Keywords
    FN = auto()
    EXTERN = auto()
    LET = auto()
    MUT = auto()
    CONST = auto()
    CONTINUE = auto()
    IF = auto()
    ELIF = auto()
    ELSE = auto()
    WHILE = auto()
    LOOP = auto()
    BREAK = auto()
    RETURN = auto()
    TRUE = auto()
    FALSE = auto()
    AND = auto()
    OR = auto()
    NOT = auto()
    MOD = auto()
    AS = auto()
    REPEAT = auto()
    UNTIL = auto()
    STRUCT = auto()
    SELF = auto()
    ENUM = auto()
    MATCH = auto()
    FOR = auto()
    IN = auto()
    ACTOR = auto()

    # 3 Zeichen
    SENDARROW = auto()      # ->>
    SLASHSLASHEQ = auto()   # //=

    # 2 Zeichen
    ARROW = auto()          # ->
    ASSIGN = auto()         # :=
    DOTDOT = auto()         # ..  (Bereich in for)
    NE = auto()             # <>
    LE = auto()             # <=
    GE = auto()             # >=
    LSHIFT = auto()         # <<
    RSHIFT = auto()         # >>
    SLASHSLASH = auto()     # //
    PLUSEQ = auto()         # +=
    MINUSEQ = auto()        # -=
    STAREQ = auto()         # *=
    AMPEQ = auto()          # &=
    PIPEEQ = auto()         # |=
    CARETEQ = auto()        # ^=

    # nur für freundliche Fehlermeldungen erkannt
    EQEQ = auto()           # ==  -> "meintest du '='?"
    BANGEQ = auto()         # !=  -> "meintest du '<>'?"
    PERCENT = auto()        # %   -> "meintest du 'mod'?"

    # 1 Zeichen
    COLON = auto()
    COMMA = auto()
    DOT = auto()
    LBRACKET = auto()
    RBRACKET = auto()
    QUESTION = auto()       # ?  (Fehler durchreichen, §4)
    LPAREN = auto()
    RPAREN = auto()
    EQ = auto()             # =
    LT = auto()
    GT = auto()
    PLUS = auto()
    MINUS = auto()
    STAR = auto()
    AMP = auto()
    PIPE = auto()
    CARET = auto()


@dataclass
class Token:
    kind: TokenKind
    text: str
    line: int
    col: int
    value: int | None = None            # bei NUMBER und BYTE
    bytes_value: bytes | None = None    # bei STRING


KEYWORDS: dict[str, TokenKind] = {
    "fn": TokenKind.FN,
    "extern": TokenKind.EXTERN,
    "let": TokenKind.LET,
    "mut": TokenKind.MUT,
    "const": TokenKind.CONST,
    "continue": TokenKind.CONTINUE,
    "if": TokenKind.IF,
    "elif": TokenKind.ELIF,
    "else": TokenKind.ELSE,
    "while": TokenKind.WHILE,
    "loop": TokenKind.LOOP,
    "break": TokenKind.BREAK,
    "return": TokenKind.RETURN,
    "true": TokenKind.TRUE,
    "false": TokenKind.FALSE,
    "and": TokenKind.AND,
    "or": TokenKind.OR,
    "not": TokenKind.NOT,
    "mod": TokenKind.MOD,
    "as": TokenKind.AS,
    "repeat": TokenKind.REPEAT,
    "until": TokenKind.UNTIL,
    "struct": TokenKind.STRUCT,
    "self": TokenKind.SELF,
    "enum": TokenKind.ENUM,
    "match": TokenKind.MATCH,
    "for": TokenKind.FOR,
    "in": TokenKind.IN,
    "actor": TokenKind.ACTOR,
}

# Fehler "reserviert für später" (Anhang C.1, Revision 2).
# Bewusst NICHT reserviert (Reservierungs-Politik Rev. 2): Alltagswörter wie
# on, off, state, value, count, send, msg, timer, ... bleiben Bezeichner.
RESERVED: frozenset[str] = frozenset({
    # geplante Features (Phase 2/3)
    "case", "is",
    "task", "await", "yield",
    # Zukunftsvorrat (Result/Ok/Err sind seit P2.5 aktiv)
    "import", "pub", "type", "trait", "assert", "xor", "unsafe", "where",
    "u64", "i64", "f32", "f64",
    "Option", "Some", "None",
})

TYPE_NAMES: frozenset[str] = frozenset({
    "u8", "i8", "u16", "i16", "u32", "i32", "bool", "str", "strbuf",
})
