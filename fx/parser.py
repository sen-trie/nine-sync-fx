import re

from .constants import (
    FX_BINARY_PRECEDENCE,
    FX_QUALIFIERS,
)
from .errors import FxError
from .lexer import fx_tokenize
from .parser_expressions import FxExpressionParser
from .parser_statements import FxStatementParser
from .program import (
    FxFunction,
    FxGlobal,
    FxParam,
    FxPass,
    FxStruct,
    FxTechnique,
)


# Recursive-descent parser for the HLSL subset used by MME effects. Statements and expressions live in the two
# parser mixins.
# Expressions become tuples such as ('bin', op, a, b) that the interpreter walks.
class FxParser(FxStatementParser, FxExpressionParser):
    def __init__(self, tokens, program):
        self.tokens = tokens
        self.pos = 0
        self.program = program
        self.order = 0

    def peek(self, offset=0):
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    @property
    def text(self):
        return self.tokens[self.pos][1]

    @property
    def kind(self):
        return self.tokens[self.pos][0]

    def advance(self):
        token = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return token

    def accept(self, text):
        if self.kind != "str" and self.text == text:
            self.advance()
            return True
        return False

    def expect(self, text):
        if self.kind == "str" or self.text != text:
            raise FxError("expected '%s' but found '%s' (token %d)" % (text, self.text, self.pos))
        return self.advance()

    def ident(self):
        if self.kind != "id":
            raise FxError("expected identifier but found '%s' (token %d)" % (self.text, self.pos))
        return self.advance()[1]

    def parse(self):
        while self.kind != "eof":
            if self.accept(";"):
                continue
            if self.text == "struct":
                self.parse_struct()
            elif self.text in ("technique", "technique9", "technique10", "technique11"):
                self.parse_technique()
            elif self.text == "typedef":
                self.advance()
                quals = self.parse_qualifiers()
                type_name = self.parse_type()
                name = self.ident()
                self.program.typedefs[name] = type_name
                self.expect(";")
            elif self.text == "cbuffer" or self.text == "tbuffer":
                self.advance()
                self.ident()
                if self.accept(":"):
                    self.parse_semantic_after_colon()
                self.expect("{")
                while not self.accept("}"):
                    self.parse_declaration_or_function()
            else:
                self.parse_declaration_or_function()
        return self.program

    def parse_qualifiers(self):
        quals = set()
        while self.kind == "id" and self.text in FX_QUALIFIERS:
            quals.add(self.advance()[1])
        return quals

    def parse_type(self):
        name = self.ident()
        if name == "unsigned":
            name = self.ident() if self.kind == "id" and self.text in ("int", "short", "char") else "int"
            return "uint"
        if name == "struct":
            return self.ident()
        lower = name.lower()
        if lower in ("vector", "matrix") and self.text == "<":
            self.advance()
            base = self.ident()
            dims = []
            while self.accept(","):
                dims.append(self.advance()[1])
            self.expect(">")
            if lower == "vector":
                return "%s%s" % (base, dims[0] if dims else "4")
            return "%s%sx%s" % (base, dims[0] if dims else "4", dims[1] if len(dims) > 1 else "4")
        if lower in ("texture1d", "texture2d", "texture3d", "texturecube", "texture") and self.text == "<":
            self.advance()
            depth = 1
            while depth and self.kind != "eof":
                if self.text == "<":
                    depth += 1
                elif self.text == ">":
                    depth -= 1
                self.advance()
        return self.program.typedefs.get(name, name)

    def parse_struct(self):
        self.expect("struct")
        name = self.ident()
        struct = FxStruct(name)
        self.program.structs[name] = struct
        self.expect("{")
        while not self.accept("}"):
            quals = self.parse_qualifiers()
            type_name = self.parse_type()
            while True:
                member = self.ident()
                array = None
                if self.accept("["):
                    array = self.parse_expression()
                    self.expect("]")
                semantic = None
                if self.accept(":"):
                    semantic = self.parse_semantic_after_colon()[0]
                struct.members.append((member, type_name, semantic, array))
                if not self.accept(","):
                    break
            self.expect(";")
        if self.kind == "id":
            self.ident()
        self.accept(";")

    def parse_semantic_after_colon(self):
        name = self.ident()
        if name in ("register", "packoffset") and self.accept("("):
            parts = []
            while not self.accept(")"):
                parts.append(self.advance()[1])
            return None, "".join(parts)
        return name, None

    def parse_annotations(self):
        ann = {}
        if not self.accept("<"):
            return ann
        while not self.accept(">"):
            if self.accept(";"):
                continue
            self.parse_qualifiers()
            self.parse_type()
            name = self.ident()
            if self.accept("["):
                while not self.accept("]"):
                    self.advance()
            self.expect("=")
            ann[name] = self.parse_annotation_value()
            self.expect(";")
        return ann

    def parse_annotation_value(self):
        if self.text == "{":
            return self.parse_initializer()
        return self.parse_ternary_no_gt()

    # Annotations are wrapped in < >, so '>' must not be read as an operator inside them; the precedence table
    # is changed temporarily.
    def parse_ternary_no_gt(self):
        saved = FX_BINARY_PRECEDENCE.pop(">")
        saved_ge = FX_BINARY_PRECEDENCE.pop(">=")
        try:
            return self.parse_assign()
        finally:
            FX_BINARY_PRECEDENCE[">"] = saved
            FX_BINARY_PRECEDENCE[">="] = saved_ge

    def parse_declaration_or_function(self):
        if self.text == "[":
            self.skip_attribute()
        quals = self.parse_qualifiers()
        type_name = self.parse_type()
        if self.text == "(" and type_name not in self.program.structs:
            raise FxError("unexpected '(' after %s" % type_name)
        name = self.ident()
        if self.text == "(":
            self.parse_function(type_name, name)
            return
        while True:
            self.parse_global_declarator(quals, type_name, name)
            if not self.accept(","):
                break
            name = self.ident()
        self.expect(";")

    def skip_attribute(self):
        self.expect("[")
        depth = 1
        while depth and self.kind != "eof":
            if self.text == "[":
                depth += 1
            elif self.text == "]":
                depth -= 1
            self.advance()

    def parse_global_declarator(self, quals, type_name, name):
        array = None
        if self.accept("["):
            array = self.parse_expression() if self.text != "]" else None
            self.expect("]")
        semantic = None
        register = None
        while self.accept(":"):
            sem, reg = self.parse_semantic_after_colon()
            semantic = sem or semantic
            register = reg or register
        ann = self.parse_annotations()
        init = None
        if self.accept("="):
            if self.text.lower() == "sampler_state":
                self.advance()
                init = ("sampler_state", self.parse_state_block())
            elif self.text == "compile":
                init = self.parse_compile()
            elif self.text == "{":
                init = self.parse_initializer()
            else:
                init = self.parse_assign()
        elif self.text == "{" and type_name.lower().startswith("sampler"):
            init = ("sampler_state", self.parse_state_block())
        self.program.globals[name] = FxGlobal(
            name, type_name, quals, semantic.upper() if semantic else None, register, ann, init, array, self.order
        )
        self.order += 1

    # Sampler and pass state blocks keep their values as raw token lists; they are interpreted later where the
    # meaning is known.
    def parse_state_block(self):
        states = {}
        self.expect("{")
        while not self.accept("}"):
            if self.accept(";"):
                continue
            key = self.advance()[1]
            if self.accept("["):
                while not self.accept("]"):
                    self.advance()
            self.expect("=")
            value = []
            depth = 0
            while self.kind != "eof" and not (self.text == ";" and depth == 0):
                if self.text in "(<{":
                    depth += 1
                elif self.text in ")>}":
                    depth -= 1
                value.append(self.advance()[1])
            self.expect(";")
            states[key.lower()] = value
        return states

    # 'compile vs_3_0 Main(args)' is kept as ('compile', profile, call); entry points are resolved by name
    # afterwards.
    def parse_compile(self):
        self.expect("compile")
        profile = self.ident()
        call = self.parse_postfix(self.parse_primary())
        return ("compile", profile, call)

    def parse_initializer(self):
        self.expect("{")
        items = []
        while not self.accept("}"):
            if self.text == "{":
                items.append(self.parse_initializer())
            else:
                items.append(self.parse_assign())
            if not self.accept(","):
                self.expect("}")
                break
        return ("init", items)

    def parse_function(self, ret, name):
        self.expect("(")
        params = []
        if not self.accept(")"):
            if self.text == "void" and self.peek(1)[1] == ")":
                self.advance()
                self.expect(")")
            else:
                while True:
                    quals = self.parse_qualifiers()
                    type_name = self.parse_type()
                    pname = self.ident()
                    array = None
                    if self.accept("["):
                        array = self.parse_expression()
                        self.expect("]")
                    semantic = None
                    while self.accept(":"):
                        semantic = self.parse_semantic_after_colon()[0] or semantic
                    default = None
                    if self.accept("="):
                        default = self.parse_assign()
                    params.append(
                        FxParam(pname, type_name, semantic.upper() if semantic else None, quals, default, array)
                    )
                    if not self.accept(","):
                        break
                self.expect(")")
        semantic = None
        while self.accept(":"):
            semantic = self.parse_semantic_after_colon()[0] or semantic
        if self.accept(";"):
            return
        body = self.parse_block()
        self.program.functions.setdefault(name, []).append(
            FxFunction(name, ret, semantic.upper() if semantic else None, params, body)
        )

    def parse_technique(self):
        self.advance()
        name = self.ident() if self.kind == "id" else ""
        ann = self.parse_annotations()
        technique = FxTechnique(name, ann)
        self.expect("{")
        while not self.accept("}"):
            if self.accept(";"):
                continue
            self.expect("pass")
            pass_name = self.ident() if self.kind == "id" else ""
            pass_ann = self.parse_annotations()
            fx_pass = FxPass(pass_name, pass_ann)
            self.expect("{")
            while not self.accept("}"):
                if self.accept(";"):
                    continue
                key = self.advance()[1]
                if self.text == "(":
                    self.expect("(")
                    depth = 1
                    inner = []
                    while depth and self.kind != "eof":
                        if self.text == "(":
                            depth += 1
                        elif self.text == ")":
                            depth -= 1
                            if depth == 0:
                                self.advance()
                                break
                        inner.append(self.advance())
                    self.accept(";")
                    self.parse_set_shader(fx_pass, key, inner)
                    continue
                if self.accept("["):
                    while not self.accept("]"):
                        self.advance()
                self.expect("=")
                if self.text == "compile":
                    value = self.parse_compile()
                    self.expect(";")
                else:
                    value = []
                    while self.kind != "eof" and self.text != ";":
                        value.append(self.advance()[1])
                    self.expect(";")
                lower = key.lower()
                if lower == "vertexshader":
                    fx_pass.vs = value if isinstance(value, tuple) else None
                elif lower == "pixelshader":
                    fx_pass.ps = value if isinstance(value, tuple) else None
                else:
                    fx_pass.states[lower] = " ".join(value) if isinstance(value, list) else value
            technique.passes.append(fx_pass)
        self.program.techniques.append(technique)

    def parse_set_shader(self, fx_pass, key, inner):
        lower = key.lower()
        if lower not in ("setvertexshader", "setpixelshader"):
            return
        text = " ".join(t[1] for t in inner)
        match = re.match(r"CompileShader\s*\(\s*(\w+)\s*,\s*(.*)\)\s*$", text)
        if not match:
            return
        sub = FxParser(fx_tokenize(match.group(2)), self.program)
        call = sub.parse_postfix(sub.parse_primary())
        value = ("compile", match.group(1), call)
        if lower == "setvertexshader":
            fx_pass.vs = value
        else:
            fx_pass.ps = value














