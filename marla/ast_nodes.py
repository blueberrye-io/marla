"""AST-Knoten als Dataclasses (Anhang B.3). Alle Knoten tragen Zeile/Spalte.

Das Feld `typ` an Ausdrücken füllt der Checker aus ("u8", ..., "bool").
"""

from __future__ import annotations

from dataclasses import dataclass, field


# --- Ausdrücke ---------------------------------------------------------------

@dataclass
class NumberLit:
    value: int
    line: int
    col: int
    typ: str | None = None  # Byte-Literale ('A') kommen mit typ="u8" vom Parser


@dataclass
class StrLit:
    value: bytes            # UTF-8-Bytes, ohne das stille \0
    line: int
    col: int
    typ: str | None = "str"


@dataclass
class BoolLit:
    value: bool
    line: int
    col: int
    typ: str | None = None


@dataclass
class NameExpr:
    name: str
    line: int
    col: int
    typ: str | None = None
    # füllt der Checker: True, wenn der Name eine datenlose enum-Variante ist
    # (dann ist .typ der enum-Name und der Codegen baut den Varianten-Wert)
    is_variant: bool = False


@dataclass
class CallExpr:
    name: str
    args: list["Expr"]
    line: int
    col: int
    typ: str | None = None
    # parallel zu args: Argumentname bei benannten Argumenten, sonst None
    names: list[str | None] = field(default_factory=list)
    # parallel zu args: True bei 'mut'-Argument an der Aufrufstelle
    muts: list[bool] = field(default_factory=list)
    # füllt der Checker: "fn" | "extern" | "struct" | "enum" (Konstruktion)
    callee_kind: str | None = None
    # bei callee_kind "enum": Name des Enums (Variante steht in .name)
    enum_name: str | None = None


@dataclass
class FieldAccess:
    recv: "Expr"
    name: str
    line: int
    col: int
    typ: str | None = None


@dataclass
class IndexExpr:
    recv: "Expr"
    index: "Expr"
    line: int
    col: int
    typ: str | None = None          # Elementtyp, füllt der Checker
    needs_check: bool = True        # False = Bereich zur Compile-Zeit bewiesen
    length: int = 0                 # Arraylänge der indizierten Dimension


@dataclass
class MethodCall:
    recv: "Expr"
    name: str
    args: list["Expr"]
    line: int
    col: int
    typ: str | None = None
    names: list[str | None] = field(default_factory=list)
    muts: list[bool] = field(default_factory=list)
    # füllt der Checker: Name des structs, dessen Methode gerufen wird
    struct_name: str | None = None


@dataclass
class UnaryOp:
    op: str            # "-", "not"
    operand: "Expr"
    line: int
    col: int
    typ: str | None = None
    const_value: int | None = None  # gefalteter Wert bei rein literalem Teilbaum


@dataclass
class BinaryOp:
    op: str            # "+", "and", "=", "<>", "//", "mod", ...
    left: "Expr"
    right: "Expr"
    line: int
    col: int
    typ: str | None = None
    const_value: int | None = None  # gefalteter Wert bei rein literalem Teilbaum


@dataclass
class CastExpr:
    expr: "Expr"
    target: str        # "u8", ..., "bool" (bool wird im Checker abgelehnt)
    line: int
    col: int
    typ: str | None = None


@dataclass
class QuestionExpr:
    # expr? — nur als komplette rechte Seite / eigene Anweisung erlaubt
    operand: "Expr"
    line: int
    col: int
    typ: str | None = None


Expr = (NumberLit | StrLit | BoolLit | NameExpr | CallExpr | UnaryOp | BinaryOp
        | CastExpr | FieldAccess | MethodCall | IndexExpr | QuestionExpr)


# --- Anweisungen -------------------------------------------------------------

@dataclass
class LetStmt:
    name: str
    mutable: bool
    declared_type: str | None   # None = Typ aus dem Initialisierer ableiten
    value: Expr | None          # None nur bei Arrays (Null-Initialisierung)
    line: int
    col: int


@dataclass
class AssignStmt:
    target: Expr       # NameExpr oder FieldAccess (lvalue)
    op: str            # ":=", "+=", "-=", "*=", "//=", "&=", "|=", "^="
    value: Expr
    line: int
    col: int
    target_type: str | None = None  # füllt der Checker aus (für den Codegen)


@dataclass
class CallStmt:
    call: "CallExpr | MethodCall"
    line: int
    col: int


@dataclass
class IfStmt:
    # (Bedingung, Block) für if und jedes elif, in Reihenfolge
    branches: list[tuple[Expr, "Block"]]
    else_block: "Block | None"
    line: int
    col: int


@dataclass
class WhileStmt:
    cond: Expr
    body: "Block"
    line: int
    col: int


@dataclass
class LoopStmt:
    body: "Block"
    line: int
    col: int


@dataclass
class RepeatStmt:
    # repeat: block / until cond -- Körper läuft mindestens einmal,
    # until ist Abbruchbedingung (Pascal-Semantik)
    body: "Block"
    cond: Expr
    line: int
    col: int


@dataclass
class BreakStmt:
    line: int
    col: int


@dataclass
class ContinueStmt:
    line: int
    col: int


@dataclass
class ReturnStmt:
    value: Expr | None
    line: int
    col: int


@dataclass
class ForStmt:
    # for i in start..end:  — end exklusiv, i unveränderlich im Rumpf
    var: str
    start: Expr
    end: Expr
    body: "Block"
    line: int
    col: int
    var_type: str | None = None     # füllt der Checker
    end_const: int | None = None    # konstantes Ende (für Check-Eliminierung)


@dataclass
class MatchArm:
    variant: str
    bindings: list[str]     # gebundene Namen, [] bei datenlosen Varianten
    body: "Block"
    line: int
    col: int
    # füllt der Checker: Typen der Bindungen (parallel zu bindings)
    binding_types: list[str] = field(default_factory=list)


@dataclass
class MatchStmt:
    expr: Expr
    arms: list[MatchArm]
    line: int
    col: int
    enum_name: str | None = None  # füllt der Checker


@dataclass
class SendStmt:
    # msg ->> Aktor — Statement, Pfeil zeigt zum Empfänger (§9)
    msg: Expr
    target: str
    line: int
    col: int
    enum_name: str | None = None    # füllt der Checker


Stmt = (LetStmt | AssignStmt | CallStmt | IfStmt | WhileStmt | LoopStmt
        | RepeatStmt | BreakStmt | ContinueStmt | ReturnStmt | MatchStmt
        | ForStmt | SendStmt)


@dataclass
class Block:
    statements: list[Stmt]


# --- Toplevel ----------------------------------------------------------------

@dataclass
class Param:
    name: str
    typ: str
    line: int
    col: int
    mutable: bool = False   # 'mut'-Parameter (§7.2); bei self: 'mut self'


@dataclass
class ExternDecl:
    name: str
    params: list[Param]
    return_type: str | None
    line: int
    col: int


@dataclass
class FnDef:
    name: str
    params: list[Param]
    return_type: str | None
    body: Block
    line: int
    col: int


@dataclass
class VariantDef:
    name: str
    params: list["Param"]   # Datenfelder (name: typ), [] = datenlos
    line: int
    col: int


@dataclass
class EnumDef:
    name: str
    variants: list[VariantDef]
    line: int
    col: int


@dataclass
class FieldDef:
    name: str
    typ: str
    default: Expr | None    # konstanter Ausdruck oder None
    line: int
    col: int
    default_value: int | bool | None = None  # füllt der Checker (Folding)


@dataclass
class StructDef:
    name: str
    fields: list[FieldDef]
    methods: list["FnDef"]  # erster Parameter ist self (Param name="self")
    line: int
    col: int


@dataclass
class ConstDecl:
    name: str
    typ: str           # Annotation ist Pflicht (Konstanten haben keinen Kontext)
    value: Expr
    line: int
    col: int
    const_value: int | bool | None = None  # füllt der Checker aus (Constant Folding)


@dataclass
class ActorDef:
    name: str
    # Kopf-Argumente roh (Namen prüft der Checker, nicht die Grammatik)
    head_names: list[str | None]
    head_args: list[Expr]
    fields: list[FieldDef]
    methods: list[FnDef]
    line: int
    col: int
    mailbox_size: int | None = None     # füllt der Checker
    policy: str | None = None           # "Drop" | "DropOldest" | "Panic"
    msg_enum: str | None = None         # Enum-Typ des receive-Handlers


@dataclass
class Program:
    toplevels: list["ExternDecl | FnDef | ConstDecl | StructDef | EnumDef"] = \
        field(default_factory=list)
    # füllt der Checker: alle verwendeten Result[T,E]-Instanzierungen
    result_types: list[str] = field(default_factory=list)
    # füllt der Checker: alle verwendeten strbuf-Kapazitäten
    strbuf_caps: list[int] = field(default_factory=list)
