from .constants import (
    FX_ASSIGN_OPS,
    FX_BINARY_PRECEDENCE,
    FX_QUALIFIERS,
)
from .errors import FxError


# Expression half of the parser: precedence climbing for binary operators, then unary, cast, postfix and
# primary expressions.
class FxExpressionParser:
    def parse_expression(self):
        first = self.parse_assign()
        if self.text != ",":
            return first
        items = [first]
        while self.accept(","):
            items.append(self.parse_assign())
        return ("comma", items)

    def parse_assign(self):
        left = self.parse_ternary()
        if self.kind == "op" and self.text in FX_ASSIGN_OPS:
            op = self.advance()[1]
            return ("assign", op, left, self.parse_assign())
        return left

    def parse_ternary(self):
        condition = self.parse_binary(1)
        if self.accept("?"):
            a = self.parse_assign()
            self.expect(":")
            b = self.parse_assign()
            return ("tern", condition, a, b)
        return condition

    def parse_binary(self, minimum):
        left = self.parse_unary()
        while True:
            op = self.text
            precedence = FX_BINARY_PRECEDENCE.get(op) if self.kind == "op" else None
            if precedence is None or precedence < minimum:
                return left
            self.advance()
            right = self.parse_binary(precedence + 1)
            left = ("bin", op, left, right)

    # Decides whether '(' starts a C-style cast. It does when the parenthesis holds a known type name;
    # otherwise it is an ordinary parenthesised expression.
    def cast_ahead(self):
        if self.text != "(" or self.peek(1)[0] != "id":
            return None
        offset = 1
        while self.peek(offset)[0] == "id" and self.peek(offset)[1] in FX_QUALIFIERS:
            offset += 1
        name = self.peek(offset)[1]
        if not self.program.is_type(name) or name.lower() in ("texture", "sampler", "string", "void"):
            return None
        if self.peek(offset + 1)[1] == ")":
            return offset + 2
        if self.peek(offset + 1)[1] == "[":
            return None
        return None

    def parse_unary(self):
        if self.kind == "op":
            op = self.text
            if op in ("-", "+", "!", "~"):
                self.advance()
                return ("un", op, self.parse_unary())
            if op in ("++", "--"):
                self.advance()
                return ("pre", op, self.parse_unary())
            skip = self.cast_ahead()
            if skip is not None:
                self.advance()
                self.parse_qualifiers()
                type_name = self.parse_type()
                self.expect(")")
                return ("cast", type_name, self.parse_unary())
        return self.parse_postfix(self.parse_primary())

    def parse_primary(self):
        kind, text = self.advance()
        if kind == "num":
            raw = text.rstrip("uUlLhH")
            if raw.lower().startswith("0x"):
                return ("num", float(int(raw, 16)), "i")
            if raw.endswith(("f", "F")):
                return ("num", float(raw[:-1]), "f")
            if "." in raw or "e" in raw or "E" in raw:
                return ("num", float(raw), "f")
            return ("num", float(int(raw)), "i")
        if kind == "str":
            parts = [text[1:-1]]
            while self.kind == "str":
                parts.append(self.advance()[1][1:-1])
            joined = "".join(parts)
            return ("str", joined.replace('\\"', '"').replace("\\\\", "\\"))
        if kind == "id":
            if text in ("true", "TRUE"):
                return ("num", 1.0, "b")
            if text in ("false", "FALSE"):
                return ("num", 0.0, "b")
            if self.text == "(" and self.program.is_type(text):
                return ("ctor", self.program.typedefs.get(text, text), self.parse_args())
            if self.text == "<" and text.lower() in ("vector", "matrix"):
                self.pos -= 1
                type_name = self.parse_type()
                return ("ctor", type_name, self.parse_args())
            if self.text == "(":
                return ("call", text, self.parse_args())
            return ("id", text)
        if text == "(":
            inner = self.parse_expression()
            self.expect(")")
            return inner
        if text == "{":
            self.pos -= 1
            return self.parse_initializer()
        raise FxError("unexpected token '%s' (token %d)" % (text, self.pos))

    def parse_args(self):
        self.expect("(")
        args = []
        if self.accept(")"):
            return args
        while True:
            args.append(self.parse_assign())
            if self.accept(")"):
                return args
            self.expect(",")

    def parse_postfix(self, node):
        while True:
            if self.accept("."):
                name = self.ident()
                if self.text == "(":
                    node = ("mcall", node, name, self.parse_args())
                else:
                    node = ("member", node, name)
            elif self.accept("["):
                index = self.parse_expression()
                self.expect("]")
                node = ("index", node, index)
            elif self.kind == "op" and self.text in ("++", "--"):
                node = ("post", self.advance()[1], node)
            else:
                return node
