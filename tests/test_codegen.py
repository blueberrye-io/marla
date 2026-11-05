"""Codegen-Tests auf dem Host: generiertes C mit cc + hal_host.c bauen,
laufen lassen, stdout vergleichen (Anhang F.1). Kein Board nötig."""

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from marla.cli import compile_source

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
CC = shutil.which("cc") or shutil.which("gcc")

pytestmark = pytest.mark.skipif(CC is None, reason="kein C-Compiler gefunden")


def run_on_host(tmp_path: Path, source: str, seconds: float = 1.0) -> str:
    """Kompiliert MARLA -> C -> Binary, lässt es kurz laufen und liefert stdout.

    Das generierte C endet in `for(;;){}` (AVR-Verhalten), deshalb wird der
    Prozess nach `seconds` abgeschossen; hal_host.c schreibt ungepuffert.
    """
    c_file = tmp_path / "prog.c"
    c_file.write_text(compile_source(source, "prog.mar"), encoding="utf-8")
    binary = tmp_path / "prog"
    subprocess.run(
        [CC, "-std=c99", f"-I{RUNTIME}", "-o", str(binary),
         str(c_file), str(RUNTIME / "hal_host.c"),
         str(RUNTIME / "marla_runtime.c")],
        check=True, capture_output=True,
    )
    proc = subprocess.Popen([str(binary)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    time.sleep(seconds)
    proc.kill()
    out, err = proc.communicate()
    run_on_host.last_stderr = err
    return out


def test_generiertes_c_kompiliert_und_laeuft(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_led_write(on: bool)\n"
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut i: u8 := 0\n"
        "    while i < 3:\n"
        "        hal_led_write(true)\n"
        "        hal_delay_ms(100)\n"
        "        hal_led_write(false)\n"
        "        i := i + 1\n"
    ))
    assert out == ("LED AN\n  warte 100 ms\nLED aus\n" * 3)


def test_arithmetik_und_casts(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let a: u16 := 200 * 3\n"          # 600
        "    hal_delay_ms(a)\n"
        "    let b: u16 := 47 mod 10\n"        # 7
        "    hal_delay_ms(b)\n"
        "    let c: u16 := -7 // 2 + 4\n"      # C-Rundung zur Null: -3 + 4 = 1
        "    hal_delay_ms(c)\n"
        "    let d: u8 := 0xF0\n"
        "    hal_delay_ms((d >> 4) as u16)\n"  # 15
    ))
    assert out == "  warte 600 ms\n  warte 7 ms\n  warte 1 ms\n  warte 15 ms\n"


def test_u8_wrap_semantik(tmp_path):
    # u8-Arithmetik wrappt exakt bei 8 Bit, trotz C-Integer-Promotion
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut y: u8 := 250\n"
        "    y := y + 10\n"                    # 260 wrappt zu 4
        "    hal_delay_ms(y as u16)\n"
        "    let x: u8 := 0x10\n"
        "    if x << 4 = 0:\n"                 # u8: 0x100 wrappt zu 0
        "        hal_delay_ms(1)\n"
        "    else:\n"
        "        hal_delay_ms(2)\n"
    ))
    assert out == "  warte 4 ms\n  warte 1 ms\n"


def test_funktionen_und_rueckgabe(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn double(x: u16) -> u16:\n"
        "    return x * 2\n"
        "fn pick(flag: bool, a: u16, b: u16) -> u16:\n"
        "    if flag:\n"
        "        return a\n"
        "    else:\n"
        "        return b\n"
        "fn main():\n"
        "    hal_delay_ms(double(21))\n"
        "    hal_delay_ms(pick(true, 1, 2))\n"
        "    hal_delay_ms(pick(false, 1, 2))\n"
    ))
    assert out == "  warte 42 ms\n  warte 1 ms\n  warte 2 ms\n"


def test_loop_break_kombizuweisung(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut n: u16 := 1\n"
        "    loop:\n"
        "        n *= 3\n"
        "        if n > 20:\n"
        "            break\n"
        "    hal_delay_ms(n)\n"                # 27
    ))
    assert out == "  warte 27 ms\n"


def test_line_direktiven_und_prefix():
    c = compile_source(
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    hal_delay_ms(1)\n",
        "blink.mar",
    )
    assert '#line 3 "blink.mar"' in c
    assert "static void ml_main(void)" in c
    assert "hal_delay_ms" in c          # extern bleibt unpräfixiert
    assert "int main(void)" in c
    assert "hal_init();" in c


def test_const_als_define(tmp_path):
    c = compile_source(
        "const LED_PIN: u8 := 13\n"
        "const READY: u16 := 1 << 9\n"
        "const DEBUG: bool := false\n"
        "fn main():\n"
        "    return\n",
        "konst.mar",
    )
    assert "#define ml_LED_PIN ((uint8_t)(13))" in c
    assert "#define ml_READY ((uint16_t)(512))" in c   # gefaltet, nicht 1 << 9
    assert "#define ml_DEBUG (false)" in c


def test_const_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "const BASE: u16 := 200\n"
        "const TRIPLE: u16 := BASE * 3\n"
        "fn main():\n"
        "    hal_delay_ms(TRIPLE)\n"
        "    hal_delay_ms(BASE + 1)\n"
    ))
    assert out == "  warte 600 ms\n  warte 201 ms\n"


def test_continue_laeuft_auf_host(tmp_path):
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let mut i: u8 := 0\n"
        "    while i < 6:\n"
        "        i += 1\n"
        "        if i mod 2 = 0:\n"
        "            continue\n"
        "        hal_delay_ms(i as u16)\n"
    ))
    assert out == "  warte 1 ms\n  warte 3 ms\n  warte 5 ms\n"


def test_integer_promotion_falle(tmp_path):
    # Anhang D Rev. 2: u8*u8 = 200*200 wäre auf AVR signed-int16-UB;
    # mit unsigned-Operanden ist es definierte Modulo-Arithmetik: 40000 mod 256 = 64
    out = run_on_host(tmp_path, (
        "extern fn hal_delay_ms(ms: u16)\n"
        "fn main():\n"
        "    let a: u8 := 200\n"
        "    let b := a * a\n"
        "    hal_delay_ms(b as u16)\n"
        "    let mut x: u8 := 200\n"
        "    x *= 2\n"                       # kombinierte Zuweisung: 400 mod 256 = 144
        "    hal_delay_ms(x as u16)\n"
    ))
    assert out == "  warte 64 ms\n  warte 144 ms\n"


def test_operanden_casts_im_c():
    c = compile_source(
        "fn main():\n"
        "    let a: u8 := 10\n"
        "    let b := a * a\n",
        "promo.mar",
    )
    assert "(unsigned)" in c   # Operanden-Cast bei u8-Arithmetik vorhanden


def test_blink_beispiel_kompiliert_auf_host(tmp_path):
    source = (ROOT / "examples" / "blink.mar").read_text(encoding="utf-8")
    out = run_on_host(tmp_path, source, seconds=0.5)
    # Endlosschleife: nur den Anfang der Sequenz prüfen
    assert out.startswith("LED AN\n  warte 500 ms\nLED aus\n  warte 500 ms\n")
