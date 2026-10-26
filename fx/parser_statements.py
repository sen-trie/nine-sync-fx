from .constants import FX_QUALIFIERS
from .errors import FxError


# Statement half of the parser: blocks, control flow, local declarations and expression statements.
class FxStatementParser:
    def parse_block(self):
        self.expect("{")
        statements = []
        while not self.accept("}"):
            if self.kind == "eof":
                raise FxError("unexpected end of file in block")
            statements.append(self.parse_statement())
        return ("block", statements)

    def looks_like_declaration(self):
        if self.kind != "id":
            return False
        offset = 0
        while self.peek(offset)[0] == "id" and self.peek(offset)[1] in FX_QUALIFIERS:
            offset += 1
        kind, text = self.peek(offset)
        if kind != "id" or not self.program.is_type(text):
            return False
        nxt = self.peek(offset + 1)
        if nxt[1] == "<" and text.lower() in ("vector", "matrix"):
            return True
        return nxt[0] == "id"

    def parse_statement(self):
        text = self.text
        if self.kind == "str":
            return ("expr", self.parse_expression_statement())
        if text == "{":
            return self.parse_block()
        if text == ";":
            self.advance()
            return ("empty",)
        if text == "[":
            self.skip_attribute()
            return self.parse_statement()
        if text == "if":
            self.advance()
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            then = self.parse_statement()
            other = None
            if self.accept("else"):
                other = self.parse_statement()
            return ("if", condition, then, other)
        if text == "for":
            self.advance()
            self.expect("(")
            if self.accept(";"):
                init = ("empty",)
            elif self.looks_like_declaration():
                init = self.parse_local_declaration()
            else:
                init = ("expr", self.parse_expression())
                self.expect(";")
            condition = None if self.text == ";" else self.parse_expression()
            self.expect(";")
            step = None if self.text == ")" else self.parse_expression()
            self.expect(")")
            return ("for", init, condition, step, self.parse_statement())
        if text == "while":
            self.advance()
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            return ("for", ("empty",), condition, None, self.parse_statement())
        if text == "do":
            self.advance()
            body = self.parse_statement()
            self.expect("while")
            self.expect("(")
            condition = self.parse_expression()
            self.expect(")")
            self.expect(";")
            return ("do", body, condition)
        if text == "return":
            self.advance()
            value = None if self.text == ";" else self.parse_expression()
            self.expect(";")
            return ("return", value)
        if text in ("break", "continue", "discard"):
            self.advance()
            self.expect(";")
            return (text,)
        if text == "struct":
            self.parse_struct()
            return ("empty",)
        if self.looks_like_declaration():
            return self.parse_local_declaration()
        return ("expr", self.parse_expression_statement())

    def parse_expression_statement(self):
        expression = self.parse_expression()
        self.expect(";")
        return expression

    def parse_local_declaration(self):
        quals = self.parse_qualifiers()
        type_name = self.parse_type()
        declarators = []
        while True:
            name = self.ident()
            array = None
            if self.accept("["):
                array = self.parse_expression() if self.text != "]" else None
                self.expect("]")
            if self.accept(":"):
                self.parse_semantic_after_colon()
            init = None
            if self.accept("="):
                init = self.parse_initializer() if self.text == "{" else self.parse_assign()
            declarators.append((name, array, init))
            if not self.accept(","):
                break
        self.expect(";")
        return ("decl", type_name, declarators, quals)
