"""Checker-Tests entlang der Liste in Anhang B.4."""

import pytest

from marla.checker import check
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse


def run(source):
    return check(parse(lex(source)))


def ok(source):
    run(source)  # darf nicht werfen


def bad(source, fragment):
    with pytest.raises(CompileError) as exc:
        run(source)
    assert fragment in exc.value.message, exc.value.message
    return exc.value


# --- 1. Namen -----------------------------------------------------------------

def test_unbekannter_name():
    bad("fn main():\n    let y: u8 := x\n", "nicht deklariert")


def test_doppeldeklaration_im_selben_scope():
    bad("fn main():\n    let x: u8 := 1\n    let x: u8 := 2\n", "bereits deklariert")


def test_shadowing_in_innerem_block_erlaubt():
    ok("fn main():\n"
       "    let x: u8 := 1\n"
       "    if x = 1:\n"
       "        let x: u16 := 2\n"
       "        hal(x)\n"
       "extern fn hal(v: u16)\n")


def test_scope_endet_mit_block():
    bad("fn main():\n"
        "    if true:\n"
        "        let x: u8 := 1\n"
        "    let y: u8 := x\n", "nicht deklariert")


def test_doppelte_funktion():
    bad("fn f():\n    return\nfn f():\n    return\nfn main():\n    f()\n",
        "bereits deklariert")


# --- 2./3. Typen und Bedingungen -------------------------------------------------

def test_keine_impliziten_casts():
    bad("fn main():\n"
        "    let a: u8 := 1\n"
        "    let b: u16 := 2\n"
        "    let c := a + b\n", "expliziter Cast mit 'as'")


def test_cast_macht_es_gut():
    ok("fn main():\n"
       "    let a: u8 := 1\n"
       "    let b: u16 := 2\n"
       "    let c := (a as u16) + b\n")


def test_bedingung_muss_bool_sein():
    err = bad("fn main():\n"
              "    let x: u8 := 1\n"
              "    if x:\n"
              "        return\n", "<> 0")
    assert "u8" in err.message


def test_while_bedingung_muss_bool_sein():
    bad("fn main():\n"
        "    let x: u8 := 1\n"
        "    while x:\n"
        "        return\n", "<> 0")


def test_and_verlangt_bool():
    bad("fn main():\n"
        "    let x: u8 := 1\n"
        "    let b := x and true\n", "'and' erwartet 'bool'")


def test_not_verlangt_bool():
    bad("fn main():\n"
        "    let x: u8 := 1\n"
        "    let b := not x\n", "'not' erwartet 'bool'")


def test_vergleich_liefert_bool():
    ok("fn main():\n"
       "    let x: u8 := 1\n"
       "    let b := x < 10\n"
       "    if b:\n"
       "        return\n")


def test_ordnung_auf_bool_verboten():
    bad("fn main():\n    let b := true < false\n", "nicht definiert")


def test_bool_gleichheit_erlaubt():
    ok("fn main():\n    let b := true <> false\n")


def test_cast_nach_bool_verboten():
    bad("fn main():\n"
        "    let x: u8 := 1\n"
        "    let b := x as bool\n", "Vergleich")


# --- 4. Literale: Typ aus Kontext, Wertebereich ---------------------------------

def test_literal_nimmt_typ_aus_parameter():
    ok("extern fn hal(v: u8)\nfn main():\n    hal(200)\n")


def test_literal_bereichspruefung_parameter():
    bad("extern fn hal(v: u8)\nfn main():\n    hal(300)\n", "passt nicht in 'u8'")


def test_literal_bereichspruefung_let():
    bad("fn main():\n    let x: i8 := 200\n", "passt nicht in 'i8'")


def test_negatives_literal():
    ok("fn main():\n    let x: i8 := -128\n")
    bad("fn main():\n    let x: i8 := -129\n", "passt nicht in 'i8'")


def test_negatives_literal_in_unsigned():
    bad("fn main():\n    let x: u8 := -1\n", "passt nicht in 'u8'")


def test_annotation_noetig_ohne_kontext():
    bad("fn main():\n    let x := 5\n", "Typannotation")


def test_literal_nimmt_typ_von_gegenseite():
    ok("fn main():\n"
       "    let x: u16 := 500\n"
       "    let y := x + 1\n"
       "    hal(y)\n"
       "extern fn hal(v: u16)\n")


def test_konstante_division_durch_null():
    bad("fn main():\n    let x: u8 := 1 // 0\n", "Division durch 0")


# --- 5. mut ----------------------------------------------------------------------

def test_zuweisung_an_nicht_mut():
    bad("fn main():\n    let x: u8 := 1\n    x := 2\n", "nicht veränderlich")


def test_zuweisung_an_mut_ok():
    ok("fn main():\n    let mut x: u8 := 1\n    x := 2\n    x += 3\n")


def test_parameter_nur_lesbar():
    bad("fn f(a: u8):\n    a := 2\nfn main():\n    f(1)\n", "Lese-Sicht")


def test_kombizuweisung_auf_bool_verboten():
    bad("fn main():\n    let mut b: bool := true\n    b += 1\n", "Zahlentyp")


# --- 6. return --------------------------------------------------------------------

def test_alle_pfade_returnen_fehlt():
    bad("fn f() -> u8:\n"
        "    let x: u8 := 1\n"
        "    if x = 1:\n"
        "        return 1\n"
        "fn main():\n"
        "    let y := f()\n", "nicht alle Pfade")


def test_alle_pfade_returnen_mit_else():
    ok("fn f(x: u8) -> u8:\n"
       "    if x = 1:\n"
       "        return 1\n"
       "    else:\n"
       "        return 2\n"
       "fn main():\n"
       "    let y := f(1)\n")


def test_endlosschleife_zaehlt_als_pfad():
    ok("fn f() -> u8:\n"
       "    loop:\n"
       "        f2()\n"
       "fn f2():\n"
       "    return\n"
       "fn main():\n"
       "    let y := f()\n")


def test_endlosschleife_mit_break_zaehlt_nicht():
    bad("fn f() -> u8:\n"
        "    loop:\n"
        "        break\n"
        "fn main():\n"
        "    let y := f()\n", "nicht alle Pfade")


def test_return_typ_passt_nicht():
    bad("fn f() -> u8:\n"
        "    let x: u16 := 1\n"
        "    return x\n"
        "fn main():\n"
        "    let y := f()\n", "erwartet 'u8'")


def test_return_ohne_wert_bei_rueckgabetyp():
    bad("fn f() -> u8:\n    return\nfn main():\n    let y := f()\n",
        "braucht einen Wert")


def test_return_mit_wert_ohne_rueckgabetyp():
    bad("fn f():\n    return 1\nfn main():\n    f()\n", "keinen Rückgabetyp")


def test_void_aufruf_im_ausdruck():
    bad("fn f():\n    return\nfn main():\n    let x := f()\n", "keinen Rückgabewert")


# --- 7. break ---------------------------------------------------------------------

def test_break_ausserhalb_schleife():
    bad("fn main():\n    break\n", "nur innerhalb")


def test_continue_ausserhalb_schleife():
    bad("fn main():\n    continue\n", "nur innerhalb")


def test_continue_in_schleife_ok():
    ok("fn main():\n"
       "    let mut i: u8 := 0\n"
       "    while i < 10:\n"
       "        i += 1\n"
       "        if i mod 2 = 0:\n"
       "            continue\n")


# --- const (Rev. 2) --------------------------------------------------------------

def test_const_verwendbar_und_typ():
    ok("const LED_PIN: u8 := 13\n"
       "extern fn hal(v: u8)\n"
       "fn main():\n"
       "    hal(LED_PIN)\n"
       "    hal(LED_PIN + 1)\n")


def test_const_konstanter_ausdruck_mit_folding():
    # Go-/Zig-Modell: unbegrenzt rechnen, nur Endergebnis prüfen
    ok("const A: u16 := -7 // 2 + 4\n"      # -3 + 4 = 1
       "const MASK: u8 := 1 << 5\n"
       "fn main():\n"
       "    return\n")


def test_const_verwendung_vor_deklaration():
    ok("const SPAET: u16 := FRUEH * 2\n"
       "const FRUEH: u16 := 100\n"
       "fn main():\n"
       "    return\n")


def test_const_bereichspruefung():
    bad("const X: u8 := 300\nfn main():\n    return\n", "passt nicht in 'u8'")


def test_const_bool():
    ok("const DEBUG: bool := true\n"
       "fn main():\n"
       "    if DEBUG:\n"
       "        return\n")


def test_const_bool_typkonflikt():
    bad("const X: u8 := true\nfn main():\n    return\n", "'bool'")


def test_const_mit_funktionsaufruf():
    bad("extern fn f() -> u8\nconst X: u8 := f()\nfn main():\n    return\n",
        "kein konstanter Ausdruck")


def test_const_zyklus():
    bad("const A: u8 := B\nconst B: u8 := A\nfn main():\n    return\n",
        "zyklisch")


def test_const_cast_bereichsgeprueft():
    bad("const A: u16 := 0x1234\nconst B: u8 := A as u8\nfn main():\n    return\n",
        "passt nicht in 'u8'")
    ok("const A: u16 := 0x1234\nconst B: u8 := (A & 0xFF) as u8\n"
       "fn main():\n    return\n")


def test_const_zuweisung_verboten():
    bad("const X: u8 := 1\nfn main():\n    X := 2\n", "Konstante")


def test_const_schatten_verboten():
    # verhindert auch die #define-Falle im Codegen
    bad("const X: u8 := 1\nfn main():\n    let X: u8 := 2\n",
        "bereits als Konstante")


def test_const_parametername_verboten():
    bad("const X: u8 := 1\nfn f(X: u8):\n    return\nfn main():\n    f(1)\n",
        "bereits als Konstante")


def test_const_doppelt():
    bad("const X: u8 := 1\nconst X: u8 := 2\nfn main():\n    return\n",
        "bereits deklariert")


def test_const_name_kollidiert_mit_funktion():
    bad("const f: u8 := 1\nfn f():\n    return\nfn main():\n    return\n",
        "bereits als Konstante")


def test_break_in_if_in_schleife_ok():
    ok("fn main():\n"
       "    loop:\n"
       "        if true:\n"
       "            break\n")


# --- 8. main ----------------------------------------------------------------------

def test_main_fehlt():
    bad("fn f():\n    return\n", "fn main()")


def test_main_mit_parametern():
    bad("fn main(x: u8):\n    return\n", "keine Parameter")


def test_main_mit_rueckgabetyp():
    bad("fn main() -> u8:\n    return 1\n", "keinen Rückgabetyp")


# --- Aufrufe ------------------------------------------------------------------------

def test_falsche_argumentzahl():
    bad("extern fn hal(a: u8, b: u8)\nfn main():\n    hal(1)\n", "2 Argument")


def test_falscher_argumenttyp():
    bad("extern fn hal(a: u8)\n"
        "fn main():\n"
        "    let x: u16 := 1\n"
        "    hal(x)\n", "erwartet 'u8'")


def test_unbekannte_funktion():
    bad("fn main():\n    zap()\n", "unbekannte Funktion")


def test_funktion_als_wert():
    bad("fn f():\n    return\nfn main():\n    let x := f\n", "Aufruf mit")


def test_vorwaertsreferenz_erlaubt():
    ok("fn main():\n    helper()\nfn helper():\n    return\n")
