from .model import (
    XBlock,
    XError,
)
from .tokenizer import (
    XRun,
    x_number,
    x_run_tokens,
)


UNSUPPORTED_BLOCKS = (
    "SkinWeights",
    "XSkinMeshHeader",
    "AnimationSet",
    "Animation",
    "AnimTicksPerSecond",
)


# Skips a balanced { } block; used for template definitions, which carry no data.
def x_skip_block(tokens, i):
    depth = 0
    while i < len(tokens):
        if tokens[i] == "{":
            depth += 1
        elif tokens[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise XError("unbalanced braces")


# Builds the block tree: numbers and strings become block data, 'Type name {' opens a child, and '{ name }' is
# stored as a __ref__ child
# (a reference to a named Material defined elsewhere). An XRun stands for its number and separator tokens and gives
# the same tree as those tokens one by one.
def x_parse_body(tokens, i, block):
    while i < len(tokens):
        token = tokens[i]
        if token.__class__ is XRun:
            block.data.extend(token.values)
            i += 1
            continue
        if token == "}":
            return i + 1
        if token in (";", ","):
            i += 1
            continue
        if token == "{":
            j = i + 1
            ref = []
            while j < len(tokens) and tokens[j] != "}":
                if tokens[j].__class__ is XRun:
                    ref.extend(t for t in tokens[j].tokens() if t not in (";", ","))
                elif tokens[j] not in (";", ","):
                    ref.append(tokens[j])
                j += 1
            child = XBlock("__ref__", ref[0] if ref else "")
            block.children.append(child)
            i = j + 1
            continue
        if token.startswith('"'):
            block.data.append(token[1:-1])
            i += 1
            continue
        number = x_number(token)
        if number is not None:
            block.data.append(number)
            i += 1
            continue
        kind = token
        i += 1
        name = ""
        if i < len(tokens) and tokens[i] != "{":
            name = tokens[i]
            i += 1
            if name.__class__ is XRun:
                words = name.tokens()
                name = words[0]
                if len(words) > 1:
                    raise XError("expected '{' after %s" % kind)
        if i >= len(tokens) or tokens[i] != "{":
            raise XError("expected '{' after %s" % kind)
        if kind == "template":
            i = x_skip_block(tokens, i)
            continue
        i += 1
        child = XBlock(kind, name)
        if i < len(tokens) and tokens[i].__class__ is str and tokens[i].startswith("<") and tokens[i].endswith(">"):
            i += 1
        i = x_parse_body(tokens, i, child)
        block.children.append(child)
    return i


# Parses the whole text body into a root block whose children are the file's top-level blocks.
def x_parse_tree(text):
    root = XBlock("root", "")
    tokens = x_run_tokens(text)
    x_parse_body(tokens, 0, root)
    return root
