"""Geprüfter AST -> lesbares, portables C99 (Anhang D).

Regeln:
- Alle Nutzer-Symbole bekommen das Präfix `ml_`; `extern fn`-Namen bleiben nackt.
- Vor jeder Anweisung eine #line-Direktive auf den Original-Quelltext.
- Großzügige Klammern; Arithmetik-Ergebnisse werden auf ihren Sprachtyp
  zurückgecastet, u8/u16-Operanden vorher nach unsigned (Integer-Promotion-
  Falle, Anhang D Rev. 2).
- structs (§7): typedef + Wertsemantik; Parameter als Zeiger (const* für
  Lese-Sichten), Methoden als ml_Struct_methode(&self, ...).
"""

from __future__ import annotations

import re

from . import ast_nodes as ast
from .prelude import PRELUDE_ENUMS

C_TYPES: dict[str, str] = {
    "u8": "uint8_t",
    "i8": "int8_t",
    "u16": "uint16_t",
    "i16": "int16_t",
    "u32": "uint32_t",
    "i32": "int32_t",
    "bool": "bool",
}

C_CMP: dict[str, str] = {"=": "==", "<>": "!=", "<": "<", "<=": "<=", ">": ">", ">=": ">="}
C_LOGIC: dict[str, str] = {"and": "&&", "or": "||"}
C_ARITH: dict[str, str] = {
    "+": "+", "-": "-", "*": "*", "//": "/", "mod": "%",
    "&": "&", "|": "|", "^": "^", "<<": "<<", ">>": ">>",
}
C_ASSIGN: dict[str, str] = {
    ":=": "=", "+=": "+=", "-=": "-=", "*=": "*=", "//=": "/=",
    "&=": "&=", "|=": "|=", "^=": "^=",
}


class Codegen:
    def __init__(self, program: ast.Program, filename: str, board=None):
        self.program = program
        self.filename = filename
        self.board = board
        self.lines: list[str] = []
        self.indent = 0
        self.structs: dict[str, ast.StructDef] = {
            t.name: t for t in program.toplevels if isinstance(t, ast.StructDef)
        }
        self.enums: dict[str, ast.EnumDef] = {
            t.name: t for t in program.toplevels if isinstance(t, ast.EnumDef)
        }
        for pe in PRELUDE_ENUMS:
            self.enums.setdefault(pe.name, pe)
        self.actors: dict[str, ast.ActorDef] = {
            t.name: t for t in program.toplevels if isinstance(t, ast.ActorDef)
        }
        # versteckte Zustands-structs der Aktoren (Checker-Konvention actor$X)
        for a in self.actors.values():
            self.structs[f"actor${a.name}"] = ast.StructDef(
                f"actor${a.name}", a.fields, [], a.line, a.col)
        # String-Literale: eindeutige Statics, Wiederverwendung gleicher Inhalte
        self.str_literals: dict[bytes, int] = {}
        # Namen von Parametern der aktuellen Funktion, die als Zeiger übergeben
        # werden (structs/enums, self) — Zugriff wird als (*ml_name) ausgegeben.
        self.ptr_params: set[str] = set()
        self.tmp_counter = 0
        self.current_ret_type: str | None = None

    # --- Infrastruktur ---------------------------------------------------------

    def emit(self, text: str = "") -> None:
        self.lines.append("    " * self.indent + text if text else "")

    def emit_line_directive(self, node) -> None:
        self.emit(f'#line {node.line} "{self.filename}"')

    @staticmethod
    def is_result(t: str | None) -> bool:
        return t is not None and t.startswith("Result[")

    @staticmethod
    def result_parts(t: str) -> tuple[str, str]:
        inner = t[len("Result["):-1]
        ok_t, err_t = inner.split(",", 1)
        return ok_t, err_t

    def c_type(self, t: str) -> str:
        if t in C_TYPES:
            return C_TYPES[t]
        if t == "str":
            return "ml_str"     # typedef in marla_runtime.h
        if self.is_strbuf(t):
            return f"ml_strbuf_{self.strbuf_cap(t)}"
        if self.is_result(t):
            ok_t, err_t = self.result_parts(t)
            return f"ml_Result__{ok_t}__{err_t}"
        if t.startswith("actor$"):
            return f"ml_actor_{t[6:]}_state"
        return f"ml_{t}"

    def method_c_name(self, struct_name: str, method: str) -> str:
        if struct_name.startswith("actor$"):
            return f"ml_{struct_name[6:]}_{method}"
        return f"ml_{struct_name}_{method}"

    @staticmethod
    def is_array(t: str | None) -> bool:
        return (t is not None and "[" in t and not t.startswith("Result[")
                and not t.startswith("strbuf["))

    @staticmethod
    def is_strbuf(t: str | None) -> bool:
        return t is not None and t.startswith("strbuf[")

    @staticmethod
    def strbuf_cap(t: str) -> int:
        return int(t[len("strbuf["):-1])

    @staticmethod
    def array_parts(t: str) -> tuple[str, str]:
        """'u8[2][3]' -> ('u8', '[2][3]')"""
        i = t.index("[")
        return t[:i], t[i:]

    def decl_code(self, typ: str, name: str, const: bool = False) -> str:
        """Deklaration einer Variablen/eines Parameters (Arrays brauchen die
        Dimensionen hinter dem Namen)."""
        prefix = "const " if const else ""
        if self.is_array(typ):
            base, dims = self.array_parts(typ)
            return f"{prefix}{self.c_type(base)} {name}{dims}"
        return f"{prefix}{self.c_type(typ)} {name}"

    def is_struct(self, t: str | None) -> bool:
        return t is not None and t in self.structs

    def is_ptr_type(self, t: str) -> bool:
        """Typen, die als Zeiger in Funktionen wandern (§7.2)."""
        return (t in self.structs or t in self.enums or self.is_result(t)
                or self.is_strbuf(t))

    # --- Datei-Gerüst (D.3) ------------------------------------------------

    def generate(self) -> str:
        self.emit(f"/* erzeugt von marla aus {self.filename} -- nicht von Hand editieren */")
        self.emit("#include <stdint.h>")
        self.emit("#include <stdbool.h>")
        self.emit('#include "hal_avr.h"')
        self.emit('#include "marla_runtime.h"   /* Panic-Hook + Bereichspruefung (7.5) */')
        self.emit()

        externs = [t for t in self.program.toplevels if isinstance(t, ast.ExternDecl)]
        fns = [t for t in self.program.toplevels if isinstance(t, ast.FnDef)]
        consts = [t for t in self.program.toplevels if isinstance(t, ast.ConstDecl)]
        structs = self.sorted_structs()

        if consts:
            self.emit("/* Konstanten (const) -- Compile-Zeit-Werte, null RAM, null Flash */")
            for c in consts:
                self.emit(f"#define ml_{c.name} {self.const_literal(c)}")
            self.emit()

        for t in self.sorted_types():
            if isinstance(t, ast.StructDef):
                self.gen_struct_typedef(t)
            else:
                self.gen_enum_typedef(t)
            self.emit()

        if self.program.strbuf_caps:
            self.emit("/* strbuf[N]-Kapazitäten (6.3) */")
            for cap in sorted(self.program.strbuf_caps):
                self.emit(f"typedef struct {{ uint16_t len; "
                          f"uint8_t data[{cap}]; }} ml_strbuf_{cap};")
            self.emit()

        literals = self.collect_string_literals()
        if literals:
            self.emit("/* String-Literale (UTF-8, stilles \\0 fuer FFI, 6.1) */")
            for value, idx in literals:
                bytes_c = ", ".join(str(b) for b in value) + ", 0"
                self.emit(f"static const uint8_t ml__str{idx}[] ML_PROGMEM = "
                          f"{{ {bytes_c} }};")
            self.emit()

        if self.program.result_types:
            self.emit("/* Result[T,E]-Instanzierungen */")
            for rt in self.program.result_types:
                ok_t, err_t = self.result_parts(rt)
                self.emit("typedef struct {")
                self.emit("    bool is_ok;")
                self.emit(f"    union {{ {self.c_type(ok_t)} ml_ok; "
                          f"{self.c_type(err_t)} ml_err; }} as;")
                self.emit(f"}} {self.c_type(rt)};")
            self.emit()

        if externs:
            self.emit("/* extern fn -> von der HAL bereitgestellt */")
            for e in externs:
                self.emit(self.signature(e, is_extern=True) + ";")
            self.emit()

        actors = list(self.actors.values())
        if actors:
            self.emit("/* Aktoren (§9): Zustand + Ringpuffer-Mailbox, statisch */")
            for a in actors:
                self.gen_actor_storage(a)
            self.emit()

        self.emit("/* Prototypen aller ml_-Funktionen */")
        for fn in fns:
            self.emit(self.signature(fn, is_extern=False) + ";")
        for s in structs:
            for m in s.methods:
                self.emit(self.method_signature(s.name, m) + ";")
        for a in actors:
            for m in a.methods:
                self.emit(self.method_signature(f"actor${a.name}", m) + ";")
            self.emit(f"static void ml__send_{a.name}({self.c_type(a.msg_enum)} m);")
        self.emit()

        for s in structs:
            for m in s.methods:
                self.gen_method(s.name, m)
                self.emit()
        for a in actors:
            for m in a.methods:
                self.gen_method(f"actor${a.name}", m)
                self.emit()
            self.gen_actor_send(a)
            self.emit()

        has_glue = self.board is not None and self.board.peripherals
        if has_glue:
            self.gen_board_glue()
            self.emit()

        for fn in fns:
            self.gen_fn(fn)
            self.emit()

        if actors:
            self.gen_poll(actors)
            self.emit()

        self.emit("int main(void) {")
        self.emit("    hal_init();          /* stellt die HAL bereit (Pins, Timer) */")
        if has_glue:
            self.emit("    ml__board_init();    /* Peripherie aus der Board-Config */")
        self.emit("    ml_main();           /* Setup */")
        if actors:
            self.emit("    for (;;) {           /* kooperativer Scheduler (§9.8) */")
            if has_glue:
                self.emit("        ml__board_poll();")
            self.emit("        ml__poll();")
            self.emit("    }")
        else:
            self.emit("    for (;;) {}          /* falls ml_main je zurückkehrt */")
        self.emit("}")
        return "\n".join(self.lines) + "\n"

    def gen_board_glue(self) -> None:
        """Timer- und UART-Glue aus der Board-Config (Abschnitt 10):
        AVR über ISRs, Host über Uhr/stdin-Attrappe."""
        board = self.board
        timers = [p for p in board.peripherals if p.kind == "timer"]
        uarts = [p for p in board.peripherals if p.kind == "uart"]
        tick = "((ml_TimerMsg){ .tag = ml_TimerMsg_Tick })"

        self.emit(f"/* Peripherie-Glue aus Board-Config '{board.name}' (Abschnitt 10) */")
        self.emit("#ifdef __AVR__")
        for t in timers:
            self.emit(f"static volatile uint16_t ml__{t.name}_cnt = 0;")
            self.emit("ISR(TIMER0_COMPA_vect) {   /* 1-ms-Basistakt */")
            self.emit(f"    if (++ml__{t.name}_cnt >= {t.period_ms}) {{")
            self.emit(f"        ml__{t.name}_cnt = 0;")
            self.emit(f"        ml__send_{t.target}({tick});")
            self.emit("    }")
            self.emit("}")
        for u in uarts:
            self.emit("ISR(USART_RX_vect) {")
            self.emit("    uint8_t b = UDR0;")
            self.emit(f"    ml__send_{u.target}(((ml_UartMsg){{ "
                      ".tag = ml_UartMsg_ByteReceived, "
                      ".as.ByteReceived = { .ml_b = b } }));")
            self.emit("}")
        self.emit("static void ml__board_init(void) {")
        if timers:
            self.emit("    TCCR0A = (1 << WGM01);              /* CTC */")
            self.emit("    OCR0A  = (uint8_t)(MARLA_F_CPU / 64UL / 1000UL - 1);")
            self.emit("    TIMSK0 = (1 << OCIE0A);")
            self.emit("    TCCR0B = (1 << CS01) | (1 << CS00); /* Prescaler 64 */")
        for u in uarts:
            self.emit(f"    UBRR0  = (uint16_t)(MARLA_F_CPU / 16UL / {u.baud}UL - 1);")
            self.emit("    UCSR0B = (1 << RXEN0) | (1 << RXCIE0) | (1 << TXEN0);")
        self.emit("    sei();")
        self.emit("}")
        self.emit("static void ml__board_poll(void) {}  /* AVR: alles per ISR */")
        self.emit("#else /* Host-Attrappe: Uhr + stdin */")
        for t in timers:
            self.emit(f"static uint32_t ml__{t.name}_next = 0;")
        self.emit("static void ml__board_init(void) {")
        for t in timers:
            self.emit(f"    ml__{t.name}_next = marla_host_now_ms() + {t.period_ms};")
        self.emit("}")
        self.emit("static void ml__board_poll(void) {")
        for t in timers:
            self.emit(f"    if ((int32_t)(marla_host_now_ms() - ml__{t.name}_next) >= 0) {{")
            self.emit(f"        ml__{t.name}_next += {t.period_ms};")
            self.emit(f"        ml__send_{t.target}({tick});")
            self.emit("    }")
        for u in uarts:
            self.emit("    { int c = marla_host_getchar();")
            self.emit(f"      if (c >= 0) ml__send_{u.target}(((ml_UartMsg){{ "
                      ".tag = ml_UartMsg_ByteReceived, "
                      ".as.ByteReceived = { .ml_b = (uint8_t)c } })); }")
        self.emit("}")
        self.emit("#endif")

    # --- Aktoren ---------------------------------------------------------------

    def gen_actor_storage(self, a: ast.ActorDef) -> None:
        state_t = self.c_type(f"actor${a.name}")
        inits = ", ".join(
            f".ml_{f.name} = (({self.c_type(f.typ)})({self.c_bool(f.default_value)}))"
            for f in a.fields if f.default is not None)
        init = f" = {{ {inits} }}" if inits else ""
        self.emit(f"static {state_t} ml_actor_{a.name}{init};")
        msg_t = self.c_type(a.msg_enum)
        n = a.mailbox_size
        self.emit(f"static {msg_t} ml__mbox_{a.name}[{n}];")
        self.emit(f"static volatile uint8_t ml__mbox_{a.name}_head = 0;")
        self.emit(f"static volatile uint8_t ml__mbox_{a.name}_count = 0;")

    @staticmethod
    def c_bool(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def gen_actor_send(self, a: ast.ActorDef) -> None:
        n = a.mailbox_size
        name = a.name
        self.emit(f"static void ml__send_{name}({self.c_type(a.msg_enum)} m) {{")
        self.emit("    ML_IRQ_LOCK();")
        if a.policy == "Drop":
            self.emit(f"    if (ml__mbox_{name}_count < {n}) {{")
        elif a.policy == "DropOldest":
            self.emit(f"    if (ml__mbox_{name}_count == {n}) {{  /* Ältestes weicht */")
            self.emit(f"        ml__mbox_{name}_head = (uint8_t)((ml__mbox_{name}_head + 1) % {n});")
            self.emit(f"        ml__mbox_{name}_count--;")
            self.emit("    }")
            self.emit("    {")
        else:  # Panic
            self.emit(f"    if (ml__mbox_{name}_count == {n}) {{")
            self.emit("        ML_IRQ_UNLOCK();")
            self.emit(f'        ML_PANIC("{self.filename}", {a.line});')
            self.emit("    }")
            self.emit("    {")
        self.emit(f"        ml__mbox_{name}[(uint8_t)((ml__mbox_{name}_head + "
                  f"ml__mbox_{name}_count) % {n})] = m;")
        self.emit(f"        ml__mbox_{name}_count++;")
        self.emit("    }")
        self.emit("    ML_IRQ_UNLOCK();")
        self.emit("}")

    def gen_poll(self, actors: list[ast.ActorDef]) -> None:
        self.emit("/* Hauptschleife fragt reihum die Mailboxen ab (§9.8) */")
        self.emit("static void ml__poll(void) {")
        self.indent += 1
        for a in actors:
            n = a.mailbox_size
            msg_t = self.c_type(a.msg_enum)
            self.emit(f"if (ml__mbox_{a.name}_count > 0) {{")
            self.indent += 1
            self.emit("ML_IRQ_LOCK();")
            self.emit(f"{msg_t} m = ml__mbox_{a.name}[ml__mbox_{a.name}_head];")
            self.emit(f"ml__mbox_{a.name}_head = (uint8_t)((ml__mbox_{a.name}_head + 1) % {n});")
            self.emit(f"ml__mbox_{a.name}_count--;")
            self.emit("ML_IRQ_UNLOCK();")
            self.emit(f"ml_{a.name}_receive(&ml_actor_{a.name}, &m);")
            self.indent -= 1
            self.emit("}")
        self.indent -= 1
        self.emit("}")

    def collect_string_literals(self) -> list[tuple[bytes, int]]:
        """Sammelt alle StrLit-Knoten des Programms und vergibt Indizes."""
        def walk(obj) -> None:
            if isinstance(obj, ast.StrLit):
                if obj.value not in self.str_literals:
                    self.str_literals[obj.value] = len(self.str_literals)
            if hasattr(obj, "__dataclass_fields__"):
                for name in obj.__dataclass_fields__:
                    walk(getattr(obj, name))
            elif isinstance(obj, (list, tuple)):
                for item in obj:
                    walk(item)

        walk(self.program)
        return sorted(self.str_literals.items(), key=lambda kv: kv[1])

    def sorted_structs(self) -> list[ast.StructDef]:
        return [t for t in self.sorted_types() if isinstance(t, ast.StructDef)]

    def sorted_types(self) -> list[ast.StructDef | ast.EnumDef]:
        """Topologisch: enthaltene Typen zuerst (C braucht vollständige Typen)."""
        order: list[ast.StructDef | ast.EnumDef] = []
        done: set[str] = set()

        def deps(t: ast.StructDef | ast.EnumDef) -> list[str]:
            if isinstance(t, ast.StructDef):
                return [f.typ for f in t.fields]
            return [p.typ for v in t.variants for p in v.params]

        def visit(t: ast.StructDef | ast.EnumDef) -> None:
            if t.name in done:
                return
            done.add(t.name)
            for d in deps(t):
                if d in self.structs:
                    visit(self.structs[d])
                elif d in self.enums:
                    visit(self.enums[d])
            order.append(t)

        for t in list(self.structs.values()) + list(self.enums.values()):
            visit(t)
        return order

    def gen_enum_typedef(self, e: ast.EnumDef) -> None:
        self.emit_line_directive(e)
        tags = ", ".join(f"ml_{e.name}_{v.name}" for v in e.variants)
        self.emit(f"enum {{ {tags} }};")
        self.emit("typedef struct {")
        self.emit("    uint8_t tag;")
        data_variants = [v for v in e.variants if v.params]
        if data_variants:
            self.emit("    union {")
            for v in data_variants:
                fields = " ".join(
                    f"{self.c_type(p.typ)} ml_{p.name};" for p in v.params)
                self.emit(f"        struct {{ {fields} }} {v.name};")
            self.emit("    } as;")
        self.emit(f"}} ml_{e.name};")

    def gen_struct_typedef(self, s: ast.StructDef) -> None:
        self.emit_line_directive(s)
        self.emit("typedef struct {")
        for f in s.fields:
            self.emit("    " + self.decl_code(f.typ, f"ml_{f.name}") + ";")
        self.emit(f"}} {self.c_type(s.name)};")

    def const_literal(self, decl: ast.ConstDecl) -> str:
        value = decl.const_value
        assert value is not None, "Checker muss vor Codegen laufen"
        if decl.typ == "bool":
            return "(true)" if value else "(false)"
        return f"(({C_TYPES[decl.typ]})({value}))"

    # --- Signaturen ---------------------------------------------------------

    def param_decl(self, p: ast.Param, prefix: bool) -> str:
        name = f"ml_{p.name}" if prefix else p.name
        if self.is_array(p.typ):
            # Arrays zerfallen in C zu Zeigern -> Referenzsemantik wie §7.2
            return self.decl_code(p.typ, name, const=not p.mutable)
        if self.is_ptr_type(p.typ):
            const = "" if p.mutable else "const "
            return f"{const}{self.c_type(p.typ)} *{name}"
        return f"{self.c_type(p.typ)} {name}"

    def signature(self, decl: ast.ExternDecl | ast.FnDef, is_extern: bool) -> str:
        ret = self.c_type(decl.return_type) if decl.return_type else "void"
        if is_extern:
            prefix, name = "extern ", decl.name
            params = ", ".join(self.param_decl(p, prefix=False)
                               for p in decl.params) or "void"
        else:
            prefix, name = "static ", f"ml_{decl.name}"
            params = ", ".join(self.param_decl(p, prefix=True)
                               for p in decl.params) or "void"
        return f"{prefix}{ret} {name}({params})"

    def method_signature(self, struct_name: str, m: ast.FnDef) -> str:
        ret = self.c_type(m.return_type) if m.return_type else "void"
        self_p = m.params[0]
        const = "" if self_p.mutable else "const "
        params = [f"{const}{self.c_type(struct_name)} *ml_self"]
        params += [self.param_decl(p, prefix=True) for p in m.params[1:]]
        return f"static {ret} {self.method_c_name(struct_name, m.name)}({', '.join(params)})"

    # --- Funktionen -----------------------------------------------------------

    def gen_fn(self, fn: ast.FnDef) -> None:
        self.ptr_params = {p.name for p in fn.params if self.is_ptr_type(p.typ)}
        self.current_ret_type = fn.return_type
        self.emit_line_directive(fn)
        self.emit(self.signature(fn, is_extern=False) + " {")
        self.indent += 1
        self.gen_block(fn.body)
        self.indent -= 1
        self.emit("}")
        self.ptr_params = set()

    def gen_method(self, struct_name: str, m: ast.FnDef) -> None:
        self.ptr_params = {"self"}
        self.ptr_params |= {p.name for p in m.params[1:] if self.is_ptr_type(p.typ)}
        self.current_ret_type = m.return_type
        self.emit_line_directive(m)
        self.emit(self.method_signature(struct_name, m) + " {")
        self.indent += 1
        self.gen_block(m.body)
        self.indent -= 1
        self.emit("}")
        self.ptr_params = set()

    def gen_block(self, block: ast.Block) -> None:
        for stmt in block.statements:
            self.gen_stmt(stmt)

    def gen_question(self, q: ast.QuestionExpr) -> str:
        """Expandiert 'expr?': Temp anlegen, bei Err früh zurückkehren.
        Liefert den C-Ausdruck für den entpackten Ok-Wert."""
        assert q.operand.typ is not None and self.current_ret_type is not None
        res_t = q.operand.typ
        tmp = f"ml__q{self.tmp_counter}"
        self.tmp_counter += 1
        self.emit(f"{self.c_type(res_t)} {tmp} = {self.expr(q.operand)};")
        self.emit(f"if (!({tmp}.is_ok)) {{")
        ret_c = self.c_type(self.current_ret_type)
        self.emit(f"    return (({ret_c}){{ .is_ok = false, "
                  f".as.ml_err = {tmp}.as.ml_err }});")
        self.emit("}")
        return f"{tmp}.as.ml_ok"

    def gen_stmt(self, stmt: ast.Stmt) -> None:
        self.emit_line_directive(stmt)
        if isinstance(stmt, ast.LetStmt):
            if stmt.value is None:
                # Array ohne Initialisierer -> Null-Init
                assert stmt.declared_type is not None
                self.emit(self.decl_code(stmt.declared_type, f"ml_{stmt.name}",
                                         const=not stmt.mutable) + " = {0};")
                return
            var = self.lookup_let_type(stmt)
            const = "" if stmt.mutable else "const "
            if isinstance(stmt.value, ast.QuestionExpr):
                value = self.gen_question(stmt.value)
            else:
                value = self.expr(stmt.value)
            self.emit(f"{const}{self.c_type(var)} ml_{stmt.name} = {value};")
        elif isinstance(stmt, ast.AssignStmt):
            self.emit(self.assign_code(stmt))
        elif isinstance(stmt, ast.CallStmt):
            if isinstance(stmt.call, ast.QuestionExpr):
                self.gen_question(stmt.call)  # Ok-Wert wird verworfen
            else:
                self.emit(self.expr(stmt.call) + ";")
        elif isinstance(stmt, ast.IfStmt):
            for i, (cond, body) in enumerate(stmt.branches):
                keyword = "if" if i == 0 else "} else if"
                self.emit(f"{keyword} ({self.expr(cond)}) {{")
                self.indent += 1
                self.gen_block(body)
                self.indent -= 1
            if stmt.else_block is not None:
                self.emit("} else {")
                self.indent += 1
                self.gen_block(stmt.else_block)
                self.indent -= 1
            self.emit("}")
        elif isinstance(stmt, ast.WhileStmt):
            self.emit(f"while ({self.expr(stmt.cond)}) {{")
            self.indent += 1
            self.gen_block(stmt.body)
            self.indent -= 1
            self.emit("}")
        elif isinstance(stmt, ast.LoopStmt):
            self.emit("for (;;) {")
            self.indent += 1
            self.gen_block(stmt.body)
            self.indent -= 1
            self.emit("}")
        elif isinstance(stmt, ast.RepeatStmt):
            self.emit("do {")
            self.indent += 1
            self.gen_block(stmt.body)
            self.indent -= 1
            # until ist Abbruchbedingung -> im C-while negiert
            self.emit(f"}} while (!({self.expr(stmt.cond)}));")
        elif isinstance(stmt, ast.BreakStmt):
            self.emit("break;")
        elif isinstance(stmt, ast.ContinueStmt):
            self.emit("continue;")
        elif isinstance(stmt, ast.ReturnStmt):
            if stmt.value is None:
                self.emit("return;")
            else:
                self.emit(f"return {self.expr(stmt.value)};")
        elif isinstance(stmt, ast.MatchStmt):
            self.gen_match(stmt)
        elif isinstance(stmt, ast.ForStmt):
            self.gen_for(stmt)
        elif isinstance(stmt, ast.SendStmt):
            self.emit(f"ml__send_{stmt.target}({self.expr(stmt.msg)});")
        else:  # pragma: no cover
            raise AssertionError(f"unbekannte Anweisung {stmt!r}")

    def gen_match_result(self, stmt: ast.MatchStmt) -> None:
        tmp = f"ml__match{self.tmp_counter}"
        self.tmp_counter += 1
        self.emit("{")
        self.indent += 1
        self.emit(f"{self.c_type(stmt.enum_name)} {tmp} = {self.expr(stmt.expr)};")
        for i, arm in enumerate(stmt.arms):
            keyword = "if" if i == 0 else "} else if"
            cond = f"{tmp}.is_ok" if arm.variant == "Ok" else f"!({tmp}.is_ok)"
            self.emit(f"{keyword} ({cond}) {{")
            self.indent += 1
            member = "ml_ok" if arm.variant == "Ok" else "ml_err"
            self.emit(f"{self.decl_code(arm.binding_types[0], f'ml_{arm.bindings[0]}')} "
                      f"= {tmp}.as.{member};")
            self.gen_block(arm.body)
            self.indent -= 1
        self.emit("}")
        self.indent -= 1
        self.emit("}")

    def gen_for(self, stmt: ast.ForStmt) -> None:
        assert stmt.var_type is not None, "Checker muss vor Codegen laufen"
        t = C_TYPES[stmt.var_type]
        end_tmp = f"ml__end{self.tmp_counter}"
        self.tmp_counter += 1
        self.emit("{")
        self.indent += 1
        self.emit(f"const {t} {end_tmp} = {self.expr(stmt.end)};")
        self.emit(f"for ({t} ml_{stmt.var} = {self.expr(stmt.start)}; "
                  f"ml_{stmt.var} < {end_tmp}; ml_{stmt.var}++) {{")
        self.indent += 1
        self.gen_block(stmt.body)
        self.indent -= 1
        self.emit("}")
        self.indent -= 1
        self.emit("}")

    def gen_match(self, stmt: ast.MatchStmt) -> None:
        """match -> if-Kette statt switch: MARLAs 'break' in einem Zweig muss
        die umgebende SCHLEIFE verlassen, Cs switch würde es schlucken."""
        assert stmt.enum_name is not None, "Checker muss vor Codegen laufen"
        if self.is_result(stmt.enum_name):
            self.gen_match_result(stmt)
            return
        enum = self.enums[stmt.enum_name]
        by_name = {v.name: v for v in enum.variants}
        tmp = f"ml__match{self.tmp_counter}"
        self.tmp_counter += 1
        self.emit("{")
        self.indent += 1
        self.emit(f"ml_{stmt.enum_name} {tmp} = {self.expr(stmt.expr)};")
        for i, arm in enumerate(stmt.arms):
            keyword = "if" if i == 0 else "} else if"
            self.emit(f"{keyword} ({tmp}.tag == ml_{stmt.enum_name}_{arm.variant}) {{")
            self.indent += 1
            v = by_name[arm.variant]
            for name, p in zip(arm.bindings, v.params):
                self.emit(f"{self.c_type(p.typ)} ml_{name} = "
                          f"{tmp}.as.{arm.variant}.ml_{p.name};")
            self.gen_block(arm.body)
            self.indent -= 1
        self.emit("}")
        self.indent -= 1
        self.emit("}")

    def assign_code(self, stmt: ast.AssignStmt) -> str:
        target = self.expr(stmt.target)
        if isinstance(stmt.value, ast.QuestionExpr):
            value = self.gen_question(stmt.value)
        else:
            value = self.expr(stmt.value)
        if stmt.op == ":=":
            return f"{target} = {value};"
        # Kombinierte Zuweisung: bei u8/u16 ausgeschrieben mit unsigned-Operanden
        # (Integer-Promotion-Falle, Anhang D Rev. 2 -- z. B. wäre 'x *= y' bei
        # u8 auf AVR sonst signed-int16-Überlauf und damit UB).
        assert stmt.target_type is not None, "Checker muss vor Codegen laufen"
        base_op = C_ARITH[stmt.op[:-1]]
        if stmt.target_type in ("u8", "u16"):
            ctype = C_TYPES[stmt.target_type]
            return (f"{target} = (({ctype})((unsigned)({target}) "
                    f"{base_op} (unsigned)({value})));")
        return f"{target} {C_ASSIGN[stmt.op]} {value};"

    def lookup_let_type(self, stmt: ast.LetStmt) -> str:
        if stmt.declared_type is not None:
            return stmt.declared_type
        assert stmt.value.typ is not None, "Checker muss vor Codegen laufen"
        return stmt.value.typ

    # --- Ausdrücke --------------------------------------------------------------

    def expr(self, e: ast.Expr) -> str:
        # Sicht-Regel (6.3): strbuf steht, wo str erwartet wird
        if getattr(e, "strbuf_view", False):
            e.strbuf_view = False
            code = self.expr(e)
            e.strbuf_view = True
            return (f"((ml_str){{ .ptr = ({code}).data, .len = ({code}).len }})")
        const = getattr(e, "const_value", None)
        if const is not None and not isinstance(e, (ast.CallExpr, ast.MethodCall)):
            assert e.typ is not None
            return f"(({C_TYPES[e.typ]})({const}))"
        if isinstance(e, ast.StrLit):
            idx = self.str_literals[e.value]
            return f"ML_STR_LIT(ml__str{idx}, {len(e.value)})"
        if isinstance(e, ast.NumberLit):
            assert e.typ is not None, "Checker muss vor Codegen laufen"
            return f"(({C_TYPES[e.typ]}){e.value})"
        if isinstance(e, ast.BoolLit):
            return "true" if e.value else "false"
        if isinstance(e, ast.NameExpr):
            if e.is_variant:
                assert e.typ is not None
                return self.bare_variant(e.typ, e.name)
            if e.name in self.ptr_params:
                return f"(*ml_{e.name})"
            return f"ml_{e.name}"
        if isinstance(e, ast.FieldAccess):
            return f"({self.expr(e.recv)}).ml_{e.name}"
        if isinstance(e, ast.IndexExpr):
            recv_t = e.recv.typ
            recv = self.expr(e.recv)
            if recv_t == "str":
                return (f"ML_STR_AT({recv}, (uint32_t)({self.expr(e.index)}), "
                        f'"{self.filename}", {e.line})')
            if self.is_strbuf(recv_t):
                return (f"({recv}).data[ML_IDX((uint32_t)({self.expr(e.index)}), "
                        f'({recv}).len, "{self.filename}", {e.line})]')
            if e.needs_check:
                idx = (f"ML_IDX((uint32_t)({self.expr(e.index)}), {e.length}, "
                       f'"{self.filename}", {e.line})')
            else:
                idx = self.expr(e.index)
            return f"{recv}[{idx}]"
        if isinstance(e, ast.MethodCall):
            return self.method_call(e)
        if isinstance(e, ast.CallExpr):
            return self.call(e)
        if isinstance(e, ast.UnaryOp):
            if e.op == "not":
                return f"(!({self.expr(e.operand)}))"
            # Ergebnis auf den Sprachtyp zurückcasten (definiertes Wrap)
            assert e.typ is not None
            if e.typ in ("u8", "u16"):
                return f"(({C_TYPES[e.typ]})(-(unsigned)({self.expr(e.operand)})))"
            return f"(({C_TYPES[e.typ]})(-({self.expr(e.operand)})))"
        if isinstance(e, ast.BinaryOp):
            left = self.expr(e.left)
            right = self.expr(e.right)
            if e.op in C_LOGIC:
                return f"(({left}) {C_LOGIC[e.op]} ({right}))"
            if e.op in C_CMP:
                if e.left.typ == "str" or e.right.typ == "str":
                    call = f"marla_str_eq(({left}), ({right}))"
                    return f"({call})" if e.op == "=" else f"(!{call})"
                return f"(({left}) {C_CMP[e.op]} ({right}))"
            assert e.typ is not None
            # Integer-Promotion-Falle (Anhang D Rev. 2): C befördert u8/u16 vor
            # der Rechnung zu signed int (AVR: 16 Bit) -- u8*u8 = 200*200 wäre
            # dort UB. Operanden nach unsigned casten => definierte
            # Modulo-Arithmetik. u32 hat bereits Integer-Rang, kein Cast
            # (ein (unsigned)-Cast würde auf AVR nach 16 Bit abschneiden!).
            if e.typ in ("u8", "u16"):
                return (f"(({C_TYPES[e.typ]})((unsigned)({left}) "
                        f"{C_ARITH[e.op]} (unsigned)({right})))")
            return f"(({C_TYPES[e.typ]})(({left}) {C_ARITH[e.op]} ({right})))"
        if isinstance(e, ast.CastExpr):
            return f"(({C_TYPES[e.target]})({self.expr(e.expr)}))"
        raise AssertionError(f"unbekannter Ausdruck {e!r}")  # pragma: no cover

    def argument(self, arg: ast.Expr, param_typ: str) -> str:
        """structs/enums wandern als Zeiger: &lvalue bzw. &Compound-Literal.
        Arrays zerfallen von selbst zum Zeiger."""
        code = self.expr(arg)
        if self.is_array(param_typ):
            return code
        if self.is_ptr_type(param_typ):
            return f"&({code})"
        return code

    def call(self, call: ast.CallExpr) -> str:
        if call.callee_kind == "struct":
            return self.construction(call)
        if call.callee_kind == "enum":
            return self.variant_construction(call)
        if call.callee_kind in ("result_ok", "result_err"):
            assert call.enum_name is not None  # Result-Typ (füllt coerce)
            ok = call.callee_kind == "result_ok"
            member = "ml_ok" if ok else "ml_err"
            flag = "true" if ok else "false"
            return (f"(({self.c_type(call.enum_name)}){{ .is_ok = {flag}, "
                    f".as.{member} = {self.expr(call.args[0])} }})")
        sig = self.find_toplevel(call.name)
        assert sig is not None
        args = ", ".join(self.argument(a, p.typ)
                         for a, p in zip(call.args, sig.params))
        name = call.name if call.callee_kind == "extern" else f"ml_{call.name}"
        return f"{name}({args})"

    def construction(self, call: ast.CallExpr) -> str:
        s = self.structs[call.name]
        inits = ", ".join(
            f".ml_{f.name} = {self.expr(a)}"
            for f, a in zip(s.fields, call.args))
        return f"((ml_{call.name}){{ {inits} }})"

    def variant_construction(self, call: ast.CallExpr) -> str:
        assert call.enum_name is not None
        enum = self.enums[call.enum_name]
        v = next(v for v in enum.variants if v.name == call.name)
        inits = ", ".join(
            f".ml_{p.name} = {self.expr(a)}"
            for p, a in zip(v.params, call.args))
        return (f"((ml_{call.enum_name}){{ .tag = ml_{call.enum_name}_{call.name}, "
                f".as.{call.name} = {{ {inits} }} }})")

    def bare_variant(self, enum_name: str, variant: str) -> str:
        return f"((ml_{enum_name}){{ .tag = ml_{enum_name}_{variant} }})"

    def method_call(self, e: ast.MethodCall) -> str:
        recv_t = e.recv.typ
        if recv_t == "str" and e.name == "len":
            return f"({self.expr(e.recv)}).len"
        if self.is_strbuf(recv_t):
            return self.strbuf_method(e, recv_t)
        assert e.struct_name is not None, "Checker muss vor Codegen laufen"
        s = self.structs[e.struct_name]
        method = next(m for m in s.methods if m.name == e.name)
        parts = [f"&({self.expr(e.recv)})"]
        parts += [self.argument(a, p.typ)
                  for a, p in zip(e.args, method.params[1:])]
        return f"{self.method_c_name(e.struct_name, e.name)}({', '.join(parts)})"

    def strbuf_method(self, e: ast.MethodCall, recv_t: str) -> str:
        recv = self.expr(e.recv)
        cap = self.strbuf_cap(recv_t)
        if e.name == "len":
            return f"({recv}).len"
        if e.name == "clear":
            return f"(({recv}).len = 0)"
        # append / append_u8/u16/u32 -> Result[u16, StrbufError]
        res_c = self.c_type("Result[u16,StrbufError]")
        if e.name == "append":
            call = (f"marla_buf_append(({recv}).data, {cap}, "
                    f"&({recv}).len, {self.expr(e.args[0])})")
        else:
            call = (f"marla_buf_append_u32(({recv}).data, {cap}, "
                    f"&({recv}).len, (uint32_t)({self.expr(e.args[0])}))")
        ok = (f"(({res_c}){{ .is_ok = true, .as.ml_ok = ({recv}).len }})")
        overflow = ("((ml_StrbufError){ .tag = ml_StrbufError_Overflow })")
        err = f"(({res_c}){{ .is_ok = false, .as.ml_err = {overflow} }})"
        return f"({call} ? {ok} : {err})"

    def find_toplevel(self, name: str):
        for top in self.program.toplevels:
            if isinstance(top, (ast.ExternDecl, ast.FnDef)) and top.name == name:
                return top
        return None


def generate(program: ast.Program, filename: str, board=None) -> str:
    return Codegen(program, filename, board).generate()
