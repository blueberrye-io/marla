"""CompileError + hübsche Ausgabe mit Code-Ausschnitt."""

from __future__ import annotations


class CompileError(Exception):
    def __init__(self, message: str, line: int, col: int, filename: str = "<unbekannt>"):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col
        self.filename = filename

    def pretty(self, source: str | None = None) -> str:
        """Formatiert den Fehler mit Datei, Zeile, Spalte und markiertem Ausschnitt."""
        head = f"{self.filename}:{self.line}:{self.col}: Fehler: {self.message}"
        if source is None:
            return head
        lines = source.splitlines()
        if not (1 <= self.line <= len(lines)):
            return head
        src_line = lines[self.line - 1]
        caret = " " * (self.col - 1) + "^"
        return f"{head}\n    {src_line}\n    {caret}"
