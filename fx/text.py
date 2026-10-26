FX_TEXT_STATEMENTS = ("expr", "decl", "return", "if", "for", "do")


# Short name for an assignment target (a.b[2]) used in node labels.
def fx_target_label(node):
    kind = node[0]
    if kind == "id":
        return node[1]
    if kind == "member":
        return "%s.%s" % (fx_target_label(node[1]), node[2])
    if kind == "index":
        inner = node[2]
        index = inner[1] if inner[0] in ("num", "id") else "i"
        if isinstance(index, float) and index.is_integer():
            index = int(index)
        return "%s[%s]" % (fx_target_label(node[1]), index)
    return "value"


FX_PRECEDENCE = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6,
    "!=": 6,
    "<": 7,
    ">": 7,
    "<=": 7,
    ">=": 7,
    "<<": 8,
    ">>": 8,
    "+": 9,
    "-": 9,
    "*": 10,
    "/": 10,
    "%": 10,
}


# Adds parentheses only where precedence or associativity requires them.
def fx_operand_text(node, parent, right=False):
    text = fx_expr_text(node)
    if not isinstance(node, tuple) or not node:
        return text
    if node[0] in ("assign", "tern", "comma"):
        return "(%s)" % text
    if node[0] == "bin":
        mine = FX_PRECEDENCE.get(node[1], 11)
        outer = FX_PRECEDENCE.get(parent, 11)
        if mine < outer or (right and mine == outer):
            return "(%s)" % text
    return text


# Re-prints an expression as HLSL for the comments shown on generated nodes.
def fx_expr_text(node):
    if not isinstance(node, tuple) or not node:
        return "?"
    kind = node[0]
    if kind == "num":
        value = node[1]
        if isinstance(value, float) and value.is_integer() and abs(value) < 1e9:
            return str(int(value))
        return "%.7g" % value
    if kind == "str":
        return '"%s"' % node[1]
    if kind == "id":
        return node[1]
    if kind == "bin":
        return "%s %s %s" % (fx_operand_text(node[2], node[1]), node[1], fx_operand_text(node[3], node[1], True))
    if kind == "un":
        return "%s%s" % (node[1], fx_operand_text(node[2], "unary"))
    if kind == "pre":
        return "%s%s" % (node[1], fx_expr_text(node[2]))
    if kind == "post":
        return "%s%s" % (fx_expr_text(node[2]), node[1])
    if kind == "assign":
        return "%s %s %s" % (fx_expr_text(node[2]), node[1], fx_expr_text(node[3]))
    if kind == "tern":
        return "%s ? %s : %s" % (fx_expr_text(node[1]), fx_expr_text(node[2]), fx_expr_text(node[3]))
    if kind == "cast":
        return "(%s)%s" % (node[1] if isinstance(node[1], str) else "type", fx_expr_text(node[2]))
    if kind in ("ctor", "call"):
        name = node[1] if isinstance(node[1], str) else "type"
        return "%s(%s)" % (name, ", ".join(fx_expr_text(a) for a in node[2]))
    if kind == "member":
        return "%s.%s" % (fx_expr_text(node[1]), node[2])
    if kind == "index":
        return "%s[%s]" % (fx_expr_text(node[1]), fx_expr_text(node[2]))
    if kind == "mcall":
        return "%s.%s(%s)" % (fx_expr_text(node[1]), node[2], ", ".join(fx_expr_text(a) for a in node[3]))
    if kind == "comma":
        return ", ".join(fx_expr_text(a) for a in node[1])
    if kind == "init":
        return "{%s}" % ", ".join(fx_expr_text(a) for a in node[1])
    return kind


# One-line statement text, cut to the label limit; None for statements that get no label.
def fx_stmt_text(node):
    kind = node[0]
    if kind == "expr":
        text = fx_expr_text(node[1])
    elif kind == "decl":
        parts = []
        for name, _array, init in node[2]:
            parts.append(name if init is None else "%s = %s" % (name, fx_expr_text(init)))
        type_name = node[1] if isinstance(node[1], str) else getattr(node[1], "name", "struct")
        text = "%s %s" % (type_name, ", ".join(parts))
    elif kind == "return":
        text = "return" if node[1] is None else "return %s" % fx_expr_text(node[1])
    elif kind == "if":
        text = "if (%s)" % fx_expr_text(node[1])
    elif kind == "for":
        text = "for (%s)" % (fx_expr_text(node[2]) if node[2] is not None else "")
    elif kind == "do":
        text = "while (%s)" % fx_expr_text(node[2])
    else:
        return None
    return text if len(text) <= MME_LABEL_LIMIT else text[: MME_LABEL_LIMIT - 1] + "…"


MME_LABEL_LIMIT = 64
