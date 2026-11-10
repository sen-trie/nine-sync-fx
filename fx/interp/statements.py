from ..constants import FX_LOOP_LIMIT
from ..errors import (
    FxError,
    FxUnsupported,
)
from ..program import FxStruct
from ..text import (
    FX_TEXT_STATEMENTS,
    fx_stmt_text,
)
from ..types import (
    FxValue,
    fx_array_type,
    fx_is_array,
    fx_is_numeric,
    fx_resolve_type,
)
from .frame import FxLoop


# Statement execution. Branches run with a mask instead of jumping.
class FxStatements:
    def exec_block(self, statements):
        for statement in statements:
            if self.inactive():
                return
            self.exec(statement)

    def exec(self, node):
        kind = node[0]
        if self.B.wants_text and kind in FX_TEXT_STATEMENTS:
            self.B.statement(fx_stmt_text(node))
        if kind == "expr":
            self.eval(node[1])
        elif kind == "decl":
            self.exec_decl(node)
        elif kind == "block":
            frame = self.frames[-1]
            frame.scopes.append({})
            try:
                self.exec_block(node[1])
            finally:
                frame.scopes.pop()
        elif kind == "if":
            self.exec_if(node)
        elif kind == "for":
            self.exec_for(node)
        elif kind == "do":
            self.exec_do(node)
        elif kind == "return":
            self.exec_return(node)
        elif kind == "break":
            loop = self.frames[-1].loops[-1]
            loop.brk = self.or_mask(loop.brk, self.active())
        elif kind == "continue":
            loop = self.frames[-1].loops[-1]
            loop.cont = self.or_mask(loop.cont, self.active())
        elif kind == "discard":
            self.discard = self.or_mask(self.discard, self.active())
        elif kind == "empty":
            pass
        else:
            raise FxError("unknown statement %s" % kind)

    # Union of two masks, folding constants so common cases stay constant.
    def or_mask(self, a, b):
        B = self.B
        ka, kb = B.known(a), B.known(b)
        if ka is not None and kb is not None:
            return 1.0 if (ka or kb) else 0.0
        if ka is not None:
            return 1.0 if ka else b
        if kb is not None:
            return 1.0 if kb else a
        return B.max(a, b)

    def exec_decl(self, node):
        _kind, type_name, declarators, _quals = node
        t = fx_resolve_type(type_name, self.program)
        scope = self.frames[-1].scopes[-1]
        for name, array, init in declarators:
            vt = t
            if array is not None:
                vt = fx_array_type(t, self.const_int(array))
            if init is None:
                scope[name] = self.zero(vt)
            elif isinstance(init, tuple) and init[0] == "init":
                scope[name] = self.eval_init(init, vt)
            else:
                scope[name] = self.convert(self.eval(init), vt)
            if init is not None:
                self.tag(scope[name], name)

    # Names the scalars of a value on the backend so generated nodes get readable labels.
    def tag(self, value, label):
        if fx_is_numeric(value.t):
            comps = value.c
            dims = value.t[1]
            for k, comp in enumerate(comps):
                if len(comps) == 1:
                    suffix = ""
                elif len(dims) == 2:
                    suffix = "[%d][%d]" % divmod(k, dims[1])
                else:
                    suffix = "." + "xyzw"[k] if k < 4 else "[%d]" % k
                self.B.tag(comp, label + suffix)
        elif isinstance(value.t, FxStruct):
            for name, member in value.c.items():
                self.tag(member, "%s.%s" % (label, name))

    def condition(self, expr):
        value = self.eval(expr)
        if not fx_is_numeric(value.t):
            raise FxError("condition is not numeric")
        return self.B.truth(value.c[0])

    # A constant condition runs one branch. Otherwise both branches run, each under the condition (or its
    # inverse) multiplied into the mask.
    def exec_if(self, node):
        _kind, cond_expr, then, other = node
        c = self.condition(cond_expr)
        known = self.B.known(c)
        if known is not None:
            if known:
                self.exec(then)
            elif other is not None:
                self.exec(other)
            return
        B = self.B
        saved = self.mask
        base = self.active()
        known_base = B.known(base)
        self.mask = c if known_base is not None else B.mul(base, c)
        try:
            self.exec(then)
            if other is not None:
                inv = B.sub(1.0, c)
                self.mask = inv if known_base is not None else B.mul(base, inv)
                self.exec(other)
        finally:
            self.mask = saved

    # Unrolls the loop. A constant condition ends it normally; a data-dependent one is only possible on the
    # per-vertex numpy backend (it becomes a break mask) and is unsupported on node backends.
    def exec_for(self, node):
        _kind, init, cond, step, body = node
        frame = self.frames[-1]
        frame.scopes.append({})
        loop = FxLoop()
        try:
            self.exec(init)
            frame.loops.append(loop)
            self.trace.append(-1)
            for iteration in range(FX_LOOP_LIMIT):
                self.trace[-1] = -2 - iteration
                if self.inactive():
                    break
                if cond is not None:
                    c = self.condition(cond)
                    known = self.B.known(c)
                    if known is not None:
                        if not known:
                            break
                    else:
                        if not self.B.vectorized:
                            raise FxUnsupported("data-dependent loop")
                        loop.brk = self.or_mask(loop.brk, self.B.sub(1.0, c))
                        if not self.B.any_true(self.active()):
                            break
                self.exec(body)
                loop.cont = 0.0
                if step is not None and not self.inactive():
                    self.eval(step)
            else:
                raise FxUnsupported("loop did not finish")
        finally:
            if frame.loops and frame.loops[-1] is loop:
                frame.loops.pop()
                self.trace.pop()
            frame.scopes.pop()

    def exec_do(self, node):
        _kind, body, cond = node
        frame = self.frames[-1]
        loop = FxLoop()
        frame.loops.append(loop)
        self.trace.append(-1)
        try:
            for iteration in range(FX_LOOP_LIMIT):
                self.trace[-1] = -2 - iteration
                if self.inactive():
                    break
                self.exec(body)
                loop.cont = 0.0
                if self.inactive():
                    break
                c = self.condition(cond)
                known = self.B.known(c)
                if known is not None:
                    if not known:
                        break
                else:
                    if not self.B.vectorized:
                        raise FxUnsupported("data-dependent loop")
                    loop.brk = self.or_mask(loop.brk, self.B.sub(1.0, c))
                    if not self.B.any_true(self.active()):
                        break
        finally:
            frame.loops.pop()
            self.trace.pop()

    def exec_return(self, node):
        frame = self.frames[-1]
        rt = fx_resolve_type(frame.function.ret, self.program)
        mask = self.active()
        if node[1] is not None and rt != "void":
            value = self.convert(self.eval(node[1]), rt)
            known = self.B.known(mask)
            if frame.ret is None or (known is not None and known):
                frame.ret = value
            else:
                frame.ret = self.merge(mask, value, frame.ret)
        frame.done = self.or_mask(frame.done, mask)

    # Blends a new value with the old one under the mask, componentwise through structs and arrays.
    def merge(self, mask, new, old):
        B = self.B
        if fx_is_numeric(new.t):
            return FxValue(new.t, [B.select(mask, a, b) for a, b in zip(new.c, old.c)])
        if isinstance(new.t, FxStruct):
            return FxValue(new.t, {k: self.merge(mask, new.c[k], old.c[k]) for k in new.c})
        if fx_is_array(new.t):
            return FxValue(new.t, [self.merge(mask, a, b) for a, b in zip(new.c, old.c)])
        return new
