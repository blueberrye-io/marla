"""Tabellengetriebene Lexer-Tests: Eingabestring rein, erwartete Token-Kinds raus."""

import pytest

from marla.errors import CompileError
from marla.lexer import lex
from marla.tokens import TokenKind as K


def kinds(source):
    return [t.kind for t in lex(source)]


def numbers(source):
    return [t.value for t in lex(source) if t.kind is K.NUMBER]


CASES = [
    # (Quelltext, erwartete Kinds)
    ("let x := 5",
     [K.LET, K.IDENT, K.ASSIGN, K.NUMBER, K.NEWLINE, K.EOF]),
    ("let mut counter: u16 := 0",
     [K.LET, K.MUT, K.IDENT, K.COLON, K.TYPE, K.ASSIGN, K.NUMBER, K.NEWLINE, K.EOF]),
    # ->> / -> / - Abgrenzung (längster Match)
    ("a ->> b", [K.IDENT, K.SENDARROW, K.IDENT, K.NEWLINE, K.EOF]),
    ("a -> b", [K.IDENT, K.ARROW, K.IDENT, K.NEWLINE, K.EOF]),
    ("a - b", [K.IDENT, K.MINUS, K.IDENT, K.NEWLINE, K.EOF]),
    # //= vor // vor nichts
    ("x //= 2", [K.IDENT, K.SLASHSLASHEQ, K.NUMBER, K.NEWLINE, K.EOF]),
    ("a // b", [K.IDENT, K.SLASHSLASH, K.IDENT, K.NEWLINE, K.EOF]),
    # := vor :
    ("x := 1", [K.IDENT, K.ASSIGN, K.NUMBER, K.NEWLINE, K.EOF]),
    ("x : u8", [K.IDENT, K.COLON, K.TYPE, K.NEWLINE, K.EOF]),
    # Vergleichs- und Shift-Tokens
    ("a <> b <= c >= d << e >> f",
     [K.IDENT, K.NE, K.IDENT, K.LE, K.IDENT, K.GE, K.IDENT,
      K.LSHIFT, K.IDENT, K.RSHIFT, K.IDENT, K.NEWLINE, K.EOF]),
    # Fehlermeldungs-Tokens werden erkannt (Parser gibt den Hinweis)
    ("a == b", [K.IDENT, K.EQEQ, K.IDENT, K.NEWLINE, K.EOF]),
    ("a != b", [K.IDENT, K.BANGEQ, K.IDENT, K.NEWLINE, K.EOF]),
    ("a % b", [K.IDENT, K.PERCENT, K.IDENT, K.NEWLINE, K.EOF]),
    # Keywords vs. Idents
    ("iffy elsewhere", [K.IDENT, K.IDENT, K.NEWLINE, K.EOF]),
    ("if not true and false or x",
     [K.IF, K.NOT, K.TRUE, K.AND, K.FALSE, K.OR, K.IDENT, K.NEWLINE, K.EOF]),
    ("x mod y as u8",
     [K.IDENT, K.MOD, K.IDENT, K.AS, K.TYPE, K.NEWLINE, K.EOF]),
    # Kommentare und Leerzeilen erzeugen nichts
    ("# nur ein Kommentar", [K.EOF]),
    ("", [K.EOF]),
    ("x := 1  # Kommentar am Ende", [K.IDENT, K.ASSIGN, K.NUMBER, K.NEWLINE, K.EOF]),
]


@pytest.mark.parametrize("source,expected", CASES, ids=[c[0][:30] or "leer" for c in CASES])
def test_kinds(source, expected):
    assert kinds(source) == expected


def test_zahlformate():
    assert numbers("5 1_000 0xFF 0xFF_FF 0b0010_0000 0b1") == [5, 1000, 255, 65535, 32, 1]


def test_indent_dedent_einfach():
    src = "fn main():\n    x := 1\n"
    assert kinds(src) == [
        K.FN, K.IDENT, K.LPAREN, K.RPAREN, K.COLON, K.NEWLINE,
        K.INDENT, K.IDENT, K.ASSIGN, K.NUMBER, K.NEWLINE, K.DEDENT, K.EOF,
    ]


def test_indent_verschachtelt_eof_liefert_dedents_nach():
    src = "fn main():\n    loop:\n        break"
    ks = kinds(src)
    assert ks.count(K.INDENT) == 2
    assert ks.count(K.DEDENT) == 2
    assert ks[-1] is K.EOF
    # beide DEDENTs kommen direkt vor EOF
    assert ks[-3:] == [K.DEDENT, K.DEDENT, K.EOF]


def test_dedent_zurueck_auf_mittlere_ebene():
    src = ("fn main():\n"
           "    if x:\n"
           "        y := 1\n"
           "    z := 2\n")
    ks = kinds(src)
    assert ks.count(K.INDENT) == 2
    assert ks.count(K.DEDENT) == 2


def test_leerzeilen_und_kommentare_im_block_neutral():
    src = ("fn main():\n"
           "    x := 1\n"
           "\n"
           "    # Kommentar\n"
           "    y := 2\n")
    ks = kinds(src)
    assert ks.count(K.INDENT) == 1
    assert ks.count(K.DEDENT) == 1


def expect_error(source, fragment):
    with pytest.raises(CompileError) as exc:
        lex(source)
    assert fragment in exc.value.message


def test_tab_am_zeilenanfang():
    expect_error("fn main():\n\tx := 1\n", "Leerzeichen")


def test_einrueckung_passt_zu_keiner_ebene():
    expect_error("fn main():\n        x := 1\n    y := 2\n", "keiner offenen Ebene")


def test_reservierte_woerter():
    for word in ("case", "task", "await", "yield", "import", "trait",
                 "u64", "f32", "Option", "None"):
        expect_error(word, "reserviert")


def test_const_und_continue_sind_keywords():
    assert kinds("const X: u8 := 1") == [
        K.CONST, K.IDENT, K.COLON, K.TYPE, K.ASSIGN, K.NUMBER, K.NEWLINE, K.EOF]
    assert kinds("continue") == [K.CONTINUE, K.NEWLINE, K.EOF]


def test_on_und_send_sind_normale_bezeichner():
    # Rev. 2: Reservierungs-Politik — Alltagswörter bleiben frei
    assert kinds("on send off state value msg timer") == [K.IDENT] * 7 + [K.NEWLINE, K.EOF]


def test_attribut_sigil_reserviert():
    expect_error("@inline", "Compiler-Attribute")


def test_hex_ohne_ziffern():
    expect_error("x := 0x", "Hex")


def test_zahl_mit_buchstaben():
    expect_error("x := 5abc", "ungültige Zahl")


def test_unbekanntes_zeichen():
    expect_error("x := 1;", "unerwartetes Zeichen")


def test_string_literal_lext():
    ks = kinds('x := "hallo"')
    assert K.STRING in ks


def test_unbekanntes_escape():
    expect_error(r'x := "\q"', "unbekanntes Escape")


def test_zeile_spalte():
    toks = lex("fn main():\n    let x := 5\n")
    let_tok = next(t for t in toks if t.kind is K.LET)
    assert (let_tok.line, let_tok.col) == (2, 5)
