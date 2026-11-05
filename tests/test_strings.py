"""P2.6: str-Sichten, Literale, strbuf[N], Inhaltsvergleich (§6)."""

import pytest

from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

from test_codegen import run_on_host


def run(source):
    return check(parse(lex(source)))


def ok(source):
    run(source)


def bad(source, fragment):
    with pytest.raises(CompileError) as exc:
        run(source)
    assert fragment in exc.value.message, exc.value.message


# --- Lexer -------------------------------------------------------------------------

def test_string_literal_utf8():
    toks = lex('let s: str := "Grüße"')
    string_tok = next(t for t in toks if t.bytes_value is not None)
    assert string_tok.bytes_value == "Grüße".encode("utf-8")
    # ü und ß sind je 2 Bytes -> 7 (die Referenz §6.4 sagt fälschlich 6!)
    assert len(string_tok.bytes_value) == 7


def test_string_escapes():
    toks = lex(r'let s: str := "a\n\t\x41\\"')
    string_tok = next(t for t in toks if t.bytes_value is not None)
    assert string_tok.bytes_value == b"a\n\tA\\"


def test_byte_literal():
    toks = lex("let b: u8 := 'A'")
    byte_tok = next(t for t in toks if t.kind.name == "BYTE")
    assert byte_tok.value == 65


def test_byte_literal_escape():
    toks = lex(r"let b: u8 := '\n'")
    byte_tok = next(t for t in toks if t.kind.name == "BYTE")
    assert byte_tok.value == 10


def test_byte_literal_umlaut_fehler():
    with pytest.raises(CompileError) as exc:
        lex("let b: u8 := 'ü'")
    assert "2 Bytes in UTF-8" in exc.value.message


def test_string_nicht_geschlossen():
    with pytest.raises(CompileError) as exc:
        lex('let s: str := "offen')
    assert "nicht geschlossen" in exc.value.message


# --- Checker -----------------------------------------------------------------------

def test_str_deklaration_und_len():
    ok('fn main():\n'
       '    let s: str := "hallo"\n'
       '    let n := s.len()\n')


def test_byte_literal_ist_fest_u8():
    ok("fn main():\n    let b: u8 := 'A'\n")
    bad("fn main():\n    let x: u16 := 'A'\n", "erwartet 'u16', gefunden 'u8'")


def test_str_vergleich_inhalt():
    ok('fn main():\n'
       '    let a: str := "x"\n'
       '    if a = "x":\n'
       '        return\n')
    bad('fn main():\n'
        '    let a: str := "x"\n'
        '    if a < "y":\n'
        '        return\n', "nur '=' und '<>'")


def test_strbuf_deklaration_und_append():
    ok("fn f() -> Result[u16, StrbufError]:\n"
       "    let mut line: strbuf[64]\n"
       '    line.append("Temp: ")?\n'
       "    line.append_u16(23)?\n"
       "    return Ok(line.len())\n"
       "fn main():\n"
       "    let r := f()\n")


def test_strbuf_append_braucht_mut():
    bad("fn main():\n"
        "    let line: strbuf[8]\n"
        '    line.append("x")?\n',
        "nicht veränderlich")


def test_strbuf_als_str_sicht():
    ok("fn takes(s: str) -> u16:\n"
       "    return s.len()\n"
       "fn main():\n"
       "    let mut b: strbuf[16]\n"
       "    let n := takes(b)\n")     # Sicht-Regel 6.3


def test_strbuf_kopieren_verboten():
    bad("fn main():\n"
        "    let mut a: strbuf[8]\n"
        "    let b := a\n", "nicht am Stück kopiert")


def test_str_index_liest_u8():
    ok('fn main():\n'
       '    let s: str := "abc"\n'
       "    let b := s[1]\n"
       "    let ist_b := b = 'b'\n")


def test_str_index_schreiben_verboten():
    bad('fn main():\n'
        '    let mut s: str := "abc"\n'
        "    s[0] := 'x'\n", "unveränderliche Sicht")


def test_string_plus_verboten():
    bad('fn main():\n'
        '    let s: str := "a" + "b"\n', "append")


def test_strbuf_ohne_kapazitaet():
    bad("fn main():\n    let mut b: strbuf\n", "strbuf[N]")


def test_prelude_strbuferror_nutzbar():
    ok("fn main():\n"
       "    let mut b: strbuf[4]\n"
       '    match b.append("12345"):\n'
       "        Ok(n):\n"
       "            return\n"
       "        Err(e):\n"
       "            match e:\n"
       "                Overflow:\n"
       "                    return\n")


def test_prelude_nicht_neu_deklarierbar():
    bad("enum StrbufError:\n    X\nfn main():\n    return\n",
        "bereits deklariert")


# --- Codegen / Host ----------------------------------------------------------------

def test_literal_statics_und_view():
    c = compile_source('fn main():\n    let s: str := "hi"\n', "s.mar")
    assert "static const uint8_t ml__str0[] ML_PROGMEM = { 104, 105, 0 };" in c
    assert "ML_STR_LIT(ml__str0, 2)" in c   # Länge ohne das stille \0


def test_strings_laufen_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        '    let gruss: str := "Grüße"\n'
        "    hal_delay_ms(gruss.len())\n"          # 7 Bytes (ü/ß je 2)
        '    if gruss = "Grüße":\n'
        "        hal_delay_ms(1)\n"
        '    if gruss <> "Gruesse":\n'
        "        hal_delay_ms(2)\n"
        "    hal_delay_ms(gruss[0] as u16)\n"      # 'G' = 71
    ))
    assert out == "  warte 7 ms\n  warte 1 ms\n  warte 2 ms\n  warte 71 ms\n"


def test_strbuf_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn build(line: mut strbuf[32]) -> Result[u16, StrbufError]:\n"
        '    line.append("Temp: ")?\n'
        "    line.append_u16(23)?\n"
        "    return Ok(line.len())\n"
        "fn main():\n"
        "    let mut line: strbuf[32]\n"
        "    match build(mut line):\n"
        "        Ok(n):\n"
        "            hal_delay_ms(n)\n"            # "Temp: 23" = 8
        "        Err(e):\n"
        "            hal_delay_ms(900)\n"
        '    if line = "Temp: 23":\n'              # strbuf als str-Sicht
        "        hal_delay_ms(1)\n"
        "    line.clear()\n"
        "    hal_delay_ms(line.len())\n"           # 0
    ))
    assert out == "  warte 8 ms\n  warte 1 ms\n  warte 0 ms\n"


def test_strbuf_ueberlauf_ist_nie_still(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut b: strbuf[4]\n"
        '    match b.append("12345"):\n'
        "        Ok(n):\n"
        "            hal_delay_ms(n)\n"
        "        Err(e):\n"
        "            hal_delay_ms(500)\n"          # Überlauf -> Err
        "    hal_delay_ms(b.len())\n"              # Puffer unverändert: 0
    ))
    assert out == "  warte 500 ms\n  warte 0 ms\n"


def test_str_index_panic(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn f(s: str, i: u16):\n"
        "    hal_delay_ms(s[i] as u16)\n"
        "fn main():\n"
        '    f("ab", 1)\n'                         # 'b' = 98
        '    f("ab", 7)\n'                         # PANIC
    ))
    assert out == "  warte 98 ms\n"
    assert "PANIC" in run_on_host.last_stderr
