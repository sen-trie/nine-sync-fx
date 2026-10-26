import os
import re

from .errors import FxError


FX_COMMENT = re.compile(r'"(?:[^"\\\n]|\\.)*"|//[^\n]*|/\*.*?\*/', re.S)


FX_PP_TOKEN = re.compile(
    r'"(?:[^"\\]|\\.)*"|[A-Za-z_]\w*|\d+\.?\d*(?:[eE][+-]?\d+)?[fFhHuUlL]*|\.\d+(?:[eE][+-]?\d+)?[fFhH]?|##|#|\s+|.',
    re.S,
)


FX_IF_SAFE = re.compile(r"^[\s\d()+\-*/%<>=!&|^~.xXa-fA-F]*$")


# MMD effects come in UTF-8, UTF-16 and Shift-JIS; try them in that order and never fail on a stray byte.
def fx_read_text(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for encoding in ("utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("cp932", errors="replace")


# Keeps string literals intact and keeps the line count of block comments so error positions and #-directives
# stay on their own lines.
def fx_strip_comments(text):
    def replace(match):
        found = match.group(0)
        if found.startswith('"'):
            return found
        if found.startswith("/*"):
            return "\n" * found.count("\n") or " "
        return ""

    return FX_COMMENT.sub(replace, text)


FX_PP_IDENT = re.compile(r"[A-Za-z_]\w*")


# Small C preprocessor (#define with arguments, # and ##, #if/#elif, #include). The .fx dialect needs it
# before parsing; files it reads are recorded for cache invalidation.
class FxPreprocessor:
    def __init__(self, root_dir, defines=None):
        self.root_dir = root_dir
        self.macros = {}
        self.files = []
        self.depth = 0
        for name, value in (defines or {}).items():
            self.macros[name] = (None, str(value))

    def run_file(self, path):
        self.files.append(os.path.abspath(path))
        return self.run_text(fx_read_text(path), os.path.dirname(os.path.abspath(path)))

    # Include names are matched case-insensitively because effects written on Windows often get the case
    # wrong.
    def resolve_include(self, name, base_dir):
        name = name.replace("\\", "/")
        for folder in (base_dir, self.root_dir):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
            folder_path = os.path.dirname(candidate)
            wanted = os.path.basename(candidate).lower()
            if os.path.isdir(folder_path):
                for entry in os.listdir(folder_path):
                    if entry.lower() == wanted:
                        return os.path.join(folder_path, entry)
        return None

    def run_text(self, text, base_dir):
        text = fx_strip_comments(text)
        text = text.replace("\\\r\n", "").replace("\\\n", "").replace("\r", "")
        lines = text.split("\n")
        out = []
        stack = []
        for line in lines:
            stripped = line.strip()
            active = stack[-1][2] if stack else True
            if stripped.startswith("#"):
                directive = re.match(r"#\s*(\w+)\s*(.*)", stripped, re.S)
                out.append("")
                if not directive:
                    continue
                name, rest = directive.group(1), directive.group(2).strip()
                if name in ("if", "ifdef", "ifndef"):
                    if not active:
                        condition = False
                    elif name == "ifdef":
                        condition = bool(rest) and rest.split()[0] in self.macros
                    elif name == "ifndef":
                        condition = not (bool(rest) and rest.split()[0] in self.macros)
                    else:
                        condition = self.eval_condition(rest)
                    stack.append([active, condition, active and condition])
                elif name == "elif":
                    if not stack:
                        continue
                    parent, taken, _current = stack[-1]
                    current = parent and not taken and self.eval_condition(rest)
                    stack[-1] = [parent, taken or current, current]
                elif name == "else":
                    if not stack:
                        continue
                    parent, taken, _current = stack[-1]
                    stack[-1] = [parent, True, parent and not taken]
                elif name == "endif":
                    if stack:
                        stack.pop()
                elif not active:
                    continue
                elif name == "define":
                    self.define(rest)
                elif name == "undef":
                    self.macros.pop(rest.split()[0] if rest else "", None)
                elif name == "include":
                    found = re.match(r'["<]([^">]+)[">]', rest)
                    if not found:
                        continue
                    path = self.resolve_include(found.group(1), base_dir)
                    if path is None:
                        raise FxError("include not found: %s" % found.group(1))
                    if self.depth > 16:
                        raise FxError("include nesting too deep")
                    self.depth += 1
                    self.files.append(os.path.abspath(path))
                    try:
                        out.append(
                            self.run_text(
                                fx_read_text(path), os.path.dirname(os.path.abspath(path))
                            )
                        )
                    finally:
                        self.depth -= 1
                continue
            out.append(self.expand(line) if active else "")
        return "\n".join(out)

    def define(self, rest):
        match = re.match(r"([A-Za-z_]\w*)(\([^)]*\))?\s*(.*)", rest, re.S)
        if not match:
            return
        params = None
        if match.group(2) is not None:
            params = [p.strip() for p in match.group(2)[1:-1].split(",") if p.strip()]
        self.macros[match.group(1)] = (params, match.group(3).strip())

    # Skips the token-level work when the line contains no macro name (most lines), which dominates parse time
    # on big effects.
    # 'hidden' stops a macro from expanding inside its own body.
    def expand(self, text, hidden=frozenset()):
        if not self.macros:
            return text
        if self.macros.keys().isdisjoint(FX_PP_IDENT.findall(text)):
            return text
        tokens = FX_PP_TOKEN.findall(text)
        out = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token in self.macros and token not in hidden and (token[0].isalpha() or token[0] == "_"):
                params, body = self.macros[token]
                if params is None:
                    out.append(self.expand(body, hidden | {token}))
                    i += 1
                    continue
                j = i + 1
                while j < len(tokens) and tokens[j].isspace():
                    j += 1
                if j < len(tokens) and tokens[j] == "(":
                    args, end = self.collect_args(tokens, j)
                    if args is not None:
                        expanded = [self.expand(a, hidden) for a in args]
                        body_text = self.substitute(body, params, args, expanded)
                        out.append(self.expand(body_text, hidden | {token}))
                        i = end
                        continue
            out.append(token)
            i += 1
        return "".join(out)

    def collect_args(self, tokens, start):
        depth = 0
        args = []
        current = []
        i = start
        while i < len(tokens):
            token = tokens[i]
            if token == "(":
                depth += 1
                if depth > 1:
                    current.append(token)
            elif token == ")":
                depth -= 1
                if depth == 0:
                    args.append("".join(current).strip())
                    return ([] if args == [""] else args), i + 1
                current.append(token)
            elif token == "," and depth == 1:
                args.append("".join(current).strip())
                current = []
            else:
                current.append(token)
            i += 1
        return None, start

    def substitute(self, body, params, raw, expanded):
        tokens = FX_PP_TOKEN.findall(body)
        out = []
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token == "#" and i + 1 < len(tokens) and tokens[i + 1] in params:
                out.append('"%s"' % raw[params.index(tokens[i + 1])])
                i += 2
                continue
            if token == "##":
                while out and out[-1].isspace():
                    out.pop()
                i += 1
                while i < len(tokens) and tokens[i].isspace():
                    i += 1
                if i < len(tokens):
                    nxt = tokens[i]
                    out.append(raw[params.index(nxt)] if nxt in params else nxt)
                    i += 1
                continue
            if token in params:
                index = params.index(token)
                out.append(expanded[index] if index < len(expanded) else "")
            else:
                out.append(token)
            i += 1
        return "".join(out)

    # Evaluates #if by rewriting the expression into Python after replacing unknown names with 0; anything
    # outside a safe character set is treated as false rather than evaluated.
    def eval_condition(self, expression):
        expression = re.sub(
            r"defined\s*\(\s*(\w+)\s*\)|defined\s+(\w+)",
            lambda m: "1" if (m.group(1) or m.group(2)) in self.macros else "0",
            expression,
        )
        expression = self.expand(expression)
        expression = re.sub(r"(\d+)[uUlL]+\b", r"\1", expression)
        expression = re.sub(r"\b(?!0[xX])[A-Za-z_]\w*\b", "0", expression)
        if not FX_IF_SAFE.match(expression):
            return False
        expression = expression.replace("&&", " and ").replace("||", " or ")
        expression = re.sub(r"!(?!=)", " not ", expression)
        expression = re.sub(r"(?<![/])/(?![/])", "//", expression)
        try:
            return bool(eval(expression, {"__builtins__": {}}, {}))
        except Exception:
            return False
