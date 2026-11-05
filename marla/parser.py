"""Rekursiver Abstieg, eine Funktion pro Grammatikregel (Anhang A)."""

from __future__ import annotations

from . import ast_nodes as ast
from .errors import CompileError
from .tokens import Token, TokenKind

K = TokenKind

CMP_OPS: dict[TokenKind, str] = {
    K.EQ: "=", K.NE: "<>", K.LT: "<", K.LE: "<=", K.GT: ">", K.GE: ">=",
}

ASSIGN_OPS: dict[TokenKind, str] = {
    K.ASSIGN: ":=", K.PLUSEQ: "+=", K.MINUSEQ: "-=", K.STAREQ: "*=",
    K.SLASHSLASHEQ: "//=", K.AMPEQ: "&=", K.PIPEEQ: "|=", K.CARETEQ: "^=",
}


class Parser:
    def __init__(self, tokens: list[Token], filename: str = "<eingabe>"):
        self.tokens = tokens
        self.pos = 0
        self.filename = filename

    # --- Infrastruktur --------------------------------------------------

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def at(self, kind: TokenKind) -> bool:
        return self.peek().kind is kind

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if tok.kind is not K.EOF:
            self.pos += 1
        return tok

    def error(self, message: str, tok: Token | None = None) -> CompileError:
        tok = tok or self.peek()
        return CompileError(message, tok.line, tok.col, self.filename)

    def describe(self, tok: Token) -> str:
        if tok.kind is K.NEWLINE:
            return "Zeilenende"
        if tok.kind is K.INDENT:
            return "Einrückung"
        if tok.kind is K.DEDENT:
            return "Ende des Blocks"
        if tok.kind is K.EOF:
            return "Dateiende"
        return f"'{tok.text}'"

    def expect(self, kind: TokenKind, what: str) -> Token:
        if not self.at(kind):
            raise self.error(f"erwartet {what}, gefunden {self.describe(self.peek())}")
        return self.advance()

    # --- Toplevel ---------------------------------------------------------

    def parse_program(self) -> ast.Program:
        program = ast.Program()
        while not self.at(K.EOF):
            if self.at(K.EXTERN):
                program.toplevels.append(self.parse_extern_decl())
            elif self.at(K.FN):
                program.toplevels.append(self.parse_fn_def())
            elif self.at(K.CONST):
                program.toplevels.append(self.parse_const_decl())
            elif self.at(K.STRUCT):
                program.toplevels.append(self.parse_struct_def())
            elif self.at(K.ENUM):
                program.toplevels.append(self.parse_enum_def())
            elif self.at(K.ACTOR):
                program.toplevels.append(self.parse_actor_def())
            else:
                raise self.error(
                    f"erwartet 'fn', 'extern', 'const', 'struct', 'enum' oder "
                    f"'actor' auf oberster Ebene, gefunden {self.describe(self.peek())}"
                )
        return program

    def parse_const_decl(self) -> ast.ConstDecl:
        start = self.advance()  # const
        name = self.expect(K.IDENT, "Konstantenname").text
        if not self.at(K.COLON):
            raise self.error(
                "Konstante braucht eine Typannotation: "
                f"'const {name}: u8 := ...' (kein Kontext für Inferenz)"
            )
        self.advance()
        typ = self.parse_type()
        if self.peek().kind in (K.EQ, K.EQEQ):
            raise self.error("Zuweisung schreibt sich ':=' — meintest du ':='?")
        self.expect(K.ASSIGN, "':='")
        value = self.parse_expr()
        self.expect(K.NEWLINE, "Zeilenende nach der Konstanten")
        return ast.ConstDecl(name, typ, value, start.line, start.col)

    def parse_extern_decl(self) -> ast.ExternDecl:
        start = self.expect(K.EXTERN, "'extern'")
        self.expect(K.FN, "'fn' nach 'extern'")
        name = self.expect(K.IDENT, "Funktionsname").text
        params = self.parse_param_list()
        ret = self.parse_opt_return_type()
        self.expect(K.NEWLINE, "Zeilenende ('extern fn' hat keinen Rumpf)")
        return ast.ExternDecl(name, params, ret, start.line, start.col)

    def parse_fn_def(self, allow_self: bool = False) -> ast.FnDef:
        start = self.expect(K.FN, "'fn'")
        name = self.expect(K.IDENT, "Funktionsname").text
        params = self.parse_param_list(allow_self=allow_self)
        ret = self.parse_opt_return_type()
        self.expect(K.COLON, "':' vor dem Funktionsrumpf")
        body = self.parse_block()
        return ast.FnDef(name, params, ret, body, start.line, start.col)

    def parse_struct_def(self) -> ast.StructDef:
        start = self.advance()  # struct
        name = self.expect(K.IDENT, "struct-Name").text
        self.expect(K.COLON, "':' nach dem struct-Namen")
        self.expect(K.NEWLINE, "Zeilenende vor dem struct-Rumpf")
        self.expect(K.INDENT, "eingerückten struct-Rumpf")
        fields: list[ast.FieldDef] = []
        methods: list[ast.FnDef] = []
        while not self.at(K.DEDENT):
            if self.at(K.FN):
                methods.append(self.parse_fn_def(allow_self=True))
            elif self.at(K.IDENT):
                fields.append(self.parse_field_def())
            else:
                raise self.error(
                    f"erwartet Feld oder Methode im struct, "
                    f"gefunden {self.describe(self.peek())}"
                )
        self.advance()  # DEDENT
        return ast.StructDef(name, fields, methods, start.line, start.col)

    def parse_actor_def(self) -> ast.ActorDef:
        start = self.advance()  # actor
        name = self.expect(K.IDENT, "Aktor-Name").text
        # Kopf: benannte Argumente — actor Motor(mailbox: 8, full: DropOldest):
        args, names, muts = self.parse_arguments()
        if any(muts):
            raise self.error("'mut' hat im Aktor-Kopf nichts zu suchen", start)
        self.expect(K.COLON, "':' nach dem Aktor-Kopf")
        self.expect(K.NEWLINE, "Zeilenende vor dem Aktor-Rumpf")
        self.expect(K.INDENT, "eingerückten Aktor-Rumpf")
        fields: list[ast.FieldDef] = []
        methods: list[ast.FnDef] = []
        while not self.at(K.DEDENT):
            if self.at(K.FN):
                methods.append(self.parse_fn_def(allow_self=True))
            elif self.at(K.IDENT):
                fields.append(self.parse_field_def())
            else:
                raise self.error(
                    f"erwartet Zustandsfeld oder Methode im Aktor, "
                    f"gefunden {self.describe(self.peek())}"
                )
        self.advance()  # DEDENT
        return ast.ActorDef(name, names, args, fields, methods,
                            start.line, start.col)

    def parse_enum_def(self) -> ast.EnumDef:
        start = self.advance()  # enum
        name = self.expect(K.IDENT, "enum-Name").text
        self.expect(K.COLON, "':' nach dem enum-Namen")
        self.expect(K.NEWLINE, "Zeilenende vor dem enum-Rumpf")
        self.expect(K.INDENT, "eingerückte Varianten")
        variants: list[ast.VariantDef] = []
        while not self.at(K.DEDENT):
            v_tok = self.expect(K.IDENT, "Variantenname")
            params: list[ast.Param] = []
            if self.at(K.LPAREN):
                params = self.parse_param_list()
                if not params:
                    raise self.error(
                        f"datenlose Variante ohne Klammern schreiben: "
                        f"'{v_tok.text}' statt '{v_tok.text}()'", v_tok)
            self.expect(K.NEWLINE, "Zeilenende nach der Variante")
            variants.append(ast.VariantDef(v_tok.text, params, v_tok.line, v_tok.col))
        self.advance()  # DEDENT
        if not variants:
            raise self.error("enum braucht mindestens eine Variante", start)
        return ast.EnumDef(name, variants, start.line, start.col)

    def parse_match(self) -> ast.MatchStmt:
        start = self.advance()  # match
        expr = self.parse_expr()
        self.expect(K.COLON, "':' nach dem match-Ausdruck")
        self.expect(K.NEWLINE, "Zeilenende vor den match-Zweigen")
        self.expect(K.INDENT, "eingerückte match-Zweige")
        arms: list[ast.MatchArm] = []
        while not self.at(K.DEDENT):
            v_tok = self.expect(K.IDENT, "Variantenname im match")
            bindings: list[str] = []
            if self.at(K.LPAREN):
                self.advance()
                bindings.append(self.expect(K.IDENT, "Bindungsname").text)
                while self.at(K.COMMA):
                    self.advance()
                    bindings.append(self.expect(K.IDENT, "Bindungsname").text)
                self.expect(K.RPAREN, "')'")
            self.expect(K.COLON, "':' nach dem Muster")
            body = self.parse_block()
            arms.append(ast.MatchArm(v_tok.text, bindings, body, v_tok.line, v_tok.col))
        self.advance()  # DEDENT
        if not arms:
            raise self.error("match braucht mindestens einen Zweig", start)
        return ast.MatchStmt(expr, arms, start.line, start.col)

    def parse_field_def(self) -> ast.FieldDef:
        name_tok = self.advance()  # IDENT
        self.expect(K.COLON, "':' zwischen Feldname und Typ")
        typ = self.parse_type()
        default: ast.Expr | None = None
        if self.at(K.ASSIGN):
            self.advance()
            default = self.parse_expr()
        elif self.peek().kind in (K.EQ, K.EQEQ):
            raise self.error("Default-Wert schreibt sich ':=' — meintest du ':='?")
        self.expect(K.NEWLINE, "Zeilenende nach dem Feld")
        return ast.FieldDef(name_tok.text, typ, default, name_tok.line, name_tok.col)

    def parse_param_list(self, allow_self: bool = False) -> list[ast.Param]:
        self.expect(K.LPAREN, "'('")
        params: list[ast.Param] = []
        if not self.at(K.RPAREN):
            params.append(self.parse_param(allow_self=allow_self and not params))
            while self.at(K.COMMA):
                self.advance()
                params.append(self.parse_param(allow_self=False))
        self.expect(K.RPAREN, "')'")
        return params

    def parse_param(self, allow_self: bool = False) -> ast.Param:
        # self / mut self nur als erster Parameter einer Methode
        if self.at(K.MUT) and self.peek(1).kind is K.SELF:
            mut_tok = self.advance()
            self.advance()
            if not allow_self:
                raise self.error("'self' ist nur als erster Parameter einer "
                                 "Methode erlaubt", mut_tok)
            return ast.Param("self", "", mut_tok.line, mut_tok.col, mutable=True)
        if self.at(K.SELF):
            tok = self.advance()
            if not allow_self:
                raise self.error("'self' ist nur als erster Parameter einer "
                                 "Methode erlaubt", tok)
            return ast.Param("self", "", tok.line, tok.col, mutable=False)
        name_tok = self.expect(K.IDENT, "Parametername")
        self.expect(K.COLON, "':' zwischen Parametername und Typ")
        mutable = False
        if self.at(K.MUT):
            self.advance()
            mutable = True
        typ = self.parse_type()
        return ast.Param(name_tok.text, typ, name_tok.line, name_tok.col, mutable=mutable)

    def parse_opt_return_type(self) -> str | None:
        if self.at(K.ARROW):
            self.advance()
            return self.parse_type()
        return None

    def parse_type(self) -> str:
        tok = self.peek()
        if tok.kind is K.TYPE:
            base = self.advance().text
        elif tok.kind is K.IDENT:
            # struct-/enum-Name; ob er existiert, prüft der Checker
            base = self.advance().text
            if base == "Result":
                self.expect(K.LBRACKET, "'[' — Result braucht Typargumente: Result[T, E]")
                ok_t = self.parse_type()
                self.expect(K.COMMA, "',' zwischen Ok- und Fehlertyp")
                err_t = self.parse_type()
                self.expect(K.RBRACKET, "']'")
                return f"Result[{ok_t},{err_t}]"
        else:
            raise self.error(
                "erwartet Typ (u8, i8, u16, i16, u32, i32, bool oder struct-Name), "
                f"gefunden {self.describe(tok)}"
            )
        # Array-Suffixe: u8[16], u8[SIZE], u8[8][8] — Größe ist Zahl oder
        # Konstantenname (löst der Checker auf)
        while self.at(K.LBRACKET):
            self.advance()
            size_tok = self.peek()
            if size_tok.kind is K.NUMBER:
                self.advance()
                base += f"[{size_tok.value}]"
            elif size_tok.kind is K.IDENT:
                self.advance()
                base += f"[{size_tok.text}]"
            else:
                raise self.error(
                    "Arraygröße muss eine Zahl oder ein Konstantenname sein")
            self.expect(K.RBRACKET, "']'")
        return base

    # --- Blöcke und Anweisungen -------------------------------------------

    def parse_block(self) -> ast.Block:
        self.expect(K.NEWLINE, "Zeilenende vor dem eingerückten Block")
        self.expect(K.INDENT, "eingerückten Block")
        statements = [self.parse_statement()]
        while not self.at(K.DEDENT):
            statements.append(self.parse_statement())
        self.advance()  # DEDENT
        return ast.Block(statements)

    def parse_statement(self) -> ast.Stmt:
        tok = self.peek()
        if tok.kind is K.LET:
            return self.parse_let()
        if tok.kind is K.IF:
            return self.parse_if()
        if tok.kind is K.WHILE:
            return self.parse_while()
        if tok.kind is K.LOOP:
            return self.parse_loop()
        if tok.kind is K.REPEAT:
            return self.parse_repeat()
        if tok.kind is K.MATCH:
            return self.parse_match()
        if tok.kind is K.FOR:
            return self.parse_for()
        if tok.kind is K.UNTIL:
            raise self.error("'until' ohne zugehöriges 'repeat'")
        if tok.kind is K.BREAK:
            self.advance()
            self.expect(K.NEWLINE, "Zeilenende nach 'break'")
            return ast.BreakStmt(tok.line, tok.col)
        if tok.kind is K.CONTINUE:
            self.advance()
            self.expect(K.NEWLINE, "Zeilenende nach 'continue'")
            return ast.ContinueStmt(tok.line, tok.col)
        if tok.kind is K.RETURN:
            return self.parse_return()
        if tok.kind in (K.IDENT, K.SELF):
            return self.parse_assign_or_call()
        if tok.kind is K.CONST:
            raise self.error(
                "'const' ist nur auf oberster Ebene erlaubt — in Funktionen 'let' verwenden"
            )
        raise self.error(f"erwartet Anweisung, gefunden {self.describe(tok)}")

    def parse_let(self) -> ast.LetStmt:
        start = self.advance()  # let
        mutable = False
        if self.at(K.MUT):
            self.advance()
            mutable = True
        name = self.expect(K.IDENT, "Variablenname").text
        declared: str | None = None
        if self.at(K.COLON):
            self.advance()
            declared = self.parse_type()
        if self.peek().kind in (K.EQ, K.EQEQ):
            raise self.error("Zuweisung schreibt sich ':=' — meintest du ':='?")
        # Arrays/Puffer: Deklaration ohne Initialisierer (Null-Init), z. B.
        # let mut samples: u8[16] / let mut line: strbuf[64]
        if self.at(K.NEWLINE) and declared is not None \
                and ("[" in declared or declared == "strbuf"):
            self.advance()
            return ast.LetStmt(name, mutable, declared, None, start.line, start.col)
        self.expect(K.ASSIGN, "':='")
        value = self.parse_expr()
        self.expect(K.NEWLINE, "Zeilenende nach der Deklaration")
        return ast.LetStmt(name, mutable, declared, value, start.line, start.col)

    def parse_assign_or_call(self) -> ast.Stmt:
        start = self.peek()
        if start.kind is K.SELF:
            self.advance()
            expr: ast.Expr = ast.NameExpr("self", start.line, start.col)
        else:
            name_tok = self.expect(K.IDENT, "Name")
            if self.at(K.LPAREN):
                expr = self.parse_call_expr(name_tok)
            else:
                expr = ast.NameExpr(name_tok.text, name_tok.line, name_tok.col)
        expr = self.parse_postfix_chain(expr)

        tok = self.peek()
        if tok.kind is K.SENDARROW:
            self.advance()
            target = self.expect(K.IDENT, "Aktor-Name nach '->>'").text
            self.expect(K.NEWLINE, "Zeilenende nach dem Senden")
            return ast.SendStmt(expr, target, start.line, start.col)
        if (isinstance(expr, (ast.CallExpr, ast.MethodCall))
                or (isinstance(expr, ast.QuestionExpr)
                    and isinstance(expr.operand, (ast.CallExpr, ast.MethodCall)))) \
                and tok.kind is K.NEWLINE:
            self.advance()
            return ast.CallStmt(expr, start.line, start.col)
        if tok.kind in ASSIGN_OPS:
            if not isinstance(expr, (ast.NameExpr, ast.FieldAccess, ast.IndexExpr)):
                raise self.error(
                    "linke Seite einer Zuweisung muss eine Variable, ein Feld "
                    "oder ein Array-Element sein, kein Aufrufergebnis", tok)
            op = ASSIGN_OPS[tok.kind]
            self.advance()
            value = self.parse_expr()
            self.expect(K.NEWLINE, "Zeilenende nach der Zuweisung")
            return ast.AssignStmt(expr, op, value, start.line, start.col)
        if tok.kind in (K.EQ, K.EQEQ):
            raise self.error("Zuweisung schreibt sich ':=' — meintest du ':='?", tok)
        if isinstance(expr, (ast.CallExpr, ast.MethodCall)):
            raise self.error(
                f"erwartet Zeilenende nach dem Aufruf, gefunden {self.describe(tok)}")
        raise self.error(
            f"erwartet ':=' (Zuweisung), '.' (Feld/Methode) oder '(' (Aufruf), "
            f"gefunden {self.describe(tok)}"
        )

    def parse_call_expr(self, name_tok: Token) -> ast.CallExpr:
        args, names, muts = self.parse_arguments()
        return ast.CallExpr(name_tok.text, args, name_tok.line, name_tok.col,
                            names=names, muts=muts)

    def parse_arguments(self) -> tuple[list[ast.Expr], list[str | None], list[bool]]:
        self.expect(K.LPAREN, "'('")
        args: list[ast.Expr] = []
        names: list[str | None] = []
        muts: list[bool] = []
        if not self.at(K.RPAREN):
            self.parse_one_argument(args, names, muts)
            while self.at(K.COMMA):
                self.advance()
                self.parse_one_argument(args, names, muts)
        self.expect(K.RPAREN, "')'")
        return args, names, muts

    def parse_one_argument(self, args, names, muts) -> None:
        name: str | None = None
        if self.peek().kind is K.IDENT and self.peek(1).kind is K.COLON:
            name = self.advance().text
            self.advance()  # :
        mut = False
        if self.at(K.MUT):
            self.advance()
            mut = True
        args.append(self.parse_expr())
        names.append(name)
        muts.append(mut)

    def parse_postfix_chain(self, expr: ast.Expr) -> ast.Expr:
        while True:
            if self.at(K.DOT):
                dot = self.advance()
                name = self.expect(K.IDENT, "Feld- oder Methodenname nach '.'").text
                if self.at(K.LPAREN):
                    args, names, muts = self.parse_arguments()
                    expr = ast.MethodCall(expr, name, args, dot.line, dot.col,
                                          names=names, muts=muts)
                else:
                    expr = ast.FieldAccess(expr, name, dot.line, dot.col)
            elif self.at(K.LBRACKET):
                bracket = self.advance()
                index = self.parse_expr()
                self.expect(K.RBRACKET, "']'")
                expr = ast.IndexExpr(expr, index, bracket.line, bracket.col)
            elif self.at(K.QUESTION):
                q = self.advance()
                return ast.QuestionExpr(expr, q.line, q.col)  # '?' beendet die Kette
            else:
                return expr

    def parse_if(self) -> ast.IfStmt:
        start = self.advance()  # if
        branches: list[tuple[ast.Expr, ast.Block]] = []
        cond = self.parse_expr()
        self.expect(K.COLON, "':' nach der Bedingung")
        branches.append((cond, self.parse_block()))
        while self.at(K.ELIF):
            self.advance()
            cond = self.parse_expr()
            self.expect(K.COLON, "':' nach der Bedingung")
            branches.append((cond, self.parse_block()))
        else_block = None
        if self.at(K.ELSE):
            self.advance()
            self.expect(K.COLON, "':' nach 'else'")
            else_block = self.parse_block()
        return ast.IfStmt(branches, else_block, start.line, start.col)

    def parse_while(self) -> ast.WhileStmt:
        start = self.advance()
        cond = self.parse_expr()
        self.expect(K.COLON, "':' nach der Bedingung")
        return ast.WhileStmt(cond, self.parse_block(), start.line, start.col)

    def parse_loop(self) -> ast.LoopStmt:
        start = self.advance()
        self.expect(K.COLON, "':' nach 'loop'")
        return ast.LoopStmt(self.parse_block(), start.line, start.col)

    def parse_for(self) -> ast.ForStmt:
        start = self.advance()  # for
        var = self.expect(K.IDENT, "Schleifenvariable").text
        self.expect(K.IN, "'in' nach der Schleifenvariable")
        start_expr = self.parse_expr()
        self.expect(K.DOTDOT, "'..' im Bereich (z. B. 0..10)")
        end_expr = self.parse_expr()
        self.expect(K.COLON, "':' nach dem Bereich")
        body = self.parse_block()
        return ast.ForStmt(var, start_expr, end_expr, body, start.line, start.col)

    def parse_repeat(self) -> ast.RepeatStmt:
        # Erste Konstruktion, bei der nach dem eingerückten Block noch eine
        # Zeile zur Schleife gehört ('until' auf Ausgangsebene, wie 'else').
        start = self.advance()
        self.expect(K.COLON, "':' nach 'repeat'")
        body = self.parse_block()
        self.expect(K.UNTIL, "'until' nach dem 'repeat'-Block")
        cond = self.parse_expr()
        self.expect(K.NEWLINE, "Zeilenende nach der 'until'-Bedingung")
        return ast.RepeatStmt(body, cond, start.line, start.col)

    def parse_return(self) -> ast.ReturnStmt:
        start = self.advance()
        value: ast.Expr | None = None
        if not self.at(K.NEWLINE):
            value = self.parse_expr()
        self.expect(K.NEWLINE, "Zeilenende nach 'return'")
        return ast.ReturnStmt(value, start.line, start.col)

    # --- Ausdrücke: eine Funktion pro Rangstufe, schwächste zuerst ---------

    def parse_expr(self) -> ast.Expr:
        return self.parse_or()

    def parse_or(self) -> ast.Expr:
        left = self.parse_and()
        while self.at(K.OR):
            op_tok = self.advance()
            right = self.parse_and()
            left = ast.BinaryOp("or", left, right, op_tok.line, op_tok.col)
        return left

    def parse_and(self) -> ast.Expr:
        left = self.parse_not()
        while self.at(K.AND):
            op_tok = self.advance()
            right = self.parse_not()
            left = ast.BinaryOp("and", left, right, op_tok.line, op_tok.col)
        return left

    def parse_not(self) -> ast.Expr:
        if self.at(K.NOT):
            op_tok = self.advance()
            operand = self.parse_not()
            return ast.UnaryOp("not", operand, op_tok.line, op_tok.col)
        return self.parse_comparison()

    def _check_cmp_typos(self) -> None:
        tok = self.peek()
        if tok.kind is K.EQEQ:
            raise self.error("'==' gibt es nicht — meintest du '='?", tok)
        if tok.kind is K.BANGEQ:
            raise self.error("'!=' gibt es nicht — meintest du '<>'?", tok)

    def parse_comparison(self) -> ast.Expr:
        self._check_cmp_typos()
        left = self.parse_bitor()
        self._check_cmp_typos()
        if self.peek().kind in CMP_OPS:
            op_tok = self.advance()
            right = self.parse_bitor()
            self._check_cmp_typos()
            if self.peek().kind in CMP_OPS:  # KEIN Chaining
                raise self.error(
                    "Vergleiche können nicht verkettet werden — "
                    "schreibe z. B. '0 <= x and x < 10'"
                )
            return ast.BinaryOp(CMP_OPS[op_tok.kind], left, right, op_tok.line, op_tok.col)
        return left

    def parse_bitor(self) -> ast.Expr:
        left = self.parse_bitxor()
        while self.at(K.PIPE):
            op_tok = self.advance()
            left = ast.BinaryOp("|", left, self.parse_bitxor(), op_tok.line, op_tok.col)
        return left

    def parse_bitxor(self) -> ast.Expr:
        left = self.parse_bitand()
        while self.at(K.CARET):
            op_tok = self.advance()
            left = ast.BinaryOp("^", left, self.parse_bitand(), op_tok.line, op_tok.col)
        return left

    def parse_bitand(self) -> ast.Expr:
        left = self.parse_shift()
        while self.at(K.AMP):
            op_tok = self.advance()
            left = ast.BinaryOp("&", left, self.parse_shift(), op_tok.line, op_tok.col)
        return left

    def parse_shift(self) -> ast.Expr:
        left = self.parse_add()
        while self.peek().kind in (K.LSHIFT, K.RSHIFT):
            op_tok = self.advance()
            op = "<<" if op_tok.kind is K.LSHIFT else ">>"
            left = ast.BinaryOp(op, left, self.parse_add(), op_tok.line, op_tok.col)
        return left

    def parse_add(self) -> ast.Expr:
        left = self.parse_mul()
        while self.peek().kind in (K.PLUS, K.MINUS):
            op_tok = self.advance()
            op = "+" if op_tok.kind is K.PLUS else "-"
            left = ast.BinaryOp(op, left, self.parse_mul(), op_tok.line, op_tok.col)
        return left

    def parse_mul(self) -> ast.Expr:
        left = self.parse_unary()
        while True:
            tok = self.peek()
            if tok.kind is K.PERCENT:
                raise self.error("'%' gibt es nicht — meintest du 'mod'?", tok)
            if tok.kind is K.STAR:
                op = "*"
            elif tok.kind is K.SLASHSLASH:
                op = "//"
            elif tok.kind is K.MOD:
                op = "mod"
            else:
                return left
            self.advance()
            left = ast.BinaryOp(op, left, self.parse_unary(), tok.line, tok.col)

    def parse_unary(self) -> ast.Expr:
        if self.at(K.MINUS):
            op_tok = self.advance()
            operand = self.parse_unary()
            return ast.UnaryOp("-", operand, op_tok.line, op_tok.col)
        return self.parse_cast()

    def parse_cast(self) -> ast.Expr:
        expr = self.parse_postfix()
        while self.at(K.AS):
            as_tok = self.advance()
            target = self.parse_type()
            expr = ast.CastExpr(expr, target, as_tok.line, as_tok.col)
        return expr

    def parse_postfix(self) -> ast.Expr:
        return self.parse_postfix_chain(self.parse_primary())

    def parse_primary(self) -> ast.Expr:
        tok = self.peek()
        if tok.kind is K.NUMBER:
            self.advance()
            assert tok.value is not None
            return ast.NumberLit(tok.value, tok.line, tok.col)
        if tok.kind is K.BYTE:
            self.advance()
            assert tok.value is not None
            return ast.NumberLit(tok.value, tok.line, tok.col, typ="u8")
        if tok.kind is K.STRING:
            self.advance()
            assert tok.bytes_value is not None
            return ast.StrLit(tok.bytes_value, tok.line, tok.col)
        if tok.kind is K.TRUE:
            self.advance()
            return ast.BoolLit(True, tok.line, tok.col)
        if tok.kind is K.FALSE:
            self.advance()
            return ast.BoolLit(False, tok.line, tok.col)
        if tok.kind is K.SELF:
            self.advance()
            return ast.NameExpr("self", tok.line, tok.col)
        if tok.kind is K.IDENT:
            self.advance()
            if self.at(K.LPAREN):
                return self.parse_call_expr(tok)
            return ast.NameExpr(tok.text, tok.line, tok.col)
        if tok.kind is K.LPAREN:
            self.advance()
            expr = self.parse_expr()
            self.expect(K.RPAREN, "')'")
            return expr
        raise self.error(f"erwartet Ausdruck, gefunden {self.describe(tok)}")


def parse(tokens: list[Token], filename: str = "<eingabe>") -> ast.Program:
    return Parser(tokens, filename).parse_program()
