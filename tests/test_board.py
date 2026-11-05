"""P2.8: Board-Konfiguration (TOML) + Peripherie-Aktoren (Abschnitt 10)."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from marla.board import board_header, load_board, validate_against
from marla.checker import check
from marla.cli import compile_source
from marla.errors import CompileError
from marla.lexer import lex
from marla.parser import parse

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
CC = shutil.which("cc") or shutil.which("gcc")


BLINKER = (
    "extern fn hal_delay_ms(ms: u16)\n"
    "actor Blinker(mailbox: 4, full: DropOldest):\n"
    "    ticks: u16 := 0\n"
    "    fn receive(msg: TimerMsg):\n"
    "        match msg:\n"
    "            Tick:\n"
    "                self.ticks += 1\n"
    "                hal_delay_ms(self.ticks)\n"
    "fn main():\n"
    "    return\n"
)


def make_board(tmp_path, text):
    p = tmp_path / "test.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_board_laden(tmp_path):
    p = make_board(tmp_path, (
        '[board]\nname = "Test"\nstrings = "tagged"\npanic = "reset"\n'
        '[timer0]\ntype = "timer"\nperiod_ms = 50\ntarget = "Blinker"\n'
    ))
    b = load_board(p)
    assert b.strings == "tagged" and b.panic == "reset"
    assert b.peripherals[0].kind == "timer"
    assert b.peripherals[0].period_ms == 50


def test_board_header_defines(tmp_path):
    p = make_board(tmp_path, '[board]\nstrings = "tagged"\npanic = "reset"\n')
    h = board_header(load_board(p))
    assert "#define MARLA_STRINGS_TAGGED 1" in h
    assert "#define MARLA_PANIC_RESET 1" in h


def test_board_validierung_fehler(tmp_path):
    with pytest.raises(CompileError) as exc:
        load_board(make_board(tmp_path, '[x]\ntype = "spi"\ntarget = "A"\n'))
    assert "synchrones" in exc.value.message  # I2C/SPI: FFI-Hinweis

    with pytest.raises(CompileError) as exc:
        load_board(make_board(tmp_path, '[board]\nstrings = "magic"\n'))
    assert "direct" in exc.value.message


def test_ziel_aktor_muss_existieren(tmp_path):
    b = load_board(make_board(
        tmp_path, '[t]\ntype = "timer"\nperiod_ms = 10\ntarget = "Nix"\n'))
    program = check(parse(lex(BLINKER)))
    with pytest.raises(CompileError) as exc:
        validate_against(b, program, "test.toml")
    assert "existiert nicht" in exc.value.message


def test_ziel_aktor_braucht_timermsg(tmp_path):
    b = load_board(make_board(
        tmp_path, '[t]\ntype = "uart"\ntarget = "Blinker"\n'))
    program = check(parse(lex(BLINKER)))
    with pytest.raises(CompileError) as exc:
        validate_against(b, program, "test.toml")
    assert "UartMsg" in exc.value.message


def test_glue_im_generierten_c(tmp_path):
    b = load_board(make_board(
        tmp_path, '[timer0]\ntype = "timer"\nperiod_ms = 20\ntarget = "Blinker"\n'))
    c = compile_source(BLINKER, "b.mar", board=b)
    assert "ISR(TIMER0_COMPA_vect)" in c
    assert "ml__board_init" in c
    assert "marla_host_now_ms" in c        # Host-Attrappe im selben C
    assert "ml__send_Blinker" in c


@pytest.mark.skipif(CC is None, reason="kein C-Compiler")
def test_timer_aktor_laeuft_auf_host(tmp_path):
    b = load_board(make_board(
        tmp_path, '[timer0]\ntype = "timer"\nperiod_ms = 30\ntarget = "Blinker"\n'))
    c_file = tmp_path / "prog.c"
    c_file.write_text(compile_source(BLINKER, "prog.mar", board=b),
                      encoding="utf-8")
    binary = tmp_path / "prog"
    subprocess.run(
        [CC, "-std=c99", f"-I{RUNTIME}", "-o", str(binary), str(c_file),
         str(RUNTIME / "hal_host.c"), str(RUNTIME / "marla_runtime.c")],
        check=True, capture_output=True)
    proc = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, text=True)
    time.sleep(0.35)
    proc.kill()
    out, _ = proc.communicate()
    lines = out.splitlines()
    # ~11 Ticks in 350 ms bei 30 ms Periode; großzügig prüfen
    assert len(lines) >= 5
    assert lines[0] == "  warte 1 ms"      # Tick-Zähler beginnt bei 1
    assert lines[1] == "  warte 2 ms"


@pytest.mark.skipif(CC is None, reason="kein C-Compiler")
def test_uart_aktor_laeuft_auf_host(tmp_path):
    src = (
        "extern fn hal_delay_ms(ms: u16)\n"
        "actor Echo(mailbox: 8, full: Drop):\n"
        "    fn receive(msg: UartMsg):\n"
        "        match msg:\n"
        "            ByteReceived(b):\n"
        "                hal_delay_ms(b as u16)\n"
        "fn main():\n"
        "    return\n"
    )
    b = load_board(make_board(
        tmp_path, '[uart0]\ntype = "uart"\nbaud = 9600\ntarget = "Echo"\n'))
    c_file = tmp_path / "prog.c"
    c_file.write_text(compile_source(src, "prog.mar", board=b), encoding="utf-8")
    binary = tmp_path / "prog"
    subprocess.run(
        [CC, "-std=c99", f"-I{RUNTIME}", "-o", str(binary), str(c_file),
         str(RUNTIME / "hal_host.c"), str(RUNTIME / "marla_runtime.c")],
        check=True, capture_output=True)
    proc = subprocess.Popen([str(binary)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    proc.stdin.write("AB")
    proc.stdin.flush()
    time.sleep(0.3)
    proc.kill()
    out, _ = proc.communicate()
    assert out == "  warte 65 ms\n  warte 66 ms\n"   # 'A', 'B' über die Mailbox
