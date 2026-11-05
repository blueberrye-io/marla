"""Kommandozeile: marla compile blink.mar -o blink.c"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, board as board_mod, checker, codegen, lexer, parser
from .errors import CompileError


def compile_source(source: str, filename: str, board=None,
                   board_filename: str = "<board>") -> str:
    """Quelltext -> C-Quelltext. Wirft CompileError."""
    tokens = lexer.lex(source, filename)
    program = parser.parse(tokens, filename)
    checker.check(program, filename)
    if board is not None:
        board_mod.validate_against(board, program, board_filename)
    return codegen.generate(program, filename, board)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="marla",
        description="MARLA-Compiler (Phase 1): transpiliert .mar nach portablem C99.",
    )
    ap.add_argument("--version", action="version", version=f"marla {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    ap_compile = sub.add_parser("compile", help="eine .mar-Datei nach C übersetzen")
    ap_compile.add_argument("input", help="Eingabedatei (.mar)")
    ap_compile.add_argument("-o", "--output",
                            help="Ausgabedatei (.c); Standard: Eingabename mit .c")
    ap_compile.add_argument("--board",
                            help="Board-Konfiguration (TOML, Abschnitt 10); "
                                 "schreibt marla_board.h neben die Ausgabe")

    args = ap.parse_args(argv)

    in_path = Path(args.input)
    try:
        source = in_path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"marla: kann '{in_path}' nicht lesen: {exc.strerror}", file=sys.stderr)
        return 1

    board = None
    if args.board:
        try:
            board = board_mod.load_board(args.board)
        except CompileError as err:
            print(err.pretty(), file=sys.stderr)
            return 1

    try:
        c_source = compile_source(source, in_path.name, board,
                                  Path(args.board).name if args.board else "<board>")
    except CompileError as err:
        print(err.pretty(source), file=sys.stderr)
        return 1

    out_path = Path(args.output) if args.output else in_path.with_suffix(".c")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(c_source, encoding="utf-8")
    print(f"marla: {in_path} -> {out_path}")
    if board is not None:
        header = out_path.parent / "marla_board.h"
        header.write_text(board_mod.board_header(board), encoding="utf-8")
        print(f"marla: Board '{board.name}' -> {header}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
