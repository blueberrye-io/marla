"""P2.2: structs mit Methoden und der mut-Regel (§7)."""

import pytest

from marla import ast_nodes as ast
from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

from test_codegen import run_on_host  # Host-Roundtrip-Helfer


POINT = (
    "struct Point:\n"
    "    x: i16\n"
    "    y: i16 := 0\n"
    "\n"
    "    fn manhattan(self) -> u16:\n"
    "        let mut ax := self.x\n"
    "        if ax < 0:\n"
    "            ax := 0 - ax\n"
    "        let mut ay := self.y\n"
    "        if ay < 0:\n"
    "            ay := 0 - ay\n"
    "        return (ax as u16) + (ay as u16)\n"
    "\n"
    "    fn reset(mut self):\n"
    "        self.x := 0\n"
    "        self.y := 0\n"
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


# --- Parsen ------------------------------------------------------------------

def test_struct_parst():
    program = parse(lex(POINT + "fn main():\n    return\n"))
    s = program.toplevels[0]
    assert isinstance(s, ast.StructDef)
    assert [f.name for f in s.fields] == ["x", "y"]
    assert s.fields[1].default is not None
    assert [m.name for m in s.methods] == ["manhattan", "reset"]
    assert s.methods[0].params[0].name == "self"
    assert s.methods[1].params[0].mutable  # mut self


def test_feldzugriff_und_methodenaufruf_parsen():
    program = parse(lex(
        "fn main():\n"
        "    let a := p.x\n"
        "    p.reset()\n"
    ))
    let_stmt, call_stmt = program.toplevels[0].body.statements
    assert isinstance(let_stmt.value, ast.FieldAccess)
    assert isinstance(call_stmt.call, ast.MethodCall)


# --- Checker: Konstruktion ------------------------------------------------------

def test_konstruktion_positional_und_benannt():
    ok(POINT +
       "fn main():\n"
       "    let a := Point(3, 4)\n"
       "    let b := Point(x: 3, y: 4)\n"
       "    let c := Point(5)\n"          # y hat Default
       "    let d := Point(x: 7)\n")


def test_konstruktion_fehlendes_feld():
    bad("struct P:\n    x: u8\n    y: u8\nfn main():\n    let a := P(1)\n",
        "fehlt und hat keinen Default")


def test_konstruktion_unbekanntes_feld():
    bad(POINT + "fn main():\n    let a := Point(z: 1)\n", "kein Feld 'z'")


def test_konstruktion_gemischt_verboten():
    bad(POINT + "fn main():\n    let a := Point(1, y: 2)\n",
        "alle Argumente benannt oder alle positional")


def test_konstruktion_zu_viele():
    bad(POINT + "fn main():\n    let a := Point(1, 2, 3)\n", "2 Feld")


def test_benannte_argumente_nur_konstruktoren():
    bad("fn f(a: u8):\n    return\nfn main():\n    f(a: 1)\n",
        "nur bei Konstruktoren")


# --- Checker: Felder, Methoden, mut ------------------------------------------------

def test_feldzugriff_typen():
    ok(POINT +
       "fn main():\n"
       "    let p := Point(3, 4)\n"
       "    let sum := (p.x as u16) + (p.y as u16)\n")


def test_unbekanntes_feld():
    bad(POINT + "fn main():\n    let p := Point(1)\n    let a := p.z\n",
        "kein Feld 'z'")


def test_feld_schreiben_braucht_mut():
    bad(POINT +
        "fn main():\n"
        "    let p := Point(1)\n"
        "    p.x := 5\n", "nicht veränderlich")
    ok(POINT +
       "fn main():\n"
       "    let mut p := Point(1)\n"
       "    p.x := 5\n"
       "    p.x += 1\n")


def test_mut_methode_braucht_mut_variable():
    bad(POINT +
        "fn main():\n"
        "    let p := Point(1)\n"
        "    p.reset()\n", "nicht veränderlich")
    ok(POINT +
       "fn main():\n"
       "    let mut p := Point(1)\n"
       "    p.reset()\n")


def test_lese_methode_darf_nicht_schreiben():
    bad("struct P:\n"
        "    x: u8\n"
        "    fn evil(self):\n"
        "        self.x := 1\n"
        "fn main():\n"
        "    return\n", "mut self")


def test_mut_parameter_beidseitig():
    src = ("struct P:\n"
           "    x: u8\n"
           "fn bump(p: mut P):\n"
           "    p.x += 1\n"
           "fn main():\n"
           "    let mut p := P(1)\n"
           "    bump(mut p)\n")
    ok(src)
    bad(src.replace("bump(mut p)", "bump(p)"), "muss ebenfalls 'mut'")
    bad(("struct P:\n    x: u8\n"
         "fn peek(p: P):\n    return\n"
         "fn main():\n    let mut p := P(1)\n    peek(mut p)\n"),
        "'mut' hier ist falsch")


def test_mut_auf_kopie_typen_verboten():
    bad("fn f(a: mut u8):\n    return\nfn main():\n    f(mut 1)\n",
        "wirkungslos")


def test_wertsemantik_kopie():
    ok(POINT +
       "fn main():\n"
       "    let p := Point(3, 4)\n"
       "    let mut q := p\n"        # Kopie
       "    q.x := 9\n")


def test_struct_rekursion_verboten():
    bad("struct A:\n    b: B\nstruct B:\n    a: A\nfn main():\n    return\n",
        "enthält sich selbst")


def test_unbekannter_typ():
    bad("fn f(p: Punkt):\n    return\nfn main():\n    return\n",
        "unbekannter Typ 'Punkt'")


def test_methoden_zwischenergebnis_nicht_adressierbar():
    bad(POINT +
        "fn make() -> Point:\n"
        "    return Point(1, 2)\n"
        "fn main():\n"
        "    let a := make().manhattan()\n",
        "Variable als Empfänger")


# --- Codegen ------------------------------------------------------------------

def test_c_uebersetzung_der_referenz():
    c = compile_source(POINT + "fn main():\n    let mut p := Point(3, 4)\n    p.reset()\n",
                       "p.mar")
    assert "typedef struct {" in c
    assert "int16_t ml_x;" in c
    assert "} ml_Point;" in c
    # Lese-Methode: const*, mut-Methode: normaler Zeiger (§7.2)
    assert "static uint16_t ml_Point_manhattan(const ml_Point *ml_self)" in c
    assert "static void ml_Point_reset(ml_Point *ml_self)" in c
    assert "ml_Point_reset(&(ml_p))" in c
    # Konstruktion als Compound-Literal, Default eingesetzt
    assert "((ml_Point){ .ml_x =" in c


def test_structs_laufen_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        + POINT +
        "fn main():\n"
        "    let mut p := Point(x: -3, y: 4)\n"
        "    hal_delay_ms(p.manhattan())\n"     # 7
        "    p.reset()\n"
        "    hal_delay_ms(p.manhattan())\n"     # 0
        "    let mut q := p\n"                  # Kopie
        "    q.x := 100\n"
        "    hal_delay_ms(p.manhattan())\n"     # weiterhin 0 (echte Kopie!)
        "    hal_delay_ms(q.manhattan())\n"     # 100
    ))
    assert out == ("  warte 7 ms\n  warte 0 ms\n  warte 0 ms\n  warte 100 ms\n")


def test_mut_parameter_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "struct Zaehler:\n"
        "    n: u16 := 0\n"
        "fn tick(z: mut Zaehler, um: u16):\n"
        "    z.n += um\n"
        "fn main():\n"
        "    let mut z := Zaehler()\n"
        "    tick(mut z, 5)\n"
        "    tick(mut z, 7)\n"
        "    hal_delay_ms(z.n)\n"               # 12
    ))
    assert out == "  warte 12 ms\n"
