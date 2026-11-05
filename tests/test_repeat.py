"""P2.1: repeat/until — fußgesteuerte Schleife (Referenz §8)."""

from pathlib import Path

import pytest

from marla import ast_nodes as ast
from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse


def run(source):
    return check(parse(lex(source)))


def bad(source, fragment):
    with pytest.raises(CompileError) as exc:
        run(source)
    assert fragment in exc.value.message, exc.value.message


def test_repeat_parst():
    program = parse(lex(
        "fn main():\n"
        "    let mut x: u8 := 0\n"
        "    repeat:\n"
        "        x += 1\n"
        "    until x >= 3\n"
    ))
    stmt = program.toplevels[0].body.statements[1]
    assert isinstance(stmt, ast.RepeatStmt)
    assert isinstance(stmt.cond, ast.BinaryOp)


def test_repeat_referenzbeispiel_aus_abschnitt_8():
    # repeat: status := hal_read_status() / until status & READY_BIT <> 0
    run("extern fn hal_read_status() -> u8\n"
        "const READY_BIT: u8 := 0b0000_0100\n"
        "fn main():\n"
        "    let mut status: u8 := 0\n"
        "    repeat:\n"
        "        status := hal_read_status()\n"
        "    until status & READY_BIT <> 0\n")


def test_until_bedingung_muss_bool_sein():
    bad("fn main():\n"
        "    let mut x: u8 := 0\n"
        "    repeat:\n"
        "        x += 1\n"
        "    until x\n", "<> 0")


def test_until_sieht_keine_blocklokalen_namen():
    bad("fn main():\n"
        "    repeat:\n"
        "        let fertig: bool := true\n"
        "    until fertig\n", "nicht deklariert")


def test_until_ohne_repeat():
    bad("fn main():\n    until true\n", "ohne zugehöriges 'repeat'")


def test_break_und_continue_in_repeat_erlaubt():
    run("fn main():\n"
        "    let mut x: u8 := 0\n"
        "    repeat:\n"
        "        x += 1\n"
        "        if x = 1:\n"
        "            continue\n"
        "        if x > 10:\n"
        "            break\n"
        "    until x >= 3\n")


def test_repeat_zaehlt_als_return_pfad():
    run("fn f() -> u8:\n"
        "    repeat:\n"
        "        return 1\n"
        "    until true\n"
        "fn main():\n"
        "    let x := f()\n")


def test_codegen_do_while_negiert():
    c = compile_source(
        "fn main():\n"
        "    let mut x: u8 := 0\n"
        "    repeat:\n"
        "        x += 1\n"
        "    until x >= 3\n",
        "r.mar",
    )
    assert "do {" in c
    assert "} while (!(" in c
