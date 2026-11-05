"""P2.4: Arrays, for-Schleife, Bereichsprüfung + Panic (7.4/7.5)."""

import pytest

from marla import ast_nodes as ast
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


# --- Parsen / Typen ---------------------------------------------------------------

def test_array_deklaration_parst():
    program = parse(lex(
        "fn main():\n"
        "    let mut samples: u8[16]\n"
        "    let mut grid: u8[8][8]\n"
    ))
    s1, s2 = program.toplevels[0].body.statements
    assert s1.declared_type == "u8[16]" and s1.value is None
    assert s2.declared_type == "u8[8][8]"


def test_arraygroesse_aus_konstante():
    ok("const SIZE: u16 := 32\n"
       "fn main():\n"
       "    let mut buf: u8[SIZE]\n"
       "    buf[31] := 1\n")


def test_arraygroesse_unbekannte_konstante():
    bad("fn main():\n    let mut buf: u8[SIZE]\n", "keine Konstante")


def test_indexierung_parst():
    program = parse(lex("fn main():\n    samples[3] := samples[3] + 1\n"))
    assign = program.toplevels[0].body.statements[0]
    assert isinstance(assign.target, ast.IndexExpr)


# --- Checker -----------------------------------------------------------------------

def test_konstanter_index_wird_geprueft():
    ok("fn main():\n    let mut a: u8[4]\n    a[3] := 1\n")
    bad("fn main():\n    let mut a: u8[4]\n    a[4] := 1\n",
        "außerhalb des Bereichs 0..3")


def test_index_muss_zahl_sein():
    bad("fn main():\n"
        "    let mut a: u8[4]\n"
        "    a[true] := 1\n", "Zahlentyp")


def test_array_ganz_kopieren_verboten():
    bad("fn main():\n"
        "    let mut a: u8[4]\n"
        "    let b := a\n", "nicht am Stück kopiert")


def test_array_ganz_zuweisen_verboten():
    bad("fn main():\n"
        "    let mut a: u8[4]\n"
        "    let mut b: u8[4]\n"
        "    b := a\n", "nicht am Stück zugewiesen")


def test_array_schreiben_braucht_mut():
    bad("fn main():\n"
        "    let a: u8[4]\n"
        "    a[0] := 1\n", "nicht veränderlich")


def test_mehrdimensional():
    ok("fn main():\n"
       "    let mut grid: u8[2][3]\n"
       "    grid[1][2] := 7\n"
       "    let v := grid[0][0]\n")
    bad("fn main():\n"
        "    let mut grid: u8[2][3]\n"
        "    grid[1][3] := 7\n", "außerhalb des Bereichs 0..2")


def test_array_parameter_mit_mut_regel():
    src = ("fn fill(a: mut u8[4], v: u8):\n"
           "    for i in 0..4:\n"
           "        a[i] := v\n"
           "fn main():\n"
           "    let mut a: u8[4]\n"
           "    fill(mut a, 9)\n")
    ok(src)
    bad(src.replace("fill(mut a, 9)", "fill(a, 9)"), "muss ebenfalls 'mut'")


def test_for_bereichstypen():
    ok("fn main():\n"
       "    for i in 0..10:\n"
       "        let x := i\n")
    bad("fn main():\n"
        "    let e: u8 := 4\n"
        "    for i in (0 as u16)..e:\n"
        "        return\n", "Typkonflikt")


def test_for_variable_unveraenderlich():
    bad("fn main():\n"
        "    for i in 0..10:\n"
        "        i := 5\n", "nicht veränderlich")


def test_rueckgabe_von_arrays_verboten():
    bad("fn f() -> u8[4]:\n    return\nfn main():\n    return\n",
        "nicht zurückgegeben")


def test_array_felder_in_structs_noch_nicht():
    bad("struct S:\n    a: u8[4]\nfn main():\n    return\n", "kommen später")


# --- Codegen: Eliminierung & Panik ----------------------------------------------------

def test_eliminierung_konstanter_index():
    c = compile_source(
        "fn main():\n"
        "    let mut a: u8[4]\n"
        "    a[2] := 1\n",
        "a.mar")
    assert "ML_IDX" not in c            # konstanter Index: kein Laufzeit-Check
    assert "ml_a[((uint16_t)2)]" in c


def test_eliminierung_for_variable():
    c = compile_source(
        "fn main():\n"
        "    let mut a: u8[8]\n"
        "    for i in 0..8:\n"
        "        a[i] := 1\n",
        "a.mar")
    assert "ML_IDX" not in c            # 0..8 über u8[8]: bewiesen sicher


def test_laufzeitcheck_bleibt_wenn_unbewiesen():
    c = compile_source(
        "fn f(i: u16):\n"
        "    let mut a: u8[8]\n"
        "    a[i] := 1\n"
        "fn main():\n"
        "    f(3)\n",
        "a.mar")
    assert "ML_IDX" in c                # i unbekannt: Check bleibt


def test_arrays_laufen_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "const N: u16 := 5\n"
        "fn main():\n"
        "    let mut a: u16[N]\n"
        "    for i in 0..N:\n"
        "        a[i] := (i as u16) * 10\n"
        "    let mut sum: u16 := 0\n"
        "    for i in 0..N:\n"
        "        sum += a[i]\n"
        "    hal_delay_ms(sum)\n"          # 0+10+20+30+40 = 100
        "    hal_delay_ms(a[3])\n"         # 30
    ))
    assert out == "  warte 100 ms\n  warte 30 ms\n"


def test_nullinitialisierung(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut a: u16[4]\n"
        "    hal_delay_ms(a[2] + 1)\n"     # 0 + 1
    ))
    assert out == "  warte 1 ms\n"


def test_panic_bei_bereichsfehler(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn f(i: u16):\n"
        "    let mut a: u8[4]\n"
        "    a[i] := 1\n"
        "    hal_delay_ms(1)\n"
        "fn main():\n"
        "    f(2)\n"                       # ok
        "    f(9)\n"                       # PANIC
        "    hal_delay_ms(99)\n"           # wird nie erreicht
    ))
    assert out == "  warte 1 ms\n"         # genau einmal, dann Abbruch
    assert "PANIC" in run_on_host.last_stderr
    assert "prog.mar" in run_on_host.last_stderr  # Datei/Zeile auf dem Host


def test_break_und_continue_in_for(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut sum: u16 := 0\n"
        "    for i in 1..100:\n"
        "        if i mod 2 = 0:\n"
        "            continue\n"
        "        if i > 8:\n"
        "            break\n"
        "        sum += i as u16\n"        # 1+3+5+7 = 16
        "    hal_delay_ms(sum)\n"
    ))
    assert out == "  warte 16 ms\n"
