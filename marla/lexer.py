"""Quelltext -> Tokenliste, inkl. INDENT/DEDENT über einen Einrückungs-Stack (Anhang C.2)."""

from __future__ import annotations

from .errors import CompileError
from .tokens import KEYWORDS, RESERVED, TYPE_NAMES, Token, TokenKind

# Längster Match zuerst: erst 3-, dann 2-, dann 1-Zeichen-Kandidaten (Anhang C.1).
SYMBOLS3: dict[str, TokenKind] = {
    "->>": TokenKind.SENDARROW,
    "//=": TokenKind.SLASHSLASHEQ,
}

SYMBOLS2: dict[str, TokenKind] = {
    "->": TokenKind.ARROW,
    ":=": TokenKind.ASSIGN,
    "..": TokenKind.DOTDOT,
    "<>": TokenKind.NE,
    "<=": TokenKind.LE,
    ">=": TokenKind.GE,
    "<<": TokenKind.LSHIFT,
    ">>": TokenKind.RSHIFT,
    "//": TokenKind.SLASHSLASH,
    "+=": TokenKind.PLUSEQ,
    "-=": TokenKind.MINUSEQ,
    "*=": TokenKind.STAREQ,
    "&=": TokenKind.AMPEQ,
    "|=": TokenKind.PIPEEQ,
    "^=": TokenKind.CARETEQ,
    "==": TokenKind.EQEQ,
    "!=": TokenKind.BANGEQ,
}

SYMBOLS1: dict[str, TokenKind] = {
    ":": TokenKind.COLON,
    ",": TokenKind.COMMA,
    ".": TokenKind.DOT,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    "?": TokenKind.QUESTION,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "=": TokenKind.EQ,
    "<": TokenKind.LT,
    ">": TokenKind.GT,
    "+": TokenKind.PLUS,
    "-": TokenKind.MINUS,
    "*": TokenKind.STAR,
    "&": TokenKind.AMP,
    "|": TokenKind.PIPE,
    "^": TokenKind.CARET,
    "%": TokenKind.PERCENT,
}

HEX_DIGITS = set("0123456789abcdefABCDEF")


def _is_ident_start(c: str) -> bool:
    return c.isalpha() or c == "_"


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c == "_"


class Lexer:
    def __init__(self, source: str, filename: str = "<eingabe>"):
        self.source = source
        self.filename = filename
        self.tokens: list[Token] = []
        self.indent_stack = [0]

    def error(self, message: str, line: int, col: int) -> CompileError:
        return CompileError(message, line, col, self.filename)

    def lex(self) -> list[Token]:
        lines = self.source.splitlines()
        last_line = len(lines) + 1
        for lineno, raw in enumerate(lines, start=1):
            self._lex_line(raw, lineno)
        # EOF: für jede noch offene Ebene ein DEDENT nachliefern, dann EOF.
        while self.indent_stack[-1] > 0:
            self.indent_stack.pop()
            self.tokens.append(Token(TokenKind.DEDENT, "", last_line, 1))
        self.tokens.append(Token(TokenKind.EOF, "", last_line, 1))
        return self.tokens

    def _lex_line(self, raw: str, lineno: int) -> None:
        # Führende Leerzeichen zählen; Tab am Zeilenanfang ist verboten.
        i = 0
        while i < len(raw) and raw[i] in " \t":
            if raw[i] == "\t":
                raise self.error(
                    "Tabulator am Zeilenanfang — bitte Leerzeichen verwenden", lineno, i + 1
                )
            i += 1
        rest = raw[i:]
        # Leer- und Kommentarzeilen erzeugen gar nichts (auch kein NEWLINE).
        if rest == "" or rest.lstrip() == "" or rest.startswith("#"):
            return

        depth = i
        top = self.indent_stack[-1]
        if depth > top:
            self.indent_stack.append(depth)
            self.tokens.append(Token(TokenKind.INDENT, "", lineno, 1))
        elif depth < top:
            while self.indent_stack[-1] > depth:
                self.indent_stack.pop()
                self.tokens.append(Token(TokenKind.DEDENT, "", lineno, 1))
            if self.indent_stack[-1] != depth:
                raise self.error(
                    "Einrückung passt zu keiner offenen Ebene", lineno, depth + 1
                )

        self._lex_rest(raw, lineno, i)
        self.tokens.append(Token(TokenKind.NEWLINE, "", lineno, len(raw) + 1))

    def _lex_rest(self, line: str, lineno: int, start: int) -> None:
        i = start
        n = len(line)
        while i < n:
            c = line[i]
            col = i + 1
            if c in " \t":
                i += 1
                continue
            if c == "#":
                return  # Kommentar bis Zeilenende
            if line[i : i + 3] in SYMBOLS3:
                text = line[i : i + 3]
                self.tokens.append(Token(SYMBOLS3[text], text, lineno, col))
                i += 3
                continue
            if line[i : i + 2] in SYMBOLS2:
                text = line[i : i + 2]
                self.tokens.append(Token(SYMBOLS2[text], text, lineno, col))
                i += 2
                continue
            if c in SYMBOLS1:
                self.tokens.append(Token(SYMBOLS1[c], c, lineno, col))
                i += 1
                continue
            if c.isdigit():
                i = self._lex_number(line, lineno, i)
                continue
            if _is_ident_start(c):
                i = self._lex_word(line, lineno, i)
                continue
            if c == '"':
                i = self._lex_string(line, lineno, i)
                continue
            if c == "'":
                i = self._lex_byte(line, lineno, i)
                continue
            if c == "@":
                raise self.error(
                    "'@' ist für Compiler-Attribute reserviert (z. B. @inline) "
                    "— kommt in einer späteren Phase", lineno, col,
                )
            raise self.error(f"unerwartetes Zeichen {c!r}", lineno, col)

    def _lex_number(self, line: str, lineno: int, start: int) -> int:
        i = start
        col = start + 1
        n = len(line)
        if line.startswith("0x", i):
            i += 2
            digits_start = i
            while i < n and (line[i] in HEX_DIGITS or line[i] == "_"):
                i += 1
            digits = line[digits_start:i].replace("_", "")
            if not digits:
                raise self.error("ungültige Zahl: Hex-Ziffern erwartet nach '0x'", lineno, col)
            value = int(digits, 16)
        elif line.startswith("0b", i):
            i += 2
            digits_start = i
            while i < n and line[i] in "01_":
                i += 1
            digits = line[digits_start:i].replace("_", "")
            if not digits:
                raise self.error("ungültige Zahl: Binärziffern erwartet nach '0b'", lineno, col)
            value = int(digits, 2)
        else:
            while i < n and (line[i].isdigit() or line[i] == "_"):
                i += 1
            value = int(line[start:i].replace("_", ""))
        if i < n and _is_ident_start(line[i]):
            raise self.error(f"ungültige Zahl: {line[start:i + 1]!r}...", lineno, col)
        if i < n and line[i] == "." and i + 1 < n and line[i + 1].isdigit():
            raise self.error(
                "Kommazahlen gibt es (noch) nicht — Floats sind für eine "
                "spätere Phase reserviert", lineno, col)
        self.tokens.append(Token(TokenKind.NUMBER, line[start:i], lineno, col, value=value))
        return i

    ESCAPES = {"n": 10, "r": 13, "t": 9, "0": 0, "\\": 92, '"': 34, "'": 39}

    def _lex_escape(self, line: str, lineno: int, i: int) -> tuple[int, int]:
        """Liest die Escape-Sequenz nach '\\'; liefert (Bytewert, neue Pos)."""
        if i >= len(line):
            raise self.error("Escape am Zeilenende", lineno, i)
        c = line[i]
        if c in self.ESCAPES:
            return self.ESCAPES[c], i + 1
        if c == "x":
            hex_part = line[i + 1: i + 3]
            if len(hex_part) == 2 and all(h in HEX_DIGITS for h in hex_part):
                return int(hex_part, 16), i + 3
            raise self.error(
                "nach '\\x' kommen zwei Hex-Ziffern (z. B. '\\xFF')", lineno, i)
        raise self.error(
            f"unbekanntes Escape '\\{c}' — erlaubt: \\n \\r \\t \\0 \\\\ \\xFF",
            lineno, i)

    def _lex_string(self, line: str, lineno: int, start: int) -> int:
        i = start + 1
        col = start + 1
        out = bytearray()
        while True:
            if i >= len(line):
                raise self.error(
                    "String-Literal nicht geschlossen (\" fehlt)", lineno, col)
            c = line[i]
            if c == '"':
                i += 1
                break
            if c == "\\":
                value, i = self._lex_escape(line, lineno, i + 1)
                out.append(value)
            else:
                out.extend(c.encode("utf-8"))  # Literale sind UTF-8-kodiert (§6.4)
                i += 1
        self.tokens.append(Token(TokenKind.STRING, line[start:i], lineno, col,
                                 bytes_value=bytes(out)))
        return i

    def _lex_byte(self, line: str, lineno: int, start: int) -> int:
        i = start + 1
        col = start + 1
        if i >= len(line):
            raise self.error("Byte-Literal nicht geschlossen (' fehlt)", lineno, col)
        if line[i] == "\\":
            value, i = self._lex_escape(line, lineno, i + 1)
        else:
            encoded = line[i].encode("utf-8")
            if len(encoded) != 1:
                raise self.error(
                    f"'{line[i]}' sind {len(encoded)} Bytes in UTF-8 — "
                    "Byte-Literale erlauben nur ASCII", lineno, col)
            value = encoded[0]
            i += 1
        if i >= len(line) or line[i] != "'":
            raise self.error("Byte-Literal nicht geschlossen (' fehlt)", lineno, col)
        self.tokens.append(Token(TokenKind.BYTE, line[start:i + 1], lineno, col,
                                 value=value))
        return i + 1

    def _lex_word(self, line: str, lineno: int, start: int) -> int:
        i = start
        n = len(line)
        while i < n and _is_ident_char(line[i]):
            i += 1
        word = line[start:i]
        col = start + 1
        if word in RESERVED:
            raise self.error(
                f"'{word}' ist für eine spätere Phase reserviert", lineno, col
            )
        if word in TYPE_NAMES:
            self.tokens.append(Token(TokenKind.TYPE, word, lineno, col))
        elif word in KEYWORDS:
            self.tokens.append(Token(KEYWORDS[word], word, lineno, col))
        else:
            self.tokens.append(Token(TokenKind.IDENT, word, lineno, col))
        return i


def lex(source: str, filename: str = "<eingabe>") -> list[Token]:
    return Lexer(source, filename).lex()
