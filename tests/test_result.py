"""P2.5: Result[T,E] + ?-Operator (Fehler durchreichen)."""

import pytest

from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

from test_codegen import run_on_host


PARSE_FN = (
    "enum ParseError:\n"
    "    Empty\n"
    "    TooBig\n"
    "\n"
    "fn parse_digit(b: u8) -> Result[u16, ParseError]:\n"
    "    if b = 0:\n"
    "        return Err(Empty)\n"
    "    if b > 9:\n"
    "        return Err(TooBig)\n"
    "    return Ok((b as u16) * 10)\n"
    "\n"
)


def run(source):
    return check(parse(lex(source)))


def ok(source):
    run(source)


def bad(source, fragment):
    with pytest.raises(CompileError) as exc:
        run(source)
    assert fragment in exc.value.message, exc.value.message


# --- Checker -----------------------------------------------------------------------

def test_result_typ_und_konstruktoren():
    ok(PARSE_FN + "fn main():\n    let r := parse_digit(5)\n")


def test_ok_braucht_result_kontext():
    bad("fn main():\n    let x: u8 := Ok(5)\n", "wo ein Result erwartet wird")


def test_ok_ohne_kontext():
    bad("fn main():\n    let x := Ok(5)\n", "Typannotation")


def test_ok_wert_wird_gegen_t_geprueft():
    bad("enum E:\n    X\n"
        "fn f() -> Result[u8, E]:\n"
        "    return Ok(300)\n"
        "fn main():\n"
        "    let r := f()\n", "passt nicht in 'u8'")


def test_match_ueber_result():
    ok(PARSE_FN +
       "fn main():\n"
       "    let r := parse_digit(5)\n"
       "    match r:\n"
       "        Ok(v):\n"
       "            let x := v\n"
       "        Err(e):\n"
       "            return\n")


def test_match_result_braucht_beide_zweige():
    bad(PARSE_FN +
        "fn main():\n"
        "    let r := parse_digit(5)\n"
        "    match r:\n"
        "        Ok(v):\n"
        "            return\n",
        "es fehlt: Err")


def test_fragezeichen_reicht_fehler_durch():
    ok(PARSE_FN +
       "fn parse_two(a: u8, b: u8) -> Result[u16, ParseError]:\n"
       "    let x := parse_digit(a)?\n"
       "    let y := parse_digit(b)?\n"
       "    return Ok(x + y)\n"
       "fn main():\n"
       "    let r := parse_two(1, 2)\n")


def test_fragezeichen_braucht_result_funktion():
    bad(PARSE_FN +
        "fn main():\n"
        "    let x := parse_digit(1)?\n",
        "muss dafür 'Result[...]' zurückgeben")


def test_fragezeichen_fehlertyp_muss_passen():
    bad(PARSE_FN +
        "enum OtherError:\n"
        "    Nope\n"
        "fn g() -> Result[u16, OtherError]:\n"
        "    let x := parse_digit(1)?\n"
        "    return Ok(x)\n"
        "fn main():\n"
        "    return\n",
        "Fehlertyp passt nicht")


def test_fragezeichen_nur_an_der_wurzel():
    bad(PARSE_FN +
        "fn g() -> Result[u16, ParseError]:\n"
        "    let x := parse_digit(1)? + 1\n"
        "    return Ok(x)\n"
        "fn main():\n"
        "    return\n",
        "komplette rechte Seite")


def test_return_mit_fragezeichen_verboten():
    bad(PARSE_FN +
        "fn g() -> Result[u16, ParseError]:\n"
        "    return parse_digit(1)?\n"
        "fn main():\n"
        "    return\n",
        "'return f()' ")


def test_result_vergleich_verboten():
    bad(PARSE_FN +
        "fn main():\n"
        "    let a := parse_digit(1)\n"
        "    let b := parse_digit(2)\n"
        "    if a = b:\n"
        "        return\n",
        "für Result nicht definiert")


def test_result_in_extern_verboten():
    bad("enum E:\n    X\n"
        "extern fn f() -> Result[u8, E]\n"
        "fn main():\n    return\n",
        "extern")


def test_verschachtelte_results_verboten():
    bad("enum E:\n    X\n"
        "fn f() -> Result[Result[u8, E], E]:\n"
        "    return Err(X)\n"
        "fn main():\n    return\n",
        "verschachtelte")


# --- Codegen -----------------------------------------------------------------------

def test_result_typedef_und_mangling():
    c = compile_source(PARSE_FN + "fn main():\n    let r := parse_digit(1)\n",
                       "r.mar")
    assert "ml_Result__u16__ParseError" in c
    assert "bool is_ok;" in c


def test_result_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        + PARSE_FN +
        "fn report(r: Result[u16, ParseError]):\n"
        "    match r:\n"
        "        Ok(v):\n"
        "            hal_delay_ms(v)\n"
        "        Err(e):\n"
        "            match e:\n"
        "                Empty:\n"
        "                    hal_delay_ms(901)\n"
        "                TooBig:\n"
        "                    hal_delay_ms(902)\n"
        "fn main():\n"
        "    let a := parse_digit(7)\n"
        "    report(a)\n"                      # 70
        "    let b := parse_digit(0)\n"
        "    report(b)\n"                      # 901 (Empty)
        "    let c := parse_digit(99)\n"
        "    report(c)\n"                      # 902 (TooBig)
    ))
    assert out == "  warte 70 ms\n  warte 901 ms\n  warte 902 ms\n"


def test_fragezeichen_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        + PARSE_FN +
        "fn sum_two(a: u8, b: u8) -> Result[u16, ParseError]:\n"
        "    let x := parse_digit(a)?\n"
        "    let y := parse_digit(b)?\n"
        "    return Ok(x + y)\n"
        "fn main():\n"
        "    match sum_two(3, 4):\n"
        "        Ok(v):\n"
        "            hal_delay_ms(v)\n"        # 30+40 = 70
        "        Err(e):\n"
        "            hal_delay_ms(900)\n"
        "    match sum_two(3, 0):\n"
        "        Ok(v):\n"
        "            hal_delay_ms(v)\n"
        "        Err(e):\n"
        "            hal_delay_ms(900)\n"      # b=0 -> Err durchgereicht
    ))
    assert out == "  warte 70 ms\n  warte 900 ms\n"
