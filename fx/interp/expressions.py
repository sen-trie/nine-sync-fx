import re

from ..constants import (
    FX_EVAL,
    FX_SWIZZLE,
)
from ..errors import (
    FxError,
    FxUnsupported,
)
from ..program import FxStruct
from ..text import fx_target_label
from ..types import (
    FxValue,
    fx_count,
    fx_is_array,
    fx_is_numeric,
    fx_resolve_type,
)


# Expression evaluation; eval() dispatches on the node kind through the FX_EVAL table of method names.
class FxExpressions:
    def eval(self, node):
        kind = node[0]
        method = FX_EVAL.get(kind)
        if method is None:
            raise FxError("cannot evaluate %s" % kind)
        return getattr(self, method)(node)

    def eval_num(self, node):
        return FxValue((node[2], ()), [self.B.const(node[1])])

    def eval_str(self, node):
        return FxValue("string", node[1])

    def eval_id(self, node):
        scope, name = self.lookup(node[1])
        if scope is not None:
            return scope[name]
        return self.global_value(name)

    def eval_comma(self, node):
        result = None
        for item in node[1]:
            result = self.eval(item)
        return result

    def eval_init_expr(self, node):
        items = self.flatten_init(node)
        comps = []
        for item in items:
            comps.extend(item.c)
        dims = () if len(comps) == 1 else (len(comps),)
        if len(comps) > 4:
            dims = (len(comps),)
        return FxValue(("f", dims), comps)

    def eval_un(self, node):
        op = node[1]
        value = self.eval(node[2])
        B = self.B
        if op == "+":
            return value
        if op == "-":
            return FxValue(value.t, [B.neg(x) for x in value.c])
        if op == "!":
            return FxValue(("b", value.t[1]), [B.lnot(x) for x in value.c])
        if op == "~":
            return FxValue(value.t, [B.bitnot(x) for x in value.c])
        raise FxError("unary %s" % op)

    def eval_incdec(self, node):
        kind, op, target = node
        old = self.eval(target)
        one = FxValue(("i", ()), [self.B.const(1.0)])
        new = self.binary("+" if op == "++" else "-", old, one)
        self.assign(target, self.convert(new, old.t))
        return new if kind == "pre" else old

    def eval_bin(self, node):
        return self.binary(node[1], self.eval(node[2]), self.eval(node[3]))

    # Applies HLSL promotion rules to a pair of operands and returns the result dims and matching component
    # lists.
    def broadcast(self, a, b):
        ta, tb = a.t, b.t
        if not (fx_is_numeric(ta) and fx_is_numeric(tb)):
            raise FxError("non-numeric operand")
        da, db = ta[1], tb[1]
        na, nb = len(a.c), len(b.c)
        if da == db:
            return da, a.c, b.c
        if na == 1:
            return db, a.c * nb, b.c
        if nb == 1:
            return da, a.c, b.c * na
        if len(da) == 1 and len(db) == 1:
            k = min(da[0], db[0])
            return (k,), a.c[:k], b.c[:k]
        if len(da) == 2 and len(db) == 2:
            r, c = min(da[0], db[0]), min(da[1], db[1])
            ca = [a.c[i * da[1] + j] for i in range(r) for j in range(c)]
            cb = [b.c[i * db[1] + j] for i in range(r) for j in range(c)]
            return (r, c), ca, cb
        if na == nb:
            return da, a.c, b.c
        k = min(na, nb)
        return (k,), a.c[:k], b.c[:k]

    def binary(self, op, a, b):
        B = self.B
        dims, ca, cb = self.broadcast(a, b)
        fa, fb = a.t[0], b.t[0]
        base = "f" if "f" in (fa, fb) else "i"
        if op in ("+", "-", "*", "/", "%"):
            if op == "+":
                comps = [B.add(x, y) for x, y in zip(ca, cb)]
            elif op == "-":
                comps = [B.sub(x, y) for x, y in zip(ca, cb)]
            elif op == "*":
                comps = [B.mul(x, y) for x, y in zip(ca, cb)]
            elif op == "/":
                if base == "i":
                    comps = [B.idiv(x, y) for x, y in zip(ca, cb)]
                else:
                    comps = [B.div(x, y) for x, y in zip(ca, cb)]
            elif base == "i":
                comps = [B.imod(x, y) for x, y in zip(ca, cb)]
            else:
                comps = [B.mod(x, y) for x, y in zip(ca, cb)]
            return FxValue((base, dims), comps)
        if op in ("<", "<=", ">", ">=", "==", "!="):
            return FxValue(("b", dims), [B.cmp(op, x, y) for x, y in zip(ca, cb)])
        if op == "&&":
            return FxValue(("b", dims), [B.land(x, y) for x, y in zip(ca, cb)])
        if op == "||":
            return FxValue(("b", dims), [B.lor(x, y) for x, y in zip(ca, cb)])
        if op in ("&", "|", "^", "<<", ">>"):
            result_base = "b" if fa == "b" and fb == "b" and op in ("&", "|", "^") else "i"
            return FxValue((result_base, dims), [B.bitop(op, x, y) for x, y in zip(ca, cb)])
        raise FxError("binary %s" % op)

    def eval_assign(self, node):
        _kind, op, target, value_expr = node
        value = self.eval(value_expr)
        if op != "=":
            current = self.eval(target)
            value = self.binary(op[:-1], current, value)
        self.assign(target, value)
        result = self.eval(target)
        self.tag(result, fx_target_label(target))
        return result

    def eval_tern(self, node):
        c = self.condition(node[1])
        known = self.B.known(c)
        if known is not None:
            return self.eval(node[2] if known else node[3])
        a = self.eval(node[2])
        b = self.eval(node[3])
        if fx_is_numeric(a.t) and fx_is_numeric(b.t):
            dims, ca, cb = self.broadcast(a, b)
            base = "f" if "f" in (a.t[0], b.t[0]) else a.t[0]
            return FxValue((base, dims), [self.B.select(c, x, y) for x, y in zip(ca, cb)])
        return self.merge(c, a, self.convert(b, a.t))

    def eval_cast(self, node):
        t = fx_resolve_type(node[1], self.program)
        value = self.eval(node[2])
        return self.convert(value, t)

    def eval_ctor(self, node):
        t = fx_resolve_type(node[1], self.program)
        args = [self.eval(a) for a in node[2]]
        if isinstance(t, FxStruct):
            if len(args) == 1:
                return self.convert(args[0], t)
            raise FxUnsupported("struct constructor")
        comps = []
        for arg in args:
            comps.extend(arg.c)
        n = fx_count(t[1])
        if len(comps) == 1 and n > 1:
            comps = comps * n
        comps = (comps + [self.B.const(0.0)] * n)[:n]
        source_base = "f" if any(a.t[0] == "f" for a in args) else "i"
        return self.convert(FxValue((source_base, t[1]), comps), t)

    # Component indices for .xyzw / .rgba selections.
    def swizzle_indices(self, t, name):
        if not fx_is_numeric(t):
            return None
        dims = t[1]
        if len(dims) == 2:
            found = re.findall(r"_m(\d)(\d)|_(\d)(\d)", name)
            if not found or "".join(
                ("_m%s%s" % (a, b)) if a else ("_%s%s" % (c, d)) for a, b, c, d in found
            ) != name:
                return None
            out = []
            for a, b, c, d in found:
                if a:
                    out.append(int(a) * dims[1] + int(b))
                else:
                    out.append((int(c) - 1) * dims[1] + (int(d) - 1))
            return out
        if len(name) > 4 or any(ch not in FX_SWIZZLE for ch in name):
            return None
        return [FX_SWIZZLE[ch] for ch in name]

    def eval_member(self, node):
        value = self.eval(node[1])
        name = node[2]
        if isinstance(value.t, FxStruct):
            if name not in value.c:
                raise FxError("struct %s has no member %s" % (value.t.name, name))
            return value.c[name]
        indices = self.swizzle_indices(value.t, name)
        if indices is None:
            raise FxError("bad member %s" % name)
        comps = value.c
        out = []
        for i in indices:
            out.append(comps[i] if i < len(comps) else comps[0] if len(comps) == 1 else self.B.const(0.0))
        dims = () if len(out) == 1 else (len(out),)
        return FxValue((value.t[0], dims), out)

    def eval_index(self, node):
        value = self.eval(node[1])
        index = self.eval(node[2])
        known = self.B.known(index.c[0])
        if fx_is_array(value.t):
            if known is not None:
                k = int(known)
                return value.c[max(0, min(k, len(value.c) - 1))]
            return self.dynamic_pick(value.c, index.c[0])
        if fx_is_numeric(value.t):
            dims = value.t[1]
            if len(dims) == 2:
                rows = [
                    FxValue((value.t[0], (dims[1],)), value.c[r * dims[1]:(r + 1) * dims[1]])
                    for r in range(dims[0])
                ]
                if known is not None:
                    return rows[max(0, min(int(known), dims[0] - 1))]
                return self.dynamic_pick(rows, index.c[0])
            elems = [FxValue((value.t[0], ()), [x]) for x in value.c]
            if known is not None:
                return elems[max(0, min(int(known), len(elems) - 1))]
            return self.dynamic_pick(elems, index.c[0])
        raise FxError("cannot index")

    # Indexing with a non-constant index builds a chain of selects over all elements.
    def dynamic_pick(self, items, index):
        B = self.B
        result = items[0]
        for k in range(1, len(items)):
            hit = B.cmp("==", B.unary("trunc", index), B.const(float(k)))
            result = self.merge(hit, items[k], result)
        return result

    def lvalue_path(self, node):
        path = []
        while True:
            kind = node[0]
            if kind == "id":
                return node[1], list(reversed(path))
            if kind == "member":
                path.append(("m", node[2]))
                node = node[1]
            elif kind == "index":
                path.append(("i", node[2]))
                node = node[1]
            else:
                raise FxError("expression is not assignable")

    def assign(self, target, value):
        name, path = self.lvalue_path(target)
        scope, _ = self.lookup(name)
        if scope is None:
            if name in self.overrides or name in self.program.globals:
                current = self.global_value(name)
                updated = self.write(current, path, value, self.active())
                self.cache[name] = updated
                return
            raise FxError("unknown variable %s" % name)
        scope[name] = self.write(scope[name], path, value, self.active())

    # Writes a value into a nested path (member, index, swizzle) of a variable, under the mask so inactive
    # lanes keep the old value.
    def write(self, current, path, value, mask):
        B = self.B
        if not path:
            new = self.convert(value, current.t) if current.t is not None else value
            known = B.known(mask)
            if known is not None:
                return new if known else current
            return self.merge(mask, new, current)
        kind, arg = path[0]
        if kind == "m":
            if isinstance(current.t, FxStruct):
                members = dict(current.c)
                members[arg] = self.write(members[arg], path[1:], value, mask)
                return FxValue(current.t, members)
            indices = self.swizzle_indices(current.t, arg)
            if indices is None:
                raise FxError("bad member %s" % arg)
            sub = FxValue((current.t[0], () if len(indices) == 1 else (len(indices),)), [current.c[i] for i in indices])
            sub = self.write(sub, path[1:], value, mask)
            comps = list(current.c)
            for j, i in enumerate(indices):
                comps[i] = sub.c[j]
            return FxValue(current.t, comps)
        index = self.eval(arg)
        known = B.known(index.c[0])
        if fx_is_array(current.t):
            elems = list(current.c)
            if known is not None:
                k = max(0, min(int(known), len(elems) - 1))
                elems[k] = self.write(elems[k], path[1:], value, mask)
            else:
                for k in range(len(elems)):
                    hit = B.cmp("==", B.unary("trunc", index.c[0]), B.const(float(k)))
                    sub_mask = hit if B.known(mask) is not None else B.mul(mask, hit)
                    elems[k] = self.write(elems[k], path[1:], value, sub_mask)
            return FxValue(current.t, elems)
        dims = current.t[1]
        comps = list(current.c)
        if len(dims) == 2:
            width = dims[1]
            groups = [list(range(r * width, (r + 1) * width)) for r in range(dims[0])]
            sub_type = (current.t[0], (width,))
        else:
            groups = [[i] for i in range(len(comps))]
            sub_type = (current.t[0], ())
        targets = range(len(groups)) if known is None else [max(0, min(int(known), len(groups) - 1))]
        for k in targets:
            sub_mask = mask
            if known is None:
                hit = B.cmp("==", B.unary("trunc", index.c[0]), B.const(float(k)))
                sub_mask = hit if B.known(mask) is not None else B.mul(mask, hit)
            sub = FxValue(sub_type, [comps[i] for i in groups[k]])
            sub = self.write(sub, path[1:], value, sub_mask)
            for j, i in enumerate(groups[k]):
                comps[i] = sub.c[j]
        return FxValue(current.t, comps)
