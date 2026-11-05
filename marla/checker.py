"""Symboltabelle, Typprüfung, mut-Prüfung, "alle Pfade returnen" (Anhang B.4).

Zahlliterale sind typlos ("int") und nehmen ihren Typ aus dem Kontext an;
`finalize` schreibt den Kontexttyp in den Teilbaum und prüft Wertebereiche.

Phase 2: structs mit Methoden (§7) — Registry, Konstruktion, Feldzugriff,
mut-Regel auf beiden Seiten der Aufrufstelle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import ast_nodes as ast
from .errors import CompileError
from .prelude import PRELUDE_ENUMS

INT_TYPES = ("u8", "i8", "u16", "i16", "u32", "i32")
SCALAR_TYPES = INT_TYPES + ("bool",)

RANGES: dict[str, tuple[int, int]] = {
    "u8": (0, 255),
    "i8": (-128, 127),
    "u16": (0, 65535),
    "i16": (-32768, 32767),
    "u32": (0, 4294967295),
    "i32": (-2147483648, 2147483647),
}

UNTYPED = "int"  # Pseudo-Typ für Literale ohne Kontext

# Pseudo-Typen für kontext-typisierte Result-Konstruktoren
OK_PSEUDO = "__Ok__"
ERR_PSEUDO = "__Err__"
PSEUDO_TYPES = (UNTYPED, OK_PSEUDO, ERR_PSEUDO)


def is_result(t: str | None) -> bool:
    return t is not None and t.startswith("Result[")


def result_parts(t: str) -> tuple[str, str]:
    """'Result[u16,ErrCode]' -> ('u16', 'ErrCode') — keine Verschachtelung."""
    inner = t[len("Result["):-1]
    ok_t, err_t = inner.split(",", 1)
    return ok_t, err_t


def show_type(t: str) -> str:
    if t == UNTYPED:
        return "Zahlliteral"
    if t == OK_PSEUDO:
        return "Ok(...)"
    if t == ERR_PSEUDO:
        return "Err(...)"
    return f"'{t}'"


@dataclass
class FnSig:
    name: str
    params: list[ast.Param]
    return_type: str | None
    is_extern: bool
    line: int
    col: int


@dataclass
class Var:
    name: str
    typ: str
    mutable: bool
    is_param: bool
    # für for-Schleifenvariablen: konstante exklusive Obergrenze
    # (erlaubt Eliminierung von Bereichsprüfungen, 7.5)
    max_exclusive: int | None = None


def is_strbuf(t: str) -> bool:
    return t.startswith("strbuf[")


def strbuf_cap(t: str) -> int:
    return int(t[len("strbuf["):-1])


def is_array(t: str) -> bool:
    return ("[" in t and not t.startswith("Result[")
            and not t.startswith("strbuf["))


def split_array(t: str) -> tuple[str, list[int]]:
    """'u8[2][3]' -> ('u8', [2, 3]) — nur für normalisierte Typen."""
    base = t[: t.index("[")]
    dims = [int(d) for d in re.findall(r"\[(\d+)\]", t)]
    return base, dims


def array_elem(t: str) -> str:
    base, dims = split_array(t)
    return base + "".join(f"[{d}]" for d in dims[1:])


@dataclass
class ConstInfo:
    name: str
    typ: str
    value: int | bool
    line: int
    col: int


@dataclass
class FieldInfo:
    name: str
    typ: str
    has_default: bool
    default_value: int | bool | None


@dataclass
class MethodSig:
    name: str
    struct: str
    self_mut: bool
    params: list[ast.Param]      # ohne self
    return_type: str | None


@dataclass
class StructInfo:
    name: str
    fields: list[FieldInfo] = field(default_factory=list)
    methods: dict[str, MethodSig] = field(default_factory=dict)
    decl: ast.StructDef | None = None


@dataclass
class VariantInfo:
    name: str
    enum: str
    params: list[ast.Param]


@dataclass
class EnumInfo:
    name: str
    variants: list[VariantInfo] = field(default_factory=list)
    decl: ast.EnumDef | None = None


def _cdiv(a: int, b: int) -> int:
    """C-Semantik: Rundung zur Null."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def _cmod(a: int, b: int) -> int:
    """C-Semantik: Rest hat Vorzeichen des Dividenden."""
    return a - _cdiv(a, b) * b


class Checker:
    def __init__(self, program: ast.Program, filename: str = "<eingabe>"):
        self.program = program
        self.filename = filename
        self.functions: dict[str, FnSig] = {}
        self.consts: dict[str, ConstInfo] = {}
        self.structs: dict[str, StructInfo] = {}
        self.enums: dict[str, EnumInfo] = {}
        self.variants: dict[str, VariantInfo] = {}  # global (flacher Namensraum)
        self.actors: dict[str, ast.ActorDef] = {}
        self.scopes: list[dict[str, Var]] = []
        self.current_fn: FnSig | MethodSig | None = None
        self.loop_depth = 0

    def error(self, message: str, node) -> CompileError:
        return CompileError(message, node.line, node.col, self.filename)

    # --- Einstieg -----------------------------------------------------------

    def check(self) -> ast.Program:
        self.collect_consts()
        self.collect_enums()
        self.collect_structs()
        self.collect_actors()
        self.collect_signatures()
        for top in self.program.toplevels:
            if isinstance(top, ast.FnDef):
                self.check_fn(top)
            elif isinstance(top, ast.StructDef):
                for m in top.methods:
                    self.check_method(top.name, m)
            elif isinstance(top, ast.ActorDef):
                for m in top.methods:
                    self.check_method(f"actor${top.name}", m)
        return self.program

    # --- Aktoren (§9, Rev. 2: Kopf-Argumente, fn receive) -----------------------

    def collect_actors(self) -> None:
        for top in self.program.toplevels:
            if not isinstance(top, ast.ActorDef):
                continue
            if (top.name in self.structs or top.name in self.enums
                    or top.name in self.consts or top.name in self.variants
                    or top.name in self.actors):
                raise self.error(f"'{top.name}' ist bereits deklariert", top)
            self.check_actor_head(top)
            hidden = f"actor${top.name}"  # unerreichbar aus Quelltext
            info = StructInfo(hidden, decl=None)
            self.structs[hidden] = info

            seen_fields: set[str] = set()
            for f in top.fields:
                if f.name in seen_fields:
                    raise self.error(f"Feld '{f.name}' kommt doppelt vor", f)
                seen_fields.add(f.name)
                f.typ = self.normalize_type(f.typ, f)
                self.require_type(f.typ, f)
                if f.typ in SCALAR_TYPES:
                    if f.default is None:
                        raise self.error(
                            f"Aktor-Zustand '{f.name}' braucht einen "
                            f"Startwert: '{f.name}: {f.typ} := ...'", f)
                    value = self.eval_const_expr(f.default, self.const_decls, [])
                    self.check_const_fits(value, f.typ, f.name, f.default)
                    f.default_value = value
                elif is_array(f.typ) or is_strbuf(f.typ):
                    if f.default is not None:
                        raise self.error(
                            "Arrays/Puffer im Aktor starten genullt — "
                            "kein ':=' möglich", f)
                else:
                    raise self.error(
                        f"Aktor-Zustand '{f.name}': Typ '{f.typ}' wird als "
                        "Zustand (noch) nicht unterstützt (Zahlen, bool, "
                        "Arrays, strbuf)", f)
                info.fields.append(FieldInfo(
                    f.name, f.typ, f.default is not None, f.default_value))

            has_receive = False
            for m in top.methods:
                if m.name in info.methods or m.name in seen_fields:
                    raise self.error(
                        f"'{m.name}' kommt in '{top.name}' doppelt vor", m)
                if m.name == "receive":
                    has_receive = True
                    self.setup_receive(top, m)
                else:
                    if not m.params or m.params[0].name != "self":
                        raise self.error(
                            f"Methode '{m.name}' braucht 'self' als ersten "
                            "Parameter (nur 'receive' bekommt es implizit)", m)
                    m.params[0].typ = hidden
                self.validate_params(m.params[1:], m)
                if m.return_type is not None:
                    self.require_type(m.return_type, m)
                info.methods[m.name] = MethodSig(
                    m.name, hidden, m.params[0].mutable, m.params[1:],
                    m.return_type)
            if not has_receive:
                raise self.error(
                    f"Aktor '{top.name}' braucht einen Handler: "
                    "fn receive(msg: <NachrichtenEnum>)", top)
            self.actors[top.name] = top

    def setup_receive(self, actor: ast.ActorDef, m: ast.FnDef) -> None:
        """fn receive(msg: MsgEnum) — self kommt implizit und veränderlich."""
        if m.params and m.params[0].name == "self":
            raise self.error(
                "receive bekommt 'self' implizit — Signatur ist "
                "fn receive(msg: <NachrichtenEnum>)", m)
        if len(m.params) != 1:
            raise self.error(
                "receive hat genau einen Parameter: die Nachricht", m)
        msg_p = m.params[0]
        if not self.is_enum(msg_p.typ):
            raise self.error(
                f"der Nachrichtentyp von receive muss ein enum sein, "
                f"'{msg_p.typ}' ist keines", msg_p)
        if m.return_type is not None:
            raise self.error("receive hat keinen Rückgabewert "
                             "(fire-and-forget, §9)", m)
        actor.msg_enum = msg_p.typ
        m.params.insert(0, ast.Param(
            "self", f"actor${actor.name}", m.line, m.col, mutable=True))

    def check_actor_head(self, top: ast.ActorDef) -> None:
        """actor Name(mailbox: N, full: Policy) — beide Pflicht, Namen prüft
        der Checker (offene Frage 8, beschlossen)."""
        given: dict[str, ast.Expr] = {}
        for n, arg in zip(top.head_names, top.head_args):
            if n is None:
                raise self.error(
                    "Aktor-Kopf braucht benannte Argumente: "
                    "actor Name(mailbox: 8, full: DropOldest)", top)
            if n not in ("mailbox", "full"):
                raise self.error(
                    f"unbekannte Aktor-Option '{n}' — es gibt 'mailbox' "
                    "und 'full'", arg)
            if n in given:
                raise self.error(f"Option '{n}' kommt doppelt vor", arg)
            given[n] = arg
        for required in ("mailbox", "full"):
            if required not in given:
                raise self.error(
                    f"Aktor-Option '{required}' fehlt — beide sind Pflicht "
                    "(kein Default, §9)", top)
        size = self.eval_const_expr(given["mailbox"], self.const_decls, [])
        if isinstance(size, bool) or not (1 <= size <= 255):
            raise self.error("mailbox: Größe muss 1..255 sein", given["mailbox"])
        top.mailbox_size = int(size)
        policy = given["full"]
        if not (isinstance(policy, ast.NameExpr)
                and policy.name in self.variants
                and self.variants[policy.name].enum == "MailboxPolicy"):
            raise self.error(
                "full: erwartet eine MailboxPolicy — Drop, DropOldest "
                "oder Panic", policy)
        top.policy = policy.name

    # --- Typen-Hilfen -----------------------------------------------------------

    def is_known_type(self, t: str) -> bool:
        if is_result(t) or is_strbuf(t) or t == "str":
            return True  # Teile/Kapazität prüft require_type
        if is_array(t):
            t = t[: t.index("[")]
        return t in SCALAR_TYPES or t in self.structs or t in self.enums

    def require_type(self, t: str, node) -> None:
        if t == "strbuf":
            raise self.error(
                "strbuf braucht eine Kapazität: strbuf[N]", node)
        if is_strbuf(t):
            cap = strbuf_cap(t)
            if cap not in self.program.strbuf_caps:
                self.program.strbuf_caps.append(cap)
            return
        if t == "str":
            return
        if is_result(t):
            ok_t, err_t = result_parts(t)
            for part in (ok_t, err_t):
                if is_result(part):
                    raise self.error(
                        "verschachtelte Results gibt es (noch) nicht", node)
                if is_array(part):
                    raise self.error(
                        "Arrays in Result gibt es (noch) nicht", node)
                self.require_type(part, node)
            if t not in self.program.result_types:
                self.program.result_types.append(t)
            return
        if not self.is_known_type(t):
            raise self.error(f"unbekannter Typ '{t}'", node)

    def normalize_type(self, t: str, node) -> str:
        """Löst Konstantennamen in Array-/Puffergrößen auf: 'u8[SIZE]' -> 'u8[32]'."""
        if is_result(t) or "[" not in t:
            return t
        base = t[: t.index("[")]
        dims = re.findall(r"\[([A-Za-z_0-9]+)\]", t)
        if base == "strbuf" and len(dims) != 1:
            raise self.error("strbuf hat genau eine Kapazität: strbuf[N]", node)
        if base == "str":
            raise self.error(
                "Arrays von str-Sichten gibt es (noch) nicht", node)
        resolved: list[int] = []
        for d in dims:
            if d.isdigit():
                n = int(d)
            elif d in self.consts:
                info = self.consts[d]
                if info.typ == "bool":
                    raise self.error(
                        f"Arraygröße '{d}' ist 'bool', keine Zahl", node)
                n = int(info.value)
            else:
                raise self.error(
                    f"Arraygröße '{d}' ist keine Konstante", node)
            if not (1 <= n <= 65535):
                raise self.error(
                    f"Arraygröße muss 1..65535 sein, ist {n}", node)
            resolved.append(n)
        return base + "".join(f"[{n}]" for n in resolved)

    def is_struct(self, t: str) -> bool:
        return t in self.structs

    def is_enum(self, t: str) -> bool:
        return t in self.enums

    def is_composite(self, t: str) -> bool:
        """Typen, die als Zeiger in Funktionen wandern (structs, enums, Result)."""
        return t in self.structs or t in self.enums or is_result(t)

    # --- Konstanten (const, nur oberste Ebene) --------------------------------

    def collect_consts(self) -> None:
        decls = {t.name: t for t in self.program.toplevels
                 if isinstance(t, ast.ConstDecl)}
        seen: set[str] = set()
        for top in self.program.toplevels:
            if isinstance(top, ast.ConstDecl):
                if top.name in seen:
                    raise self.error(f"'{top.name}' ist bereits deklariert", top)
                seen.add(top.name)
        # Verwendung vor Deklaration ist erlaubt -> rekursiv mit Zyklus-Erkennung
        for name in decls:
            self.eval_const_decl(name, decls, in_progress=[])
        self.const_decls = decls

    def eval_const_decl(self, name: str, decls: dict[str, ast.ConstDecl],
                        in_progress: list[str]) -> ConstInfo:
        if name in self.consts:
            return self.consts[name]
        decl = decls[name]
        if decl.typ not in SCALAR_TYPES:
            raise self.error(
                f"'const' braucht einen Zahlentyp oder 'bool', nicht '{decl.typ}'",
                decl)
        if name in in_progress:
            chain = " -> ".join(in_progress + [name])
            raise self.error(f"zyklische Konstanten-Definition: {chain}", decl)
        value = self.eval_const_expr(decl.value, decls, in_progress + [name])
        self.check_const_fits(value, decl.typ, name, decl.value)
        decl.const_value = value
        info = ConstInfo(name, decl.typ, value, decl.line, decl.col)
        self.consts[name] = info
        return info

    def check_const_fits(self, value: int | bool, typ: str, name: str, node) -> None:
        if typ == "bool":
            if not isinstance(value, bool):
                raise self.error(
                    f"'{name}' ist als 'bool' deklariert, der Wert ist eine Zahl",
                    node)
        else:
            if isinstance(value, bool):
                raise self.error(
                    f"'{name}' ist als '{typ}' deklariert, der Wert ist 'bool'",
                    node)
            lo, hi = RANGES[typ]
            if not (lo <= value <= hi):
                raise self.error(
                    f"Wert {value} passt nicht in '{typ}' ({lo} bis {hi})", node)

    def eval_const_expr(self, e: ast.Expr, decls: dict[str, ast.ConstDecl],
                        in_progress: list[str]) -> int | bool:
        """Faltung in unbegrenzten Ganzzahlen (Go-/Zig-Modell); nur das
        Endergebnis wird bereichsgeprüft (in eval_const_decl)."""
        if isinstance(e, ast.NumberLit):
            return e.value
        if isinstance(e, ast.BoolLit):
            return e.value
        if isinstance(e, ast.NameExpr):
            if e.name in decls:
                return self.eval_const_decl(e.name, decls, in_progress).value
            if e.name in {t.name for t in self.program.toplevels
                          if not isinstance(t, ast.ConstDecl)}:
                raise self.error(
                    f"'{e.name}' ist kein konstanter Ausdruck — in Konstanten "
                    "sind nur Literale, andere Konstanten, Arithmetik/Bits und "
                    "Casts erlaubt", e)
            raise self.error(f"unbekannter Name '{e.name}' — nicht deklariert", e)
        if isinstance(e, (ast.CallExpr, ast.MethodCall)):
            raise self.error(
                f"'{e.name}(...)' ist kein konstanter Ausdruck — "
                "Funktionsaufrufe sind in Konstanten nicht erlaubt", e)
        if isinstance(e, ast.UnaryOp):
            if e.op == "-":
                v = self.eval_const_expr(e.operand, decls, in_progress)
                if isinstance(v, bool):
                    raise self.error("unäres '-' erwartet einen Zahlentyp, gefunden 'bool'", e)
                return -v
            raise self.error(
                f"'{e.op}' ist in Konstanten nicht erlaubt "
                "(nur Arithmetik/Bits/Casts)", e)
        if isinstance(e, ast.BinaryOp):
            if e.op not in ("+", "-", "*", "//", "mod", "&", "|", "^", "<<", ">>"):
                raise self.error(
                    f"'{e.op}' ist in Konstanten nicht erlaubt "
                    "(nur Arithmetik/Bits/Casts)", e)
            a = self.eval_const_expr(e.left, decls, in_progress)
            b = self.eval_const_expr(e.right, decls, in_progress)
            if isinstance(a, bool) or isinstance(b, bool):
                raise self.error(f"'{e.op}' erwartet Zahlentypen, gefunden 'bool'", e)
            if e.op in ("//", "mod") and b == 0:
                raise self.error("Division durch 0 im konstanten Ausdruck", e)
            if e.op in ("<<", ">>") and b < 0:
                raise self.error("negative Shift-Weite im konstanten Ausdruck", e)
            ops = {
                "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                "//": lambda: _cdiv(a, b), "mod": lambda: _cmod(a, b),
                "&": lambda: a & b, "|": lambda: a | b, "^": lambda: a ^ b,
                "<<": lambda: a << b, ">>": lambda: a >> b,
            }
            return ops[e.op]()
        if isinstance(e, ast.CastExpr):
            v = self.eval_const_expr(e.expr, decls, in_progress)
            if e.target == "bool":
                raise self.error(
                    "Cast nach 'bool' ist nicht erlaubt — verwende einen "
                    "Vergleich wie '<> 0'", e)
            if e.target not in SCALAR_TYPES:
                raise self.error(f"Cast nach '{e.target}' ist nicht erlaubt", e)
            if isinstance(v, bool):
                return int(v)
            lo, hi = RANGES[e.target]
            if not (lo <= v <= hi):
                raise self.error(
                    f"Wert {v} passt nicht in '{e.target}' ({lo} bis {hi}) — "
                    "zum Abschneiden Bitmaske verwenden (z. B. '& 0xFF')", e)
            return v
        raise self.error("kein konstanter Ausdruck", e)

    # --- enums -------------------------------------------------------------------

    def collect_enums(self) -> None:
        # Prelude zuerst (StrbufError, MailboxPolicy) — Nutzer-Deklarationen
        # mit gleichen Namen laufen in die normale Kollisionsprüfung
        for pe in PRELUDE_ENUMS:
            info = EnumInfo(pe.name, decl=pe)
            for v in pe.variants:
                vi = VariantInfo(v.name, pe.name, v.params)
                info.variants.append(vi)
                self.variants[v.name] = vi
            self.enums[pe.name] = info
        for top in self.program.toplevels:
            if not isinstance(top, ast.EnumDef):
                continue
            if top.name in self.enums or top.name in self.consts:
                raise self.error(f"'{top.name}' ist bereits deklariert", top)
            if top.name in SCALAR_TYPES:
                raise self.error(f"'{top.name}' ist ein eingebauter Typ", top)
            info = EnumInfo(top.name, decl=top)
            if len(top.variants) > 255:
                raise self.error("enum hat mehr als 255 Varianten", top)
            for v in top.variants:
                # flacher Namensraum: Variantennamen sind global eindeutig,
                # damit 'Stop' ohne Kontext auflösbar ist (C-enum-Modell)
                if v.name in self.variants:
                    other = self.variants[v.name].enum
                    raise self.error(
                        f"Variante '{v.name}' gibt es bereits in '{other}' — "
                        "Variantennamen sind global eindeutig", v)
                if v.name in self.consts:
                    raise self.error(f"'{v.name}' ist bereits deklariert", v)
                for p in v.params:
                    if p.mutable:
                        raise self.error(
                            "'mut' hat in Varianten-Feldern nichts zu suchen", p)
                vi = VariantInfo(v.name, top.name, v.params)
                info.variants.append(vi)
                self.variants[v.name] = vi
            self.enums[top.name] = info
        # Feldtypen der Varianten prüfen (erst wenn alle Typnamen bekannt sind
        # — structs werden gleich danach gesammelt, deshalb hier nur vormerken)

    def validate_enum_payloads(self) -> None:
        for info in self.enums.values():
            for v in info.variants:
                seen: set[str] = set()
                for p in v.params:
                    if p.name in seen:
                        raise self.error(f"Feld '{p.name}' kommt doppelt vor", p)
                    seen.add(p.name)
                    if is_array(p.typ):
                        raise self.error(
                            "Array-Felder in Varianten kommen später "
                            "(brauchen Array-Literale)", p)
                    self.require_type(p.typ, p)

    # --- structs -----------------------------------------------------------------

    def collect_structs(self) -> None:
        # Pass 1: Namen registrieren (Felder dürfen auf später deklarierte
        # structs verweisen)
        for top in self.program.toplevels:
            if not isinstance(top, ast.StructDef):
                continue
            if (top.name in self.structs or top.name in self.consts
                    or top.name in self.enums or top.name in self.variants):
                raise self.error(f"'{top.name}' ist bereits deklariert", top)
            if top.name in SCALAR_TYPES:
                raise self.error(f"'{top.name}' ist ein eingebauter Typ", top)
            self.structs[top.name] = StructInfo(top.name, decl=top)

        # Pass 2: Felder, Defaults, Methoden-Signaturen
        for top in self.program.toplevels:
            if not isinstance(top, ast.StructDef):
                continue
            info = self.structs[top.name]
            seen_fields: set[str] = set()
            for f in top.fields:
                if f.name in seen_fields:
                    raise self.error(f"Feld '{f.name}' kommt doppelt vor", f)
                seen_fields.add(f.name)
                if is_array(f.typ):
                    raise self.error(
                        "Array-Felder in structs kommen später "
                        "(brauchen Array-Literale)", f)
                if is_result(f.typ):
                    raise self.error(
                        "Result-Felder in structs gibt es (noch) nicht", f)
                if is_strbuf(f.typ):
                    raise self.error(
                        "strbuf-Felder in structs gibt es (noch) nicht", f)
                self.require_type(f.typ, f)
                default_value: int | bool | None = None
                if f.default is not None:
                    if f.typ not in SCALAR_TYPES:
                        raise self.error(
                            "Default-Werte gibt es nur für Zahlentypen und "
                            "'bool'", f)
                    default_value = self.eval_const_expr(
                        f.default, self.const_decls, [])
                    self.check_const_fits(default_value, f.typ, f.name, f.default)
                    f.default_value = default_value
                info.fields.append(FieldInfo(
                    f.name, f.typ, f.default is not None, default_value))
            for m in top.methods:
                if m.name in info.methods or m.name in seen_fields:
                    raise self.error(
                        f"'{m.name}' kommt in '{top.name}' doppelt vor", m)
                if not m.params or m.params[0].name != "self":
                    raise self.error(
                        f"Methode '{m.name}' braucht 'self' als ersten "
                        "Parameter", m)
                self_param, rest = m.params[0], m.params[1:]
                self_param.typ = top.name
                self.validate_params(rest, m)
                if m.return_type is not None:
                    self.require_type(m.return_type, m)
                info.methods[m.name] = MethodSig(
                    m.name, top.name, self_param.mutable, rest, m.return_type)

        # Varianten-Feldtypen jetzt prüfbar (alle Typnamen bekannt)
        self.validate_enum_payloads()

        # Pass 3: keine rekursiven Typen (unendliche Größe) — über structs
        # UND enums hinweg (Variante kann struct enthalten und umgekehrt)
        for name in list(self.structs) + list(self.enums):
            self.check_type_recursion(name, [])

    def contained_types(self, name: str) -> list[str]:
        if name in self.structs:
            return [f.typ for f in self.structs[name].fields]
        if name in self.enums:
            return [p.typ for v in self.enums[name].variants for p in v.params]
        return []

    def check_type_recursion(self, name: str, chain: list[str]) -> None:
        if name in chain:
            cycle = " -> ".join(chain[chain.index(name):] + [name])
            decl = (self.structs.get(name) or self.enums.get(name)).decl
            raise self.error(f"Typ enthält sich selbst: {cycle}", decl)
        for t in self.contained_types(name):
            if self.is_composite(t):
                self.check_type_recursion(t, chain + [name])

    def validate_params(self, params: list[ast.Param], node) -> None:
        seen: set[str] = set()
        for p in params:
            if p.name in seen:
                raise self.error(f"Parameter '{p.name}' kommt doppelt vor", p)
            seen.add(p.name)
            p.typ = self.normalize_type(p.typ, p)
            self.require_type(p.typ, p)
            if p.mutable and (p.typ in SCALAR_TYPES or p.typ == "str"):
                raise self.error(
                    f"'mut' bei Parameter '{p.name}' ist wirkungslos — "
                    f"'{p.typ}' wird kopiert (mut gibt es nur für structs, "
                    "enums und Arrays/Puffer)", p)

    # --- Funktions-Signaturen ------------------------------------------------------

    def collect_signatures(self) -> None:
        for top in self.program.toplevels:
            if isinstance(top, (ast.ConstDecl, ast.StructDef, ast.EnumDef,
                                ast.ActorDef)):
                continue
            if top.name in self.actors:
                raise self.error(
                    f"'{top.name}' ist bereits als Aktor deklariert", top)
            if top.name in self.consts:
                raise self.error(
                    f"'{top.name}' ist bereits als Konstante deklariert", top)
            if top.name in self.structs or top.name in self.enums:
                raise self.error(
                    f"'{top.name}' ist bereits als Typ deklariert", top)
            if top.name in self.variants:
                raise self.error(
                    f"'{top.name}' ist bereits eine enum-Variante", top)
            if top.name in self.functions:
                raise self.error(f"'{top.name}' ist bereits deklariert", top)
            self.validate_params(top.params, top)
            if top.return_type is not None:
                if is_array(top.return_type) or is_strbuf(top.return_type):
                    raise self.error(
                        "Arrays/Puffer können nicht zurückgegeben werden — "
                        "als 'mut'-Parameter füllen", top)
                self.require_type(top.return_type, top)
            self.functions[top.name] = FnSig(
                top.name, top.params, top.return_type,
                isinstance(top, ast.ExternDecl), top.line, top.col,
            )

        for name in ("Result", "Ok", "Err"):
            if name in self.functions or name in self.structs \
                    or name in self.enums or name in self.consts \
                    or name in self.variants:
                bad = (self.functions.get(name) or self.structs.get(name)
                       or self.enums.get(name))
                raise CompileError(
                    f"'{name}' ist vordefiniert (Result-Typen) und kann nicht "
                    "neu deklariert werden", 1, 1, self.filename)

        for top in self.program.toplevels:
            if isinstance(top, ast.ExternDecl):
                if is_result(top.return_type or "") or \
                        any(is_result(p.typ) for p in top.params):
                    raise self.error(
                        "Result in 'extern fn' ist nicht erlaubt — die C-Seite "
                        "kennt den Typ nicht", top)

        main = self.functions.get("main")
        if main is None or main.is_extern:
            raise CompileError(
                "genau eine 'fn main()' ohne Parameter und Rückgabetyp wird benötigt",
                1, 1, self.filename,
            )
        if main.params:
            raise CompileError("'fn main()' darf keine Parameter haben",
                               main.line, main.col, self.filename)
        if main.return_type is not None:
            raise CompileError("'fn main()' darf keinen Rückgabetyp haben",
                               main.line, main.col, self.filename)

    # --- Funktionen und Methoden ----------------------------------------------------

    def check_fn(self, fn: ast.FnDef) -> None:
        self.current_fn = self.functions[fn.name]
        self.enter_fn_scope(fn.params)
        self.check_block(fn.body, new_scope=False)
        if fn.return_type is not None and not self.always_returns(fn.body):
            raise self.error(
                f"'{fn.name}' gibt '{fn.return_type}' zurück, "
                "aber nicht alle Pfade returnen", fn,
            )
        self.current_fn = None

    def check_method(self, struct_name: str, m: ast.FnDef) -> None:
        sig = self.structs[struct_name].methods[m.name]
        self.current_fn = sig
        self.enter_fn_scope(m.params)
        self.check_block(m.body, new_scope=False)
        if m.return_type is not None and not self.always_returns(m.body):
            raise self.error(
                f"'{m.name}' gibt '{m.return_type}' zurück, "
                "aber nicht alle Pfade returnen", m,
            )
        self.current_fn = None

    def enter_fn_scope(self, params: list[ast.Param]) -> None:
        self.loop_depth = 0
        scope: dict[str, Var] = {}
        for p in params:
            if p.name in self.consts:
                raise self.error(
                    f"'{p.name}' ist bereits als Konstante deklariert", p)
            scope[p.name] = Var(p.name, p.typ, mutable=p.mutable, is_param=True)
        self.scopes = [scope]

    # --- Anweisungen ------------------------------------------------------------

    def check_block(self, block: ast.Block, new_scope: bool = True) -> None:
        if new_scope:
            self.scopes.append({})
        for stmt in block.statements:
            self.check_stmt(stmt)
        if new_scope:
            self.scopes.pop()

    def check_stmt(self, stmt: ast.Stmt) -> None:
        if isinstance(stmt, ast.LetStmt):
            self.check_let(stmt)
        elif isinstance(stmt, ast.AssignStmt):
            self.check_assign(stmt)
        elif isinstance(stmt, ast.CallStmt):
            if isinstance(stmt.call, ast.QuestionExpr):
                # z. B. line.append(s)? — Ok-Wert wird bewusst verworfen
                self.check_question(stmt.call)
            elif isinstance(stmt.call, ast.MethodCall):
                self.check_method_call(stmt.call)
            else:
                self.check_call(stmt.call)
                if stmt.call.callee_kind == "struct":
                    raise self.error(
                        "Konstruktion ohne Verwendung — Ergebnis einer "
                        "Konstruktion muss zugewiesen werden", stmt)
                if stmt.call.callee_kind in ("result_ok", "result_err"):
                    raise self.error(
                        "Ok/Err ohne Verwendung — das Result muss zugewiesen "
                        "oder zurückgegeben werden", stmt)
        elif isinstance(stmt, ast.IfStmt):
            for cond, body in stmt.branches:
                self.check_condition(cond)
                self.check_block(body)
            if stmt.else_block is not None:
                self.check_block(stmt.else_block)
        elif isinstance(stmt, ast.WhileStmt):
            self.check_condition(stmt.cond)
            self.loop_depth += 1
            self.check_block(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ast.LoopStmt):
            self.loop_depth += 1
            self.check_block(stmt.body)
            self.loop_depth -= 1
        elif isinstance(stmt, ast.RepeatStmt):
            self.loop_depth += 1
            self.check_block(stmt.body)
            self.loop_depth -= 1
            # Bedingung NACH dem Block prüfen, aber außerhalb seines Scopes:
            # until sieht nur Namen, die die Schleife überleben (Ausgangsebene).
            self.check_condition(stmt.cond)
        elif isinstance(stmt, ast.BreakStmt):
            if self.loop_depth == 0:
                raise self.error("'break' ist nur innerhalb von 'while'/'loop' erlaubt", stmt)
        elif isinstance(stmt, ast.ContinueStmt):
            if self.loop_depth == 0:
                raise self.error(
                    "'continue' ist nur innerhalb von 'while'/'loop' erlaubt", stmt)
        elif isinstance(stmt, ast.ReturnStmt):
            self.check_return(stmt)
        elif isinstance(stmt, ast.MatchStmt):
            self.check_match(stmt)
        elif isinstance(stmt, ast.ForStmt):
            self.check_for(stmt)
        elif isinstance(stmt, ast.SendStmt):
            self.check_send(stmt)
        else:  # pragma: no cover
            raise AssertionError(f"unbekannte Anweisung {stmt!r}")

    def check_send(self, stmt: ast.SendStmt) -> None:
        actor = self.actors.get(stmt.target)
        if actor is None:
            hint = ""
            if stmt.target in self.structs or stmt.target in self.enums:
                hint = " — nur Aktoren empfangen Nachrichten"
            raise self.error(f"unbekannter Aktor '{stmt.target}'{hint}", stmt)
        assert actor.msg_enum is not None
        t = self.check_expr(stmt.msg)
        self.coerce(stmt.msg, t, actor.msg_enum,
                    f"Nachricht an '{stmt.target}'")
        stmt.enum_name = actor.msg_enum

    def check_for(self, stmt: ast.ForStmt) -> None:
        if stmt.var in self.consts:
            raise self.error(
                f"'{stmt.var}' ist bereits als Konstante deklariert", stmt)
        ts = self.check_expr(stmt.start)
        te = self.check_expr(stmt.end)
        for t, side in ((ts, stmt.start), (te, stmt.end)):
            if t != UNTYPED and t not in INT_TYPES:
                raise self.error(
                    f"Bereichsgrenze muss ein Zahlentyp sein, ist '{t}'", side)
        if ts == UNTYPED and te == UNTYPED:
            var_type = "u16"  # Default für rein literale Bereiche
            self.finalize(stmt.start, var_type)
            self.finalize(stmt.end, var_type)
        elif ts == UNTYPED:
            self.finalize(stmt.start, te)
            var_type = te
        elif te == UNTYPED:
            self.finalize(stmt.end, ts)
            var_type = ts
        elif ts != te:
            raise self.error(
                f"Typkonflikt im Bereich: '{ts}' und '{te}' — "
                "keine impliziten Umwandlungen", stmt)
        else:
            var_type = ts
        stmt.var_type = var_type

        # konstantes Ende -> Obergrenze für Check-Eliminierung (7.5)
        end_const: int | None = None
        e = stmt.end
        if isinstance(e, ast.NumberLit):
            end_const = e.value
        elif isinstance(e, ast.NameExpr) and e.name in self.consts \
                and self.lookup_var(e.name) is None:
            end_const = int(self.consts[e.name].value)
        elif getattr(e, "const_value", None) is not None:
            end_const = e.const_value
        if var_type not in ("u8", "u16", "u32"):
            # signierte Schleifenvariable kann negativ starten -> die
            # Obergrenze allein beweist keinen gültigen Index
            end_const = None
        stmt.end_const = end_const

        self.scopes.append({
            stmt.var: Var(stmt.var, var_type, mutable=False, is_param=False,
                          max_exclusive=end_const)
        })
        self.loop_depth += 1
        self.check_block(stmt.body, new_scope=False)
        self.loop_depth -= 1
        self.scopes.pop()

    def check_match(self, stmt: ast.MatchStmt) -> None:
        t = self.check_expr(stmt.expr)
        if is_result(t):
            self.check_match_result(stmt, t)
            return
        if not self.is_enum(t):
            raise self.error(
                f"match braucht ein enum oder Result, gefunden {show_type(t)}",
                stmt)
        info = self.enums[t]
        stmt.enum_name = t
        by_name = {v.name: v for v in info.variants}
        seen: set[str] = set()
        for arm in stmt.arms:
            v = by_name.get(arm.variant)
            if v is None:
                raise self.error(
                    f"'{arm.variant}' ist keine Variante von '{t}'", arm)
            if arm.variant in seen:
                raise self.error(f"Variante '{arm.variant}' kommt doppelt vor", arm)
            seen.add(arm.variant)
            if len(arm.bindings) != len(v.params):
                if not v.params:
                    raise self.error(
                        f"'{arm.variant}' ist datenlos — Muster ohne Klammern "
                        "schreiben", arm)
                raise self.error(
                    f"'{arm.variant}' hat {len(v.params)} Feld(er), "
                    f"{len(arm.bindings)} Name(n) gebunden", arm)
            arm.binding_types = [p.typ for p in v.params]
            self.scopes.append({})
            for name, p in zip(arm.bindings, v.params):
                if name in self.consts:
                    raise self.error(
                        f"'{name}' ist bereits als Konstante deklariert", arm)
                self.scopes[-1][name] = Var(name, p.typ, mutable=False,
                                            is_param=False)
            self.check_block(arm.body, new_scope=False)
            self.scopes.pop()
        missing = [v.name for v in info.variants if v.name not in seen]
        if missing:
            raise self.error(
                f"match deckt nicht alle Varianten von '{t}' ab — "
                f"es fehlt: {', '.join(missing)}", stmt)

    def check_match_result(self, stmt: ast.MatchStmt, t: str) -> None:
        """match über Result[T,E]: genau die Zweige Ok(x) und Err(e)."""
        ok_t, err_t = result_parts(t)
        stmt.enum_name = t
        seen: set[str] = set()
        for arm in stmt.arms:
            if arm.variant not in ("Ok", "Err"):
                raise self.error(
                    f"'{arm.variant}' ist kein Result-Zweig — erlaubt sind "
                    "Ok(wert) und Err(fehler)", arm)
            if arm.variant in seen:
                raise self.error(f"'{arm.variant}' kommt doppelt vor", arm)
            seen.add(arm.variant)
            if len(arm.bindings) != 1:
                raise self.error(
                    f"'{arm.variant}' bindet genau einen Namen, "
                    f"{len(arm.bindings)} angegeben", arm)
            typ = ok_t if arm.variant == "Ok" else err_t
            arm.binding_types = [typ]
            self.scopes.append({
                arm.bindings[0]: Var(arm.bindings[0], typ, mutable=False,
                                     is_param=False)
            })
            self.check_block(arm.body, new_scope=False)
            self.scopes.pop()
        if len(seen) != 2:
            missing = {"Ok", "Err"} - seen
            raise self.error(
                f"match über Result braucht beide Zweige — es fehlt: "
                f"{', '.join(sorted(missing))}", stmt)

    def check_let(self, stmt: ast.LetStmt) -> None:
        if stmt.name in self.consts:
            # verhindert auch die #define-Falle im Codegen (ml_-Makro würde
            # die lokale Deklaration texteuell ersetzen)
            raise self.error(
                f"'{stmt.name}' ist bereits als Konstante deklariert", stmt)
        if stmt.name in self.structs or stmt.name in self.enums:
            raise self.error(f"'{stmt.name}' ist ein Typ, kein Variablenname", stmt)
        if stmt.name in self.scopes[-1]:
            raise self.error(f"'{stmt.name}' ist in diesem Scope bereits deklariert", stmt)
        if stmt.declared_type is not None:
            stmt.declared_type = self.normalize_type(stmt.declared_type, stmt)
            self.require_type(stmt.declared_type, stmt)

        # Array-/Puffer-Deklaration ohne Initialisierer (Null-Init)
        if stmt.value is None:
            assert stmt.declared_type is not None
            assert is_array(stmt.declared_type) or is_strbuf(stmt.declared_type)
            self.scopes[-1][stmt.name] = Var(
                stmt.name, stmt.declared_type, stmt.mutable, is_param=False)
            return
        if stmt.declared_type is not None and is_array(stmt.declared_type):
            raise self.error(
                "Array-Literale gibt es (noch) nicht — Deklaration ohne ':=' "
                "legt ein genulltes Array an", stmt)

        if isinstance(stmt.value, ast.QuestionExpr):
            t = self.check_question(stmt.value)
        else:
            t = self.check_expr(stmt.value)
        if is_array(t) or is_strbuf(t):
            raise self.error(
                "Arrays/Puffer können nicht am Stück kopiert werden — "
                "elementweise oder als 'mut'-Parameter übergeben", stmt)
        if stmt.declared_type is not None:
            self.coerce(stmt.value, t, stmt.declared_type,
                        f"'{stmt.name}' ist als '{stmt.declared_type}' deklariert")
            typ = stmt.declared_type
        else:
            if t in PSEUDO_TYPES:
                raise self.error(
                    "Typ nicht ableitbar — Typannotation nötig, "
                    f"z. B. 'let {stmt.name}: u16 := ...' bzw. "
                    f"'let {stmt.name}: Result[u16, Fehler] := ...'", stmt,
                )
            typ = t
        self.scopes[-1][stmt.name] = Var(stmt.name, typ, stmt.mutable, is_param=False)

    def check_assign(self, stmt: ast.AssignStmt) -> None:
        target = stmt.target
        if isinstance(target, ast.NameExpr):
            var = self.lookup_var(target.name)
            if var is None:
                if target.name in self.consts:
                    raise self.error(
                        f"Konstante '{target.name}' kann nicht zugewiesen werden — "
                        "Konstanten sind unveränderlich", stmt)
                raise self.error(
                    f"unbekannter Name '{target.name}' — nicht deklariert", stmt)
            self.require_mutable(var, stmt)
            target.typ = var.typ
            target_type = var.typ
        elif isinstance(target, (ast.FieldAccess, ast.IndexExpr)):
            target_type = self.check_expr(target)
            root = self.root_var(target)
            if root is not None:
                self.require_mutable(root, stmt)
        else:  # pragma: no cover — Parser lässt nur lvalues durch
            raise self.error("linke Seite ist nicht zuweisbar", stmt)

        if is_array(target_type) or is_strbuf(target_type):
            raise self.error(
                "Arrays/Puffer können nicht am Stück zugewiesen werden", stmt)
        if isinstance(stmt.target, ast.IndexExpr) \
                and stmt.target.recv.typ in ("str",) :
            raise self.error(
                "str ist eine unveränderliche Sicht — schreiben geht nur "
                "über einen strbuf", stmt)
        if isinstance(stmt.target, ast.IndexExpr) \
                and stmt.target.recv.typ is not None \
                and is_strbuf(stmt.target.recv.typ):
            raise self.error(
                "strbuf wächst über append(...) — Elemente werden nicht "
                "direkt beschrieben", stmt)

        if stmt.op != ":=":
            if target_type == "bool":
                raise self.error(
                    f"'{stmt.op}' erwartet einen Zahlentyp, Ziel ist 'bool'", stmt)
            if self.is_composite(target_type):
                raise self.error(
                    f"'{stmt.op}' ist für structs/enums nicht definiert", stmt)
        if isinstance(stmt.value, ast.QuestionExpr):
            t = self.check_question(stmt.value)
        else:
            t = self.check_expr(stmt.value)
        self.coerce(stmt.value, t, target_type, f"Ziel hat Typ '{target_type}'")
        stmt.target_type = target_type

    def check_question(self, e: ast.QuestionExpr) -> str:
        """expr? — entpackt Ok oder reicht Err an den Aufrufer durch (§4)."""
        t = self.check_expr(e.operand)
        if not is_result(t):
            raise self.error(
                f"'?' braucht ein Result, gefunden {show_type(t)}", e)
        assert self.current_fn is not None
        rt = self.current_fn.return_type
        if rt is None or not is_result(rt):
            raise self.error(
                "'?' reicht den Fehler an den Aufrufer durch — die Funktion "
                "muss dafür 'Result[...]' zurückgeben", e)
        _, err_t = result_parts(t)
        _, fn_err = result_parts(rt)
        if err_t != fn_err:
            raise self.error(
                f"Fehlertyp passt nicht: '?' auf 'Result[..., {err_t}]' in "
                f"einer Funktion mit 'Result[..., {fn_err}]'", e)
        ok_t, _ = result_parts(t)
        e.typ = ok_t
        return ok_t

    def require_mutable(self, var: Var, node) -> None:
        if var.mutable:
            return
        if var.name == "self":
            raise self.error(
                "Methode liest nur — für schreibende Methoden 'mut self' "
                "deklarieren", node)
        if var.is_param:
            raise self.error(
                f"Parameter '{var.name}' ist eine Lese-Sicht — wer schreiben "
                "will, deklariert 'mut' auf beiden Seiten (§7.2)", node)
        raise self.error(
            f"'{var.name}' ist nicht veränderlich — deklariere 'let mut {var.name}'",
            node)

    def root_var(self, e: ast.Expr) -> Var | None:
        """Wurzel-Variable eines lvalue-Ausdrucks (für mut-Prüfungen)."""
        while isinstance(e, (ast.FieldAccess, ast.IndexExpr)):
            e = e.recv
        if isinstance(e, ast.NameExpr):
            return self.lookup_var(e.name)
        return None

    def is_lvalue(self, e: ast.Expr) -> bool:
        if isinstance(e, ast.NameExpr):
            return not e.is_variant  # nackte Variante ist ein Wert, kein Ort
        return isinstance(e, (ast.FieldAccess, ast.IndexExpr))

    def check_condition(self, cond: ast.Expr) -> None:
        t = self.check_expr(cond)
        if t != "bool":
            raise self.error(
                f"Bedingung muss 'bool' sein, ist {show_type(t)} — "
                "meintest du '... <> 0'?", cond)

    def check_return(self, stmt: ast.ReturnStmt) -> None:
        assert self.current_fn is not None
        fn = self.current_fn
        if fn.return_type is None:
            if stmt.value is not None:
                raise self.error(
                    f"'{fn.name}' hat keinen Rückgabetyp — 'return' ohne Wert verwenden",
                    stmt,
                )
            return
        if stmt.value is None:
            raise self.error(
                f"'{fn.name}' gibt '{fn.return_type}' zurück — 'return' braucht einen Wert",
                stmt,
            )
        if isinstance(stmt.value, ast.QuestionExpr):
            raise self.error(
                "'return f()?' ist nicht erlaubt — entweder 'return f()' "
                "(Result direkt durchreichen) oder erst 'let x := f()?'", stmt)
        t = self.check_expr(stmt.value)
        self.coerce(stmt.value, t, fn.return_type,
                    f"'{fn.name}' gibt '{fn.return_type}' zurück")

    # --- Ausdrücke ------------------------------------------------------------

    def check_expr(self, e: ast.Expr) -> str:
        if isinstance(e, ast.NumberLit):
            if e.typ is not None:
                return e.typ  # Byte-Literal 'A' ist fest u8 (§6.4)
            return UNTYPED
        if isinstance(e, ast.StrLit):
            return "str"
        if isinstance(e, ast.BoolLit):
            e.typ = "bool"
            return "bool"
        if isinstance(e, ast.NameExpr):
            var = self.lookup_var(e.name)
            if var is None:
                if e.name in self.consts:
                    typ = self.consts[e.name].typ
                    e.typ = typ
                    return typ
                if e.name in self.variants:
                    v = self.variants[e.name]
                    if v.params:
                        raise self.error(
                            f"Variante '{e.name}' trägt Daten — Konstruktion "
                            f"mit '{e.name}(...)'", e)
                    e.typ = v.enum
                    e.is_variant = True
                    return v.enum
                if e.name in self.functions:
                    raise self.error(
                        f"'{e.name}' ist eine Funktion — Aufruf mit '{e.name}(...)'", e)
                if e.name in self.actors:
                    raise self.error(
                        f"'{e.name}' ist ein Aktor — sein Zustand ist isoliert "
                        f"(§9); Nachrichten senden mit 'msg ->> {e.name}'", e)
                if e.name in self.structs or e.name in self.enums:
                    raise self.error(
                        f"'{e.name}' ist ein Typ — Konstruktion mit '{e.name}(...)'", e)
                raise self.error(f"unbekannter Name '{e.name}' — nicht deklariert", e)
            e.typ = var.typ
            return var.typ
        if isinstance(e, ast.FieldAccess):
            return self.check_field_access(e)
        if isinstance(e, ast.IndexExpr):
            return self.check_index(e)
        if isinstance(e, ast.MethodCall):
            t = self.check_method_call(e)
            if t is None:
                raise self.error(
                    f"'{e.name}' hat keinen Rückgabewert und kann nicht "
                    "in einem Ausdruck stehen", e)
            return t
        if isinstance(e, ast.QuestionExpr):
            raise self.error(
                "'?' ist nur als komplette rechte Seite erlaubt — "
                "z. B. 'let x := f()?' oder 'buf.append(s)?'", e)
        if isinstance(e, ast.CallExpr):
            sig = self.check_call(e)
            if e.callee_kind == "result_ok":
                return OK_PSEUDO
            if e.callee_kind == "result_err":
                return ERR_PSEUDO
            if e.callee_kind in ("struct", "enum"):
                return e.typ  # Konstruktion
            assert sig is not None
            if sig.return_type is None:
                raise self.error(
                    f"'{e.name}' hat keinen Rückgabewert und kann nicht "
                    "in einem Ausdruck stehen", e,
                )
            e.typ = sig.return_type
            return sig.return_type
        if isinstance(e, ast.UnaryOp):
            return self.check_unary(e)
        if isinstance(e, ast.BinaryOp):
            return self.check_binary(e)
        if isinstance(e, ast.CastExpr):
            return self.check_cast(e)
        raise AssertionError(f"unbekannter Ausdruck {e!r}")  # pragma: no cover

    def check_field_access(self, e: ast.FieldAccess) -> str:
        t = self.check_expr(e.recv)
        if self.is_enum(t):
            raise self.error(
                f"'{t}' ist ein enum — Daten werden über 'match' zerlegt, "
                "nicht über Feldzugriff", e)
        if not self.is_struct(t):
            shown = "Zahlliteral" if t == UNTYPED else f"'{t}'"
            raise self.error(f"Feldzugriff '.{e.name}' braucht ein struct, "
                             f"gefunden {shown}", e)
        info = self.structs[t]
        for f in info.fields:
            if f.name == e.name:
                e.typ = f.typ
                return f.typ
        if e.name in info.methods:
            raise self.error(
                f"'{e.name}' ist eine Methode von '{t}' — Aufruf mit "
                f"'.{e.name}(...)'", e)
        raise self.error(f"'{t}' hat kein Feld '{e.name}'", e)

    def check_index(self, e: ast.IndexExpr) -> str:
        """Bereichsprüfung als Standard, Eliminierung beweisbar sicherer
        Zugriffe (Abschnitt 7.5)."""
        t = self.check_expr(e.recv)
        if t == "str" or is_strbuf(t):
            # s[i] -> u8; Länge ist ein Laufzeitwert -> Check immer (7.5)
            it = self.check_expr(e.index)
            if it == UNTYPED:
                self.finalize(e.index, "u16")
            elif it not in INT_TYPES:
                raise self.error(
                    f"Index muss ein Zahlentyp sein, ist {show_type(it)}", e)
            e.needs_check = True
            e.length = 0  # Laufzeitlänge
            e.typ = "u8"
            return "u8"
        if not is_array(t):
            raise self.error(
                f"Indexierung braucht ein Array, gefunden {show_type(t)}", e)
        _, dims = split_array(t)
        e.length = dims[0]
        elem = array_elem(t)

        it = self.check_expr(e.index)
        if it == UNTYPED:
            # konstanter Index: zur Compile-Zeit prüfen, kein Laufzeit-Check
            v = self.fold(e.index)
            if not (0 <= v < e.length):
                raise self.error(
                    f"Index {v} außerhalb des Bereichs 0..{e.length - 1}", e)
            self.finalize(e.index, "u16")
            e.needs_check = False
        else:
            if it not in INT_TYPES:
                raise self.error(f"Index muss ein Zahlentyp sein, ist '{it}'", e)
            e.needs_check = True
            if isinstance(e.index, ast.NameExpr):
                var = self.lookup_var(e.index.name)
                if var is not None:
                    # for-Schleifenvariable mit bewiesener Obergrenze
                    if (var.max_exclusive is not None
                            and var.max_exclusive <= e.length):
                        e.needs_check = False
                elif e.index.name in self.consts:
                    cv = int(self.consts[e.index.name].value)
                    if not (0 <= cv < e.length):
                        raise self.error(
                            f"Index {cv} außerhalb des Bereichs "
                            f"0..{e.length - 1}", e)
                    e.needs_check = False
        e.typ = elem
        return elem

    STRBUF_METHODS = {
        # name -> (Parametertypen, schreibt?, Ergebnis; None = void)
        "append": (("str",), True, "Result[u16,StrbufError]"),
        "append_u8": (("u8",), True, "Result[u16,StrbufError]"),
        "append_u16": (("u16",), True, "Result[u16,StrbufError]"),
        "append_u32": (("u32",), True, "Result[u16,StrbufError]"),
        "clear": ((), True, None),
        "len": ((), False, "u16"),
    }

    def check_method_call(self, e: ast.MethodCall) -> str | None:
        recv_t = self.check_expr(e.recv)
        if recv_t == "str":
            if e.name != "len":
                raise self.error(
                    f"'str' hat nur die Methode len(), nicht '{e.name}'", e)
            if e.args:
                raise self.error("len() nimmt keine Argumente", e)
            e.typ = "u16"
            return "u16"
        if is_strbuf(recv_t):
            return self.check_strbuf_method(e, recv_t)
        if not self.is_struct(recv_t):
            shown = "Zahlliteral" if recv_t == UNTYPED else f"'{recv_t}'"
            raise self.error(
                f"Methodenaufruf '.{e.name}(...)' braucht ein struct, "
                f"gefunden {shown}", e)  # enums haben keine Methoden (Phase 2)
        info = self.structs[recv_t]
        sig = info.methods.get(e.name)
        if sig is None:
            fields = [f.name for f in info.fields]
            hint = (f" — '{e.name}' ist ein Feld, kein Aufruf"
                    if e.name in fields else "")
            raise self.error(f"'{recv_t}' hat keine Methode '{e.name}'{hint}", e)
        if not self.is_lvalue(e.recv):
            raise self.error(
                "Methoden brauchen eine Variable als Empfänger — "
                "Zwischenergebnis erst mit 'let' speichern", e)
        if sig.self_mut:
            root = self.root_var(e.recv)
            if root is not None:
                self.require_mutable(root, e)
        self.check_args(e.args, e.names, e.muts, sig.params,
                        f"'{recv_t}.{e.name}'", e, allow_named=False)
        e.struct_name = recv_t
        e.typ = sig.return_type
        return sig.return_type

    def check_strbuf_method(self, e: ast.MethodCall, recv_t: str) -> str | None:
        spec = self.STRBUF_METHODS.get(e.name)
        if spec is None:
            raise self.error(
                f"strbuf kennt '{e.name}' nicht — es gibt: "
                f"{', '.join(sorted(self.STRBUF_METHODS))}", e)
        param_types, writes, ret = spec
        if not self.is_lvalue(e.recv):
            raise self.error("strbuf-Methoden brauchen eine Puffer-Variable", e)
        if writes:
            root = self.root_var(e.recv)
            if root is not None:
                self.require_mutable(root, e)
        if any(n is not None for n in e.names) or any(e.muts):
            raise self.error("strbuf-Methoden: nur positionale Argumente", e)
        if len(e.args) != len(param_types):
            raise self.error(
                f"'{e.name}' erwartet {len(param_types)} Argument(e), "
                f"{len(e.args)} übergeben", e)
        for arg, pt in zip(e.args, param_types):
            t = self.check_expr(arg)
            self.coerce(arg, t, pt, f"Argument von '{e.name}'")
        if ret is not None and is_result(ret):
            self.require_type(ret, e)  # registriert die Instanzierung
        e.typ = ret
        return ret

    def check_call(self, call: ast.CallExpr) -> FnSig | None:
        # Ok/Err: kontext-typisierte Result-Konstruktoren — die Prüfung des
        # Arguments passiert erst in coerce(), wenn der Zieltyp bekannt ist
        if call.name in ("Ok", "Err"):
            if len(call.args) != 1 or any(n is not None for n in call.names) \
                    or any(call.muts):
                raise self.error(
                    f"'{call.name}' braucht genau einen Wert: {call.name}(wert)",
                    call)
            call.callee_kind = "result_ok" if call.name == "Ok" else "result_err"
            return None
        # Konstruktion? (struct-Name wie ein Aufruf verwendet)
        if call.name in self.structs:
            self.check_construction(call)
            return None
        if call.name in self.variants:
            self.check_variant_construction(call)
            return None
        if call.name in self.enums:
            raise self.error(
                f"'{call.name}' ist ein enum — konstruiert wird über seine "
                "Varianten", call)
        sig = self.functions.get(call.name)
        if sig is None:
            if self.lookup_var(call.name) is not None:
                raise self.error(f"'{call.name}' ist eine Variable, keine Funktion", call)
            if call.name in self.consts:
                raise self.error(
                    f"'{call.name}' ist eine Konstante, keine Funktion", call)
            raise self.error(f"unbekannte Funktion '{call.name}'", call)
        self.check_args(call.args, call.names, call.muts, sig.params,
                        f"'{call.name}'", call, allow_named=False)
        call.callee_kind = "extern" if sig.is_extern else "fn"
        return sig

    def check_construction(self, call: ast.CallExpr) -> None:
        """Point(10, 20) oder Point(x: 10, y: 20); Felder mit Default weglassbar.
        Normalisiert call.args auf vollständige Feld-Reihenfolge."""
        info = self.structs[call.name]
        if any(call.muts):
            raise self.error("'mut' ist bei Konstruktion sinnlos — es entsteht "
                             "ein neuer Wert", call)
        named = [n for n in call.names if n is not None]
        if named and len(named) != len(call.args):
            raise self.error(
                "entweder alle Argumente benannt oder alle positional", call)

        values: dict[str, ast.Expr] = {}
        if named:
            field_names = {f.name for f in info.fields}
            for n, arg in zip(call.names, call.args):
                assert n is not None
                if n not in field_names:
                    raise self.error(f"'{call.name}' hat kein Feld '{n}'", arg)
                if n in values:
                    raise self.error(f"Feld '{n}' kommt doppelt vor", arg)
                values[n] = arg
        else:
            if len(call.args) > len(info.fields):
                raise self.error(
                    f"'{call.name}' hat {len(info.fields)} Feld(er), "
                    f"{len(call.args)} Argumente übergeben", call)
            for f, arg in zip(info.fields, call.args):
                values[f.name] = arg

        normalized: list[ast.Expr] = []
        for f in info.fields:
            if f.name in values:
                arg = values[f.name]
                t = self.check_expr(arg)
                self.coerce(arg, t, f.typ,
                            f"Feld '{f.name}' von '{call.name}'")
                self.check_pointer_arg(arg, f.typ, mut=False)
                normalized.append(arg)
            elif f.has_default:
                lit: ast.Expr
                if f.typ == "bool":
                    lit = ast.BoolLit(bool(f.default_value), call.line, call.col)
                    lit.typ = "bool"
                else:
                    lit = ast.NumberLit(int(f.default_value), call.line, call.col)
                    lit.typ = f.typ
                normalized.append(lit)
            else:
                raise self.error(
                    f"Feld '{f.name}' von '{call.name}' fehlt und hat keinen "
                    "Default", call)
        call.args = normalized
        call.names = [None] * len(normalized)
        call.muts = [False] * len(normalized)
        call.callee_kind = "struct"
        call.typ = call.name

    def check_variant_construction(self, call: ast.CallExpr) -> None:
        """SetSpeed(120) / Move(x: -40, y: 200) — Enum-Konstruktor, kein Aufruf."""
        v = self.variants[call.name]
        if not v.params:
            raise self.error(
                f"'{call.name}' ist datenlos — ohne Klammern schreiben "
                f"('{call.name}', nicht '{call.name}()')", call)
        if any(call.muts):
            raise self.error("'mut' ist bei Konstruktion sinnlos — es entsteht "
                             "ein neuer Wert", call)
        named = [n for n in call.names if n is not None]
        if named and len(named) != len(call.args):
            raise self.error(
                "entweder alle Argumente benannt oder alle positional", call)
        if named:
            by_name = {p.name: i for i, p in enumerate(v.params)}
            ordered: list[ast.Expr | None] = [None] * len(v.params)
            for n, arg in zip(call.names, call.args):
                assert n is not None
                if n not in by_name:
                    raise self.error(f"'{call.name}' hat kein Feld '{n}'", arg)
                if ordered[by_name[n]] is not None:
                    raise self.error(f"Feld '{n}' kommt doppelt vor", arg)
                ordered[by_name[n]] = arg
            for p, slot in zip(v.params, ordered):
                if slot is None:
                    raise self.error(
                        f"Feld '{p.name}' von '{call.name}' fehlt", call)
            call.args = [a for a in ordered if a is not None]
            call.names = [None] * len(call.args)
        elif len(call.args) != len(v.params):
            raise self.error(
                f"'{call.name}' hat {len(v.params)} Feld(er), "
                f"{len(call.args)} Argumente übergeben", call)
        for arg, p in zip(call.args, v.params):
            t = self.check_expr(arg)
            self.coerce(arg, t, p.typ, f"Feld '{p.name}' von '{call.name}'")
            self.check_pointer_arg(arg, p.typ, mut=False)
        call.muts = [False] * len(call.args)
        call.callee_kind = "enum"
        call.enum_name = v.enum
        call.typ = v.enum

    def check_args(self, args, names, muts, params: list[ast.Param],
                   what: str, node, allow_named: bool) -> None:
        if not allow_named and any(n is not None for n in names):
            raise self.error(
                "benannte Argumente gibt es nur bei Konstruktoren", node)
        if len(args) != len(params):
            raise self.error(
                f"{what} erwartet {len(params)} Argument(e), "
                f"{len(args)} übergeben", node,
            )
        for i, (arg, is_mut, param) in enumerate(zip(args, muts, params), start=1):
            t = self.check_expr(arg)
            self.coerce(arg, t, param.typ,
                        f"Argument {i} von {what} ('{param.name}')")
            # mut auf beiden Seiten ehrlich (§7.2)
            if param.mutable and not is_mut:
                raise self.error(
                    f"Argument {i} von {what}: Parameter '{param.name}' ist "
                    "'mut' — die Aufrufstelle muss ebenfalls 'mut' schreiben "
                    "(§7.2), z. B. f(mut x)", arg)
            if is_mut and not param.mutable:
                raise self.error(
                    f"Argument {i} von {what}: Parameter '{param.name}' ist "
                    "nur eine Lese-Sicht — 'mut' hier ist falsch", arg)
            if is_mut:
                if not self.is_lvalue(arg):
                    raise self.error("'mut'-Argument braucht eine Variable", arg)
                root = self.root_var(arg)
                if root is not None:
                    self.require_mutable(root, arg)
            self.check_pointer_arg(arg, param.typ, mut=is_mut)

    def check_pointer_arg(self, arg: ast.Expr, typ: str, mut: bool) -> None:
        """structs/enums werden als Zeiger übergeben — das Argument muss
        adressierbar sein: Variable, Feld oder Konstruktion (Compound-Literal)."""
        if is_array(typ) or is_strbuf(typ):
            if not self.is_lvalue(arg):
                raise self.error("Array-/Puffer-Argument braucht eine Variable", arg)
            return
        if not self.is_composite(typ):
            return
        if self.is_lvalue(arg):
            return
        if isinstance(arg, ast.CallExpr) and arg.callee_kind in ("struct", "enum"):
            return
        if isinstance(arg, ast.NameExpr) and arg.is_variant:
            return  # wird als Compound-Literal ausgegeben -> adressierbar
        raise self.error(
            "Zwischenergebnis kann nicht direkt übergeben werden — "
            "erst mit 'let' in eine Variable speichern", arg)

    def check_unary(self, e: ast.UnaryOp) -> str:
        t = self.check_expr(e.operand)
        if e.op == "not":
            if t != "bool":
                raise self.error(f"'not' erwartet 'bool', gefunden '{t}'", e)
            e.typ = "bool"
            return "bool"
        # unäres Minus
        if t == "bool" or self.is_struct(t):
            raise self.error(f"unäres '-' erwartet einen Zahlentyp, gefunden '{t}'", e)
        if t != UNTYPED:
            e.typ = t
        return t

    def check_binary(self, e: ast.BinaryOp) -> str:
        op = e.op
        if op in ("and", "or"):
            lt = self.check_expr(e.left)
            rt = self.check_expr(e.right)
            for t, side in ((lt, e.left), (rt, e.right)):
                if t != "bool":
                    shown = "Zahlliteral" if t == UNTYPED else f"'{t}'"
                    raise self.error(f"'{op}' erwartet 'bool'-Operanden, gefunden {shown}",
                                     side)
            e.typ = "bool"
            return "bool"

        lt = self.check_expr(e.left)
        rt = self.check_expr(e.right)
        for t, side in ((lt, e.left), (rt, e.right)):
            if t in (OK_PSEUDO, ERR_PSEUDO):
                raise self.error(
                    "Ok/Err gibt es nur, wo ein Result erwartet wird", side)
            if is_result(t):
                raise self.error(
                    f"'{op}' ist für Result nicht definiert — über 'match' "
                    "oder '?' zerlegen", side)
            if self.is_struct(t):
                raise self.error(
                    f"'{op}' ist für structs nicht definiert (Typ '{t}')", side)
            if self.is_enum(t):
                raise self.error(
                    f"'{op}' ist für enums nicht definiert (Typ '{t}') — "
                    "Fallunterscheidung über 'match'", side)
            if is_array(t):
                raise self.error(
                    f"'{op}' ist für Arrays nicht definiert (Typ '{t}')", side)

        if op in ("=", "<>", "<", "<=", ">", ">="):
            if "str" in (lt, rt) or is_strbuf(lt) or is_strbuf(rt):
                if op not in ("=", "<>"):
                    raise self.error(
                        f"'{op}' ist für Strings nicht definiert — "
                        "nur '=' und '<>' (Inhaltsvergleich)", e)
                # strbuf-Seiten werden automatisch zur str-Sicht (6.3)
                self.coerce(e.left, lt, "str", "linke Seite des Vergleichs")
                self.coerce(e.right, rt, "str", "rechte Seite des Vergleichs")
                e.typ = "bool"
                return "bool"
            if "bool" in (lt, rt):
                if op not in ("=", "<>"):
                    raise self.error(f"'{op}' ist für 'bool' nicht definiert", e)
                if lt != rt:
                    raise self.error(
                        f"Typkonflikt bei '{op}': '{lt}' und '{rt}'", e)
                e.typ = "bool"
                return "bool"
            if self.unify_int_operands(e, lt, rt) == UNTYPED:
                raise self.error(
                    "Vergleich zweier Zahlliterale — Typ nicht bestimmbar, "
                    "Cast mit 'as' nötig", e,
                )
            e.typ = "bool"
            return "bool"

        # Arithmetik / Bits / Shifts
        for t, side in ((lt, e.left), (rt, e.right)):
            if t == "bool":
                raise self.error(f"'{op}' erwartet Zahlentypen, gefunden 'bool'", side)
            if t == "str" or is_strbuf(t):
                raise self.error(
                    f"'{op}' ist für Strings nicht definiert — "
                    "zum Anhängen strbuf.append(...) verwenden", side)
        result = self.unify_int_operands(e, lt, rt)
        if result != UNTYPED:
            e.typ = result
        return result

    def unify_int_operands(self, e: ast.BinaryOp, lt: str, rt: str) -> str:
        """Beide Operanden gleich; typlose Literale nehmen den Typ der Gegenseite."""
        if lt == UNTYPED and rt == UNTYPED:
            return UNTYPED
        if lt == UNTYPED:
            self.finalize(e.left, rt)
            return rt
        if rt == UNTYPED:
            self.finalize(e.right, lt)
            return lt
        if lt != rt:
            raise self.error(
                f"Typkonflikt bei '{e.op}': '{lt}' und '{rt}' — "
                "keine impliziten Umwandlungen, expliziter Cast mit 'as' nötig", e,
            )
        return lt

    def check_cast(self, e: ast.CastExpr) -> str:
        t = self.check_expr(e.expr)
        if e.target == "bool":
            raise self.error(
                "Cast nach 'bool' ist nicht erlaubt — verwende einen Vergleich wie '<> 0'",
                e,
            )
        if e.target not in INT_TYPES:
            raise self.error(f"Cast nach '{e.target}' ist nicht erlaubt", e)
        if self.is_composite(t) or is_array(t) or t == "str" or is_strbuf(t):
            raise self.error(
                "structs/enums/Arrays/Strings können nicht gecastet werden", e)
        if t == UNTYPED:
            self.finalize(e.expr, e.target)
        e.typ = e.target
        return e.target

    # --- Typ-Finalisierung für typlose Literale ---------------------------------

    def coerce(self, e: ast.Expr, actual: str, expected: str, context: str) -> None:
        # Sicht-Regel (6.3): ein strbuf kann überall stehen, wo str erwartet
        # wird — der Compiler baut die Sicht (Längen-Schnappschuss!)
        if expected == "str" and is_strbuf(actual):
            if not self.is_lvalue(e):
                raise self.error(
                    "strbuf-Sicht braucht eine Puffer-Variable", e)
            e.strbuf_view = True  # dynamisches Attribut für den Codegen
            e.typ = "str"
            return
        if actual in (OK_PSEUDO, ERR_PSEUDO):
            if not is_result(expected):
                raise self.error(
                    f"{context}: erwartet '{expected}', gefunden "
                    f"{show_type(actual)} — Ok/Err gibt es nur, wo ein "
                    "Result erwartet wird", e)
            assert isinstance(e, ast.CallExpr)
            ok_t, err_t = result_parts(expected)
            inner = ok_t if actual == OK_PSEUDO else err_t
            t_arg = self.check_expr(e.args[0])
            self.coerce(e.args[0], t_arg, inner,
                        f"Wert in {e.name}(...)")
            e.typ = expected
            e.enum_name = expected  # Result-Typ für den Codegen
            return
        if actual == UNTYPED:
            if expected in RANGES:
                self.finalize(e, expected)
                return
            raise self.error(f"{context}: erwartet '{expected}', gefunden Zahlliteral", e)
        if actual != expected:
            raise self.error(
                f"{context}: erwartet '{expected}', gefunden {show_type(actual)}", e)

    def finalize(self, e: ast.Expr, typ: str) -> None:
        """Schreibt den Kontexttyp in einen rein literalen Teilbaum und prüft Bereiche."""
        if typ == "bool":
            raise self.error("Zahlliteral, wo 'bool' erwartet wird", e)
        value = self.fold(e)
        lo, hi = RANGES[typ]
        if not (lo <= value <= hi):
            raise self.error(
                f"Wert {value} passt nicht in '{typ}' ({lo} bis {hi})", e)
        self._stamp(e, typ)
        if isinstance(e, (ast.UnaryOp, ast.BinaryOp)):
            # Der Codegen gibt den gefalteten Wert aus — C würde den Ausdruck
            # sonst im (ggf. vorzeichenlosen) Zieltyp anders auswerten als die
            # typlose Literal-Arithmetik (z. B. -7 // 2 + 4 in u16).
            e.const_value = value

    def _stamp(self, e: ast.Expr, typ: str) -> None:
        e.typ = typ
        if isinstance(e, ast.UnaryOp):
            self._stamp(e.operand, typ)
        elif isinstance(e, ast.BinaryOp):
            self._stamp(e.left, typ)
            self._stamp(e.right, typ)

    def fold(self, e: ast.Expr) -> int:
        """Konstantes Auswerten eines typlosen Teilbaums (nur Literale möglich)."""
        if isinstance(e, ast.NumberLit):
            return e.value
        if isinstance(e, ast.UnaryOp) and e.op == "-":
            return -self.fold(e.operand)
        if isinstance(e, ast.BinaryOp):
            a = self.fold(e.left)
            b = self.fold(e.right)
            if e.op in ("//", "mod") and b == 0:
                raise self.error("Division durch 0 im konstanten Ausdruck", e)
            if e.op in ("<<", ">>") and b < 0:
                raise self.error("negative Shift-Weite im konstanten Ausdruck", e)
            ops = {
                "+": lambda: a + b,
                "-": lambda: a - b,
                "*": lambda: a * b,
                "//": lambda: _cdiv(a, b),
                "mod": lambda: _cmod(a, b),
                "&": lambda: a & b,
                "|": lambda: a | b,
                "^": lambda: a ^ b,
                "<<": lambda: a << b,
                ">>": lambda: a >> b,
            }
            return ops[e.op]()
        raise AssertionError(f"nicht faltbar: {e!r}")  # pragma: no cover

    # --- Hilfen --------------------------------------------------------------

    def lookup_var(self, name: str) -> Var | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # --- "alle Pfade returnen" --------------------------------------------------

    def always_returns(self, block: ast.Block) -> bool:
        return any(self.stmt_returns(s) for s in block.statements)

    def stmt_returns(self, stmt: ast.Stmt) -> bool:
        if isinstance(stmt, ast.ReturnStmt):
            return True
        if isinstance(stmt, ast.IfStmt):
            if stmt.else_block is None:
                return False
            return (all(self.always_returns(b) for _, b in stmt.branches)
                    and self.always_returns(stmt.else_block))
        if isinstance(stmt, ast.LoopStmt):
            # Endlosschleife ohne eigenes break kehrt nie zurück -> Pfad ist abgedeckt
            return not self.block_has_break(stmt.body)
        if isinstance(stmt, ast.RepeatStmt):
            # Körper läuft mindestens einmal
            return self.always_returns(stmt.body)
        if isinstance(stmt, ast.MatchStmt):
            # match ist nach der Vollständigkeitsprüfung erschöpfend
            return all(self.always_returns(a.body) for a in stmt.arms)
        return False

    def block_has_break(self, block: ast.Block) -> bool:
        """break, das DIESE Schleife verlässt (nicht in verschachtelten Schleifen)."""
        for stmt in block.statements:
            if isinstance(stmt, ast.BreakStmt):
                return True
            if isinstance(stmt, ast.IfStmt):
                if any(self.block_has_break(b) for _, b in stmt.branches):
                    return True
                if stmt.else_block is not None and self.block_has_break(stmt.else_block):
                    return True
            if isinstance(stmt, ast.MatchStmt):
                if any(self.block_has_break(a.body) for a in stmt.arms):
                    return True
            # while/loop/repeat: deren breaks gehören zu ihnen selbst
        return False


def check(program: ast.Program, filename: str = "<eingabe>") -> ast.Program:
    return Checker(program, filename).check()
