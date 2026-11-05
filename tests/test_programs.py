"""Programm-Tests (Anhang F.1):

- tests/programs/ok/*.mar müssen fehlerfrei durchlaufen.
- tests/programs/err/*.mar tragen in Zeile 1 einen Kommentar mit dem erwarteten
  Fehlertext-Fragment (`# ERWARTET: ...`); genau dieser Fehler muss kommen.
  Fehlermeldungen sind damit Teil der Testsuite.
"""

from pathlib import Path

import pytest

from marla.cli import compile_source
from marla.errors import CompileError

PROGRAMS = Path(__file__).resolve().parent / "programs"
OK_PROGRAMS = sorted((PROGRAMS / "ok").glob("*.mar"))
ERR_PROGRAMS = sorted((PROGRAMS / "err").glob("*.mar"))


def test_korpus_vollstaendig():
    assert len(OK_PROGRAMS) >= 4
    # Definition of Done: mindestens zehn absichtlich falsche Programme
    assert len(ERR_PROGRAMS) >= 10


@pytest.mark.parametrize("path", OK_PROGRAMS, ids=lambda p: p.stem)
def test_ok_programm_kompiliert(path):
    c = compile_source(path.read_text(encoding="utf-8"), path.name)
    assert "int main(void)" in c


@pytest.mark.parametrize("path", ERR_PROGRAMS, ids=lambda p: p.stem)
def test_err_programm_liefert_erwartete_meldung(path):
    source = path.read_text(encoding="utf-8")
    first_line = source.splitlines()[0]
    assert first_line.startswith("# ERWARTET:"), \
        f"{path.name}: Zeile 1 braucht '# ERWARTET: <Fragment>'"
    fragment = first_line.removeprefix("# ERWARTET:").strip()

    with pytest.raises(CompileError) as exc:
        compile_source(source, path.name)
    assert fragment in exc.value.message, (
        f"{path.name}: erwartet Fragment {fragment!r}, "
        f"bekommen: {exc.value.message!r}"
    )
    # hübsche Ausgabe funktioniert und nennt Datei + Zeile (kein Traceback)
    pretty = exc.value.pretty(source)
    assert path.name in pretty and "Fehler:" in pretty
