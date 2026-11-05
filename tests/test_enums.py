"""P2.3: Enums mit Daten + match mit Vollständigkeitsprüfung (§9-Syntax)."""

import pytest

from marla import ast_nodes as ast
from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

from test_codegen import run_on_host


MOTOR = (
    "enum MotorMsg:\n"
    "    Stop\n"
    "    SetSpeed(rpm: u16)\n"
    "    Move(x: i16, y: i16)\n"
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


# --- Parsen -------------------------------------------------------------------

def test_enum_parst():
    program = parse(lex(MOTOR + "fn main():\n    return\n"))
    e = program.toplevels[0]
    assert isinstance(e, ast.EnumDef)
    assert [v.name for v in e.variants] == ["Stop", "SetSpeed", "Move"]
    assert e.variants[0].params == []
    assert [p.name for p in e.variants[2].params] == ["x", "y"]


def test_datenlose_variante_mit_klammern_hinweis():
    with pytest.raises(CompileError) as exc:
        parse(lex("enum E:\n    A()\nfn main():\n    return\n"))
    assert "ohne Klammern" in exc.value.message


def test_match_parst():
    program = parse(lex(
        MOTOR +
        "fn f(msg: MotorMsg):\n"
        "    match msg:\n"
        "        Stop:\n"
        "            return\n"
        "        SetSpeed(rpm):\n"
        "            return\n"
        "        Move(x, y):\n"
        "            return\n"
        "fn main():\n"
        "    return\n"
    ))
    m = program.toplevels[1].body.statements[0]
    assert isinstance(m, ast.MatchStmt)
    assert [a.variant for a in m.arms] == ["Stop", "SetSpeed", "Move"]
    assert m.arms[2].bindings == ["x", "y"]


# --- Checker -------------------------------------------------------------------

FULL_MATCH = (
    "fn f(msg: MotorMsg) -> u16:\n"
    "    match msg:\n"
    "        Stop:\n"
    "            return 0\n"
    "        SetSpeed(rpm):\n"
    "            return rpm\n"
    "        Move(x, y):\n"
    "            return (x as u16) + (y as u16)\n"
    "fn main():\n"
    "    let s := f(Stop)\n"
)


def test_match_vollstaendig_ok():
    ok(MOTOR + FULL_MATCH)


def test_match_unvollstaendig():
    bad(MOTOR +
        "fn f(msg: MotorMsg):\n"
        "    match msg:\n"
        "        Stop:\n"
        "            return\n"
        "fn main():\n"
        "    return\n",
        "es fehlt: SetSpeed, Move")


def test_match_doppelte_variante():
    bad(MOTOR +
        "fn f(msg: MotorMsg):\n"
        "    match msg:\n"
        "        Stop:\n"
        "            return\n"
        "        Stop:\n"
        "            return\n"
        "        SetSpeed(rpm):\n"
        "            return\n"
        "        Move(x, y):\n"
        "            return\n"
        "fn main():\n"
        "    return\n",
        "doppelt")


def test_match_falsche_bindungszahl():
    bad(MOTOR +
        "fn f(msg: MotorMsg):\n"
        "    match msg:\n"
        "        Stop:\n"
        "            return\n"
        "        SetSpeed(a, b):\n"
        "            return\n"
        "        Move(x, y):\n"
        "            return\n"
        "fn main():\n"
        "    return\n",
        "1 Feld")


def test_match_datenlos_ohne_klammern():
    bad(MOTOR +
        "fn f(msg: MotorMsg):\n"
        "    match msg:\n"
        "        Stop(x):\n"
        "            return\n"
        "        SetSpeed(rpm):\n"
        "            return\n"
        "        Move(x, y):\n"
        "            return\n"
        "fn main():\n"
        "    return\n",
        "datenlos")


def test_match_auf_nicht_enum():
    bad("fn main():\n"
        "    let x: u8 := 1\n"
        "    match x:\n"
        "        A:\n"
        "            return\n",
        "braucht ein enum")


def test_konstruktor_typpruefung():
    bad(MOTOR + "fn main():\n    let m := SetSpeed(true)\n", "erwartet 'u16'")
    ok(MOTOR + "fn main():\n    let m := SetSpeed(120)\n")
    ok(MOTOR + "fn main():\n    let m := Move(x: -40, y: 200)\n")


def test_datenlose_variante_bare():
    ok(MOTOR + "fn main():\n    let m := Stop\n")
    bad(MOTOR + "fn main():\n    let m := Stop()\n", "ohne Klammern")


def test_daten_variante_braucht_argumente():
    bad(MOTOR + "fn main():\n    let m := SetSpeed\n", "trägt Daten")


def test_globale_eindeutigkeit_der_varianten():
    bad(MOTOR + "enum Other:\n    Stop\nfn main():\n    return\n",
        "global eindeutig")


def test_enum_vergleich_verboten():
    bad(MOTOR +
        "fn main():\n"
        "    let a := Stop\n"
        "    let b := Stop\n"
        "    if a = b:\n"
        "        return\n",
        "match")


def test_enum_feldzugriff_verboten():
    bad(MOTOR +
        "fn main():\n"
        "    let m := SetSpeed(5)\n"
        "    let r := m.rpm\n",
        "über 'match'")


def test_rekursives_enum_verboten():
    bad("enum E:\n    A(e: E)\nfn main():\n    return\n", "enthält sich selbst")


def test_match_zaehlt_als_return_pfad():
    ok(MOTOR + FULL_MATCH)  # f hat Rückgabetyp, alle Zweige returnen


# --- Codegen ------------------------------------------------------------------

def test_c_tagged_union():
    c = compile_source(MOTOR + FULL_MATCH, "m.mar")
    assert "enum { ml_MotorMsg_Stop, ml_MotorMsg_SetSpeed, ml_MotorMsg_Move };" in c
    assert "uint8_t tag;" in c
    assert "struct { uint16_t ml_rpm; } SetSpeed;" in c
    # match als if-Kette, nicht als switch (break-Semantik!)
    assert "switch" not in c
    assert ".tag == ml_MotorMsg_SetSpeed" in c


def test_enum_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        + MOTOR +
        "fn speed_of(msg: MotorMsg) -> u16:\n"
        "    match msg:\n"
        "        Stop:\n"
        "            return 0\n"
        "        SetSpeed(rpm):\n"
        "            return rpm\n"
        "        Move(x, y):\n"
        "            return (x as u16) + (y as u16)\n"
        "fn main():\n"
        "    hal_delay_ms(speed_of(Stop))\n"            # 0
        "    hal_delay_ms(speed_of(SetSpeed(120)))\n"   # 120
        "    let m := Move(x: 40, y: 200)\n"
        "    hal_delay_ms(speed_of(m))\n"               # 240
    ))
    assert out == "  warte 0 ms\n  warte 120 ms\n  warte 240 ms\n"


def test_break_in_match_bricht_die_schleife(tmp_path):
    # Der Grund für die if-Kette: break im match-Zweig gehört zur Schleife
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "enum Cmd:\n"
        "    Halt\n"
        "    Weiter\n"
        "fn main():\n"
        "    let mut i: u16 := 0\n"
        "    loop:\n"
        "        i += 1\n"
        "        let c := Halt\n"
        "        match c:\n"
        "            Halt:\n"
        "                if i >= 3:\n"
        "                    break\n"
        "            Weiter:\n"
        "                continue\n"
        "    hal_delay_ms(i)\n"                          # 3
    ))
    assert out == "  warte 3 ms\n"
