"""Parser-Tests: Rangfolge-Kaskade, Chaining-Verbot, freundliche Hinweise, ganzes Programm."""

from pathlib import Path

import pytest

from marla import ast_nodes as ast
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

ROOT = Path(__file__).resolve().parent.parent


def parse_program(source):
    return parse(lex(source))


def parse_expr(expr_src):
    """Bettet den Ausdruck in ein Mini-Programm ein und holt ihn wieder heraus."""
    program = parse_program(f"fn main():\n    let q := {expr_src}\n")
    let_stmt = program.toplevels[0].body.statements[0]
    return let_stmt.value


def test_rangfolge_bits_ueber_vergleichen():
    # a & b <> 0 parst als (a & b) <> 0  — die bewusste C-Korrektur
    e = parse_expr("a & b <> 0")
    assert isinstance(e, ast.BinaryOp) and e.op == "<>"
    assert isinstance(e.left, ast.BinaryOp) and e.left.op == "&"
    assert isinstance(e.right, ast.NumberLit)


def test_rangfolge_punkt_vor_strich():
    e = parse_expr("a + b * c")
    assert e.op == "+"
    assert isinstance(e.right, ast.BinaryOp) and e.right.op == "*"


def test_rangfolge_shift_ueber_add():
    # Kaskade: shift { << add } -> a << (b + 1) bindet + stärker
    e = parse_expr("a << b + 1")
    assert e.op == "<<"
    assert isinstance(e.right, ast.BinaryOp) and e.right.op == "+"


def test_rangfolge_vergleich_ueber_and_or():
    e = parse_expr("a < b and c or d")
    assert e.op == "or"
    assert e.left.op == "and"
    assert e.left.left.op == "<"


def test_not_bindet_staerker_als_and():
    e = parse_expr("not a and b")
    assert isinstance(e, ast.BinaryOp) and e.op == "and"
    assert isinstance(e.left, ast.UnaryOp) and e.left.op == "not"


def test_unaeres_minus_schwaecher_als_as():
    # -x as u8 parst als -(x as u8), Grammatik: unaer = "-" unaer | cast
    e = parse_expr("-x as u8")
    assert isinstance(e, ast.UnaryOp) and e.op == "-"
    assert isinstance(e.operand, ast.CastExpr) and e.operand.target == "u8"


def test_klammern():
    e = parse_expr("(a + b) * c")
    assert e.op == "*"
    assert isinstance(e.left, ast.BinaryOp) and e.left.op == "+"


def test_mehrfacher_cast():
    e = parse_expr("x as u16 as u32")
    assert isinstance(e, ast.CastExpr) and e.target == "u32"
    assert isinstance(e.expr, ast.CastExpr) and e.expr.target == "u16"


def expect_error(source, fragment):
    with pytest.raises(CompileError) as exc:
        parse_program(source)
    assert fragment in exc.value.message, exc.value.message


def test_kein_chaining():
    expect_error("fn main():\n    if 0 <= x < 10:\n        return\n", "and")


def test_hinweis_eqeq():
    expect_error("fn main():\n    if x == 1:\n        return\n", "meintest du '='")


def test_hinweis_bangeq():
    expect_error("fn main():\n    if x != 1:\n        return\n", "meintest du '<>'")


def test_hinweis_percent():
    expect_error("fn main():\n    let y := x % 2\n", "meintest du 'mod'")


def test_hinweis_zuweisung_mit_gleich():
    expect_error("fn main():\n    x = 5\n", "meintest du ':='")


def test_hinweis_let_mit_gleich():
    expect_error("fn main():\n    let x = 5\n", "meintest du ':='")


def test_zuweisung_kein_ausdruck():
    # x := (y := 5) — Zuweisung ist Statement, ':=' im Ausdruck ist Fehler
    expect_error("fn main():\n    x := (y := 5)\n", "erwartet")


def test_extern_ohne_rumpf():
    program = parse_program("extern fn hal_x(ms: u16)\nfn main():\n    hal_x(1)\n")
    ext = program.toplevels[0]
    assert isinstance(ext, ast.ExternDecl)
    assert ext.params[0].typ == "u16"
    assert ext.return_type is None


def test_funktionskopf_mit_rueckgabetyp():
    program = parse_program("fn f(a: u8, b: i16) -> u32:\n    return 0\nfn main():\n    f(1, 2)\n")
    fn = program.toplevels[0]
    assert fn.return_type == "u32"
    assert [p.typ for p in fn.params] == ["u8", "i16"]


def test_if_elif_else():
    program = parse_program(
        "fn main():\n"
        "    if a:\n"
        "        x := 1\n"
        "    elif b:\n"
        "        x := 2\n"
        "    elif c:\n"
        "        x := 3\n"
        "    else:\n"
        "        x := 4\n"
    )
    if_stmt = program.toplevels[0].body.statements[0]
    assert isinstance(if_stmt, ast.IfStmt)
    assert len(if_stmt.branches) == 3
    assert if_stmt.else_block is not None


def test_blink_wird_vollstaendiger_ast():
    # M4: examples/blink.mar (Rev. 2, mit const) wird zum vollständigen AST
    source = (ROOT / "examples" / "blink.mar").read_text(encoding="utf-8")
    program = parse_program(source)
    assert len(program.toplevels) == 4
    externs = [t for t in program.toplevels if isinstance(t, ast.ExternDecl)]
    consts = [t for t in program.toplevels if isinstance(t, ast.ConstDecl)]
    fns = [t for t in program.toplevels if isinstance(t, ast.FnDef)]
    assert {e.name for e in externs} == {"hal_led_write", "hal_delay_ms"}
    assert consts[0].name == "BLINK_MS" and consts[0].typ == "u16"
    assert fns[0].name == "main"
    loop = fns[0].body.statements[1]
    assert isinstance(loop, ast.LoopStmt)
    assert len(loop.body.statements) == 7


def test_const_deklaration():
    program = parse_program("const LED_PIN: u8 := 13\nfn main():\n    return\n")
    const = program.toplevels[0]
    assert isinstance(const, ast.ConstDecl)
    assert (const.name, const.typ) == ("LED_PIN", "u8")
    assert isinstance(const.value, ast.NumberLit)


def test_const_mit_konstantem_ausdruck():
    program = parse_program("const MASK: u8 := 1 << 5\nfn main():\n    return\n")
    assert isinstance(program.toplevels[0].value, ast.BinaryOp)


def test_const_braucht_typannotation():
    expect_error("const LIMIT := 100\nfn main():\n    return\n", "Typannotation")


def test_const_nicht_in_funktion():
    expect_error("fn main():\n    const X: u8 := 1\n", "oberster Ebene")


def test_continue_statement():
    program = parse_program(
        "fn main():\n    loop:\n        continue\n")
    loop = program.toplevels[0].body.statements[0]
    assert isinstance(loop.body.statements[0], ast.ContinueStmt)
