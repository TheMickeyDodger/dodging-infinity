"""The non-invocation pin for the Task 7 read path, shared by
tests/test_static.py, tests/test_mission_observation.py and
tests/test_mission_reconciliation.py.

The claim is deliberately narrow and stated exactly:

1. RAW SURFACE. A function that receives a raw caller value (``observe``
   and ``reconcile`` in the service; ``normalize_inputs`` in the
   observation module) may use each raw parameter, before its first
   sanitizer call, for exactly two things: an ``is None`` / ``is not
   None`` test, or a rebinding to a literal inside such a test. Its FIRST
   other use must be as the first argument of a SANITIZER call in a
   top-level statement of the function. After ``normalize_inputs`` (which
   returns new sanitized bindings) has consumed a raw name it is never
   read again; after ``require_plain_data`` / ``require_exact_*`` (which
   prove the value in place) the name is exact plain data.

2. SANITIZERS. ``require_plain_data``, ``require_exact_str``,
   ``require_exact_int`` and ``require_exact_context`` are the only code
   that reads a raw value. Their bodies are checked at the EXPRESSION
   level: a raw name (the value being sanitized, the names an exact dict
   yields, and every local derived from them other than through
   ``type()`` or a sanitizer) may appear ONLY as: the left operand of
   ``is`` / ``is not``; the sole argument of ``type()``; the argument of
   ``len()`` once established as an exact str; the argument of
   ``dict.items()`` once established as an exact dict; the receiver of
   ``.bit_length()`` once established as an exact int; the first
   argument of a sanitizer; the left operand of ``== "<literal>"`` once
   established as an exact bounded str; the whole value of a bare
   assignment (aliasing, no dispatch); ``x.__dict__`` on a name
   established as the exact ``AuthenticatedContext`` class, only as the
   whole value of an assignment (the class-level data descriptor,
   resolved on the type by ``object.__getattribute__``, never through an
   instance-dict lookup); inside refusal / path text or the context
   constructor once established as an exact str AND bounded; or the
   operand of ``return`` once FULLY established — None, a bounded str, a
   bounded int, or a dict whose keys and items a loop checked. Any other
   position — a truth test, another comparison, formatting, iteration,
   subscript, attribute, method, other builtin, or returning anything
   less than fully established — fails, so a sanitizer cannot hand
   rejected or unchecked input back to its caller.

   A guard establishes anything ONLY when its body provably refuses: a
   single ``_input(...)`` / ``record.fail(...)`` call or a ``raise``.
   ``return`` is NOT a refusal (it hands the value back) and ``pass``
   proves nothing. ``_input`` itself is checked to be one call to
   ``record.fail``, the baseline's raise (Task 4 pins that it raises).
   The guards are ``if type(x) is not T: <refuse>`` (establishing ``x``
   as ``T`` afterwards), ``if kind is T:`` / ``if type(x) is T:``
   (inside the body, ``kind = type(x)``), ``if len(x) > BOUND:
   <refuse>`` (bounding an exact str afterwards), ``if x.bit_length() >
   BITS: <refuse>`` (bounding an exact int afterwards), ``if x is
   None:`` (None inside the body), and a ``for key, item in
   dict.items(x)`` loop whose body refuses a non-str key and passes
   every item into a sanitizer (x is a checked dict afterwards).

3. EVERYWHERE ELSE on the read path (the rest of the two modules and the
   service read functions, nested closures included): no call's callee
   is rooted at a name TAINTED by a parameter (the parameters and,
   transitively, every local bound from an expression mentioning a
   tainted name), so no method is ever invoked ON a value derived from
   an argument — container operations on such values go through explicit
   ``dict.get(x, ...)`` / ``list.append(x, ...)`` base-class forms whose
   receiver is the exact builtin class; no callee Name is lexically bound
   in the function (shadowing); no attribute access spells a dunder name;
   and ``callable``, ``getattr``, ``setattr``, ``eval``, ``exec``,
   ``compile``, ``__import__`` and ``open`` are never called.

Stated limit, on purpose: outside the raw surface the data is
sanitized, so builtins that iterate or compare (``sorted``, ``len``,
``set``, ``dict``, ``isinstance``) are permitted on it; the pin does not
claim those are dispatch-free on arbitrary objects, only that no
arbitrary object reaches them, because the raw surface admits exact
builtins only and the sanitizers are checked by shape.
"""

import ast

SANITIZERS = frozenset({
    "require_plain_data", "require_exact_str", "require_exact_int",
    "require_exact_context", "normalize_inputs",
})
TIER1 = frozenset({
    "require_plain_data", "require_exact_str", "require_exact_int",
    "require_exact_context",
})
TIER1_HELPERS = frozenset({"_input", "_at"})
# A sanitizer that returns NEW sanitized bindings: the raw name it consumed
# must never be read again. The others prove the value in place.
CONSUMING_SANITIZERS = frozenset({"normalize_inputs"})
FORBIDDEN_CALLEES = frozenset({
    "callable", "getattr", "setattr", "delattr", "eval", "exec", "compile",
    "__import__", "import_module", "vars", "globals", "locals", "open",
})


class Violation(Exception):
    pass


def _root(node):
    """The root of a callee / receiver expression: a Name's id, the
    string ``"<literal>"`` for a constant receiver, or None for a
    computed expression (a call result, a subscript of a call, ...)."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return "<literal>"
    return None


def _names(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _stores(node):
    return {n.id for n in ast.walk(node)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}


def _bindings(function):
    """(targets_node, value_expr) for every binding statement in the
    function, nested functions included."""
    pairs = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                pairs.append((target, node.value))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign, ast.NamedExpr)):
            if node.value is not None:
                pairs.append((node.target, node.value))
        elif isinstance(node, (ast.For, ast.comprehension)):
            pairs.append((node.target, node.iter))
        elif isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars is not None:
                    pairs.append((item.optional_vars, item.context_expr))
    return pairs


def bound_names(function):
    names = {a.arg for a in function.args.args + function.args.kwonlyargs}
    if function.args.vararg:
        names.add(function.args.vararg.arg)
    if function.args.kwarg:
        names.add(function.args.kwarg.arg)
    for target, _ in _bindings(function):
        names |= _stores(target)
    for node in ast.walk(function):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node is not function:
            names.add(node.name)
    return names


def tainted_names(function):
    """Parameters (except self) and, transitively, every local bound
    from an expression mentioning a tainted name."""
    tainted = {a.arg for a in function.args.args + function.args.kwonlyargs}
    tainted.discard("self")
    pairs = _bindings(function)
    changed = True
    while changed:
        changed = False
        for target, value in pairs:
            if _names(value) & tainted:
                new = _stores(target) - tainted
                if new:
                    tainted |= new
                    changed = True
    return tainted


def _dunder_accesses(node):
    return [(n.lineno, n.attr) for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr.startswith("__")]


def _callee_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _check_common(function, known, where):
    bound = bound_names(function)
    tainted = tainted_names(function)
    for lineno, attr in _dunder_accesses(function):
        raise Violation("%s:%d reads dunder attribute %s" % (where, lineno, attr))
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = _callee_name(func)
        if name in FORBIDDEN_CALLEES:
            raise Violation("%s:%d calls %s" % (where, node.lineno, name))
        if isinstance(func, ast.Name):
            if func.id in bound:
                raise Violation("%s:%d calls %s, a name bound in the function (a"
                                " parameter, a local or a shadowed name)"
                                % (where, node.lineno, func.id))
            continue
        root = _root(func)
        if root is None:
            raise Violation("%s:%d calls a method on a computed expression"
                            % (where, node.lineno))
        if root in tainted:
            raise Violation("%s:%d calls %s on %s, a value derived from an argument"
                            % (where, node.lineno, name, root))
    return tainted


def _guard(test, aliases):
    """(raw_name, type_token, negated) for ``type(x) is T`` / ``type(x) is
    not T`` / ``kind is T`` / ``kind is not T`` with ``kind = type(x)``,
    where T is a builtin Name or ``record.AuthenticatedContext``; else
    None."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return None
    if not isinstance(test.ops[0], (ast.Is, ast.IsNot)):
        return None
    left, right = test.left, test.comparators[0]
    if isinstance(right, ast.Name):
        token = right.id
    elif isinstance(right, ast.Attribute) and isinstance(right.value, ast.Name) and (
        right.value.id == "record" and right.attr == "AuthenticatedContext"
    ):
        token = "AuthenticatedContext"
    else:
        return None
    negated = isinstance(test.ops[0], ast.IsNot)
    if isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and (
        left.func.id == "type" and len(left.args) == 1
        and isinstance(left.args[0], ast.Name)
    ):
        return left.args[0].id, token, negated
    if isinstance(left, ast.Name) and left.id in aliases:
        return aliases[left.id], token, negated
    return None


def _refuses(body):
    """A guard body PROVES refusal only when it is exactly one statement
    that cannot return to the caller: a call to ``_input`` /
    ``record.fail`` (each verified to raise, see ``check_refusal_helper``)
    or a ``raise``. A ``return`` is NOT a refusal — it hands the value
    back — and ``pass``, or anything else, proves nothing."""
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Raise):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        name = _callee_name(stmt.value.func)
        return name in TIER1_HELPERS and name == "_input" or (
            isinstance(stmt.value.func, ast.Attribute)
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "record" and name == "fail")
    return False


CONTEXT_FIELDS = ("transport", "principal_kind", "principal_ref", "configured_subject")


def _check_tier1(function, where):
    """The sanitizer bodies, shape by shape, at the EXPRESSION level: a
    raw name may appear only in an explicitly allowed position (see the
    module docstring); a guard establishes a type or a bound only when
    its body provably refuses; nothing else touches a raw value."""
    aliases = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(
            node.targets[0], ast.Name
        ) and isinstance(node.value, ast.Call) and isinstance(
            node.value.func, ast.Name
        ) and node.value.func.id == "type" and len(node.value.args) == 1 and (
            isinstance(node.value.args[0], ast.Name)
        ):
            aliases[node.targets[0].id] = node.value.args[0].id
    # Raw names: the value being sanitized, the names an exact dict
    # yields, and every local bound from an expression mentioning a raw
    # name other than through ``type()`` or a sanitizer call.
    raw = {function.args.args[0].arg, "key", "item"}
    changed = True
    while changed:
        changed = False
        for target, value in _bindings(function):
            if isinstance(value, ast.Call) and _callee_name(value.func) in (
                SANITIZERS | {"type"}
            ):
                continue
            if isinstance(value, ast.IfExp) and isinstance(value.orelse, ast.Call) and (
                _callee_name(value.orelse.func) in SANITIZERS
            ) and isinstance(value.body, ast.Constant) and value.body.value is None:
                continue
            if _names(value) & raw:
                new = _stores(target) - raw
                if new:
                    raw |= new
                    changed = True
    # Parent map for expression-position checks.
    parents = {}
    for node in ast.walk(function):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def parent(node):
        return parents.get(node)

    # Walk statements tracking what each raw name is established as.
    uses = []  # (Name node, established snapshot)
    calls = []  # (Call node, established snapshot)

    def collect(node, established):
        for inner in ast.walk(node):
            if isinstance(inner, ast.Call):
                calls.append((inner, dict(established)))
            elif isinstance(inner, ast.Name) and isinstance(inner.ctx, ast.Load) and (
                inner.id in raw
            ):
                uses.append((inner, dict(established)))

    def block(stmts, established):
        established = dict(established)
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                collect(stmt.test, established)
                guard = _guard(stmt.test, aliases)
                if guard is not None:
                    name, token, negated = guard
                    if negated:
                        block(stmt.body, established)
                        block(stmt.orelse, established)
                        if not stmt.orelse and _refuses(stmt.body):
                            established[name] = token
                    else:
                        inner = dict(established)
                        inner[name] = token
                        block(stmt.body, inner)
                        block(stmt.orelse, established)
                    continue
                none_of = _is_none_guard(stmt.test)
                if none_of is not None:
                    name, negated = none_of
                    inner = dict(established)
                    if not negated:
                        inner[name] = "none"
                    block(stmt.body, inner)
                    block(stmt.orelse, established)
                    continue
                bounded = _length_guard(stmt.test)
                bits = _bits_guard(stmt.test)
                block(stmt.body, established)
                block(stmt.orelse, established)
                if bounded is not None and not stmt.orelse and _refuses(stmt.body) and (
                    established.get(bounded) == "str"
                ):
                    established[bounded] = "str_bounded"
                if bits is not None and not stmt.orelse and _refuses(stmt.body) and (
                    established.get(bits) == "int"
                ):
                    established[bits] = "int_bounded"
                continue
            sanitized = _sanitizer_assignment(stmt)
            if sanitized is not None:
                collect(stmt, established)
                target, token = sanitized
                established[target] = token
                continue
            if isinstance(stmt, ast.For):
                collect(stmt.iter, established)
                inner = dict(established)
                block(stmt.body, inner)
                walked = _dict_items_of(stmt.iter)
                if walked is not None and established.get(walked) == "dict" and (
                    _loop_checks_keys_and_items(stmt)
                ):
                    established[walked] = "dict_checked"
                continue
            collect(stmt, established)

    block(function.body, {})

    def allowed_use(name_node, established):
        """The allowed positions of a raw name."""
        name = name_node.id
        p = parent(name_node)
        if isinstance(p, ast.Compare) and p.left is name_node and len(p.ops) == 1 and (
            isinstance(p.ops[0], (ast.Is, ast.IsNot))
        ):
            return True
        if isinstance(p, ast.Compare) and p.left is name_node and len(p.ops) == 1 and (
            isinstance(p.ops[0], ast.Eq) and isinstance(p.comparators[0], ast.Constant)
            and type(p.comparators[0].value) is str
            and established.get(name) == "str_bounded"
        ):
            return True
        if isinstance(p, ast.Return):
            return established.get(name) in ("none", "str_bounded", "int_bounded",
                                             "dict_checked")
        if isinstance(p, ast.Assign) and p.value is name_node:
            return True
        if isinstance(p, ast.Call):
            callee = _callee_name(p.func)
            if callee == "type" and len(p.args) == 1:
                return True
            if callee == "len" and established.get(name) in ("str", "str_bounded"):
                return True
            if callee == "type" and len(p.args) == 1:
                return True
            if callee in SANITIZERS and p.args and p.args[0] is name_node:
                return True
            if callee in TIER1_HELPERS or (
                isinstance(p.func, ast.Attribute) and _root(p.func) == "record"
                and callee == "fail"
            ):
                return established.get(name) == "str_bounded"
            if isinstance(p.func, ast.Attribute) and _root(p.func) == "dict" and (
                callee == "items" and established.get(name) == "dict"
            ):
                return True
            if isinstance(p.func, ast.Attribute) and _root(p.func) == "record" and (
                callee == "AuthenticatedContext"
            ):
                return established.get(name) == "str_bounded"
            return False
        if isinstance(p, ast.keyword):
            call = parent(p)
            return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and _root(call.func) == "record"
                    and _callee_name(call.func) == "AuthenticatedContext"
                    and established.get(name) == "str_bounded")
        if isinstance(p, ast.Attribute) and p.value is name_node:
            if p.attr == "bit_length" and established.get(name) in ("int", "int_bounded"):
                return isinstance(parent(p), ast.Call)
            if p.attr == "__dict__" and established.get(name) == "AuthenticatedContext":
                grand = parent(p)
                return isinstance(grand, ast.Assign) and grand.value is p
            # Any other attribute read on a raw name (a context field, for
            # instance) is an instance-dict lookup that hashes and compares
            # caller-planted keys: never allowed.
            return False
        if isinstance(p, (ast.Tuple, ast.BinOp)) and established.get(name) == "str_bounded":
            # ``"%s" % (location, key)`` / ``location + key`` for a bounded str
            grand = parent(p) if isinstance(p, ast.Tuple) else p
            if isinstance(grand, ast.BinOp) and isinstance(grand.op, (ast.Mod, ast.Add)):
                return True
        return False

    for name_node, established in uses:
        if not allowed_use(name_node, established):
            raise Violation("%s:%d raw name %s used in a position that could dispatch"
                            " (truth test, comparison, formatting, iteration, method,"
                            " attribute) before its type and bound are established"
                            % (where, name_node.lineno, name_node.id))
    for call, established in calls:
        func = call.func
        callee = _callee_name(func)
        if isinstance(func, ast.Name):
            if callee in ("type", "len") or callee in TIER1 or callee in TIER1_HELPERS:
                continue
            raise Violation("%s:%d calls %s inside a sanitizer" % (where, call.lineno,
                                                                    callee))
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            base = func.value.id
            if (base, callee) in (("dict", "items"), ("record", "fail"),
                                  ("record", "AuthenticatedContext")):
                continue
            if callee == "bit_length" and not call.args:
                continue
            raise Violation("%s:%d method call %s.%s inside a sanitizer"
                            % (where, call.lineno, base, callee))
        raise Violation("%s:%d computed callee inside a sanitizer" % (where, call.lineno))
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            if node.attr == "__dict__" and isinstance(node.value, ast.Name) and any(
                use is node.value
                and established.get(node.value.id) == "AuthenticatedContext"
                for use, established in uses
            ):
                continue
            raise Violation("%s:%d reads dunder attribute %s"
                            % (where, node.lineno, node.attr))


_SANITIZER_TOKENS = {
    "require_exact_str": "str_bounded",
    "require_exact_int": "int_bounded",
    "require_plain_data": "plain",
    "require_exact_context": "context",
}


def _sanitizer_assignment(stmt):
    """``x = <sanitizer>(y, ...)`` or ``x = None if y is None else
    <sanitizer>(y, ...)`` with a single Name target: (x, token), the
    token being what the sanitizer establishes; else None."""
    if not (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)):
        return None
    value = stmt.value
    if isinstance(value, ast.IfExp) and isinstance(value.body, ast.Constant) and (
        value.body.value is None and _is_none_guard(value.test) is not None
    ):
        value = value.orelse
    if isinstance(value, ast.Call) and _callee_name(value.func) in _SANITIZER_TOKENS:
        return stmt.targets[0].id, _SANITIZER_TOKENS[_callee_name(value.func)]
    return None


def _is_none_guard(test):
    """(name, negated) for ``x is None`` / ``x is not None``; else None."""
    if (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and len(test.ops) == 1 and isinstance(test.ops[0], (ast.Is, ast.IsNot))
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None):
        return test.left.id, isinstance(test.ops[0], ast.IsNot)
    return None


def _bits_guard(test):
    """The name x of an ``x.bit_length() > <bound>`` test, else None."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Gt)):
        return None
    left = test.left
    if isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and (
        left.func.attr == "bit_length" and isinstance(left.func.value, ast.Name)
        and not left.args
    ):
        return left.func.value.id
    return None


def _dict_items_of(expr):
    """The name x of a ``dict.items(x)`` expression, else None."""
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and (
        isinstance(expr.func.value, ast.Name) and expr.func.value.id == "dict"
        and expr.func.attr == "items" and len(expr.args) == 1
        and isinstance(expr.args[0], ast.Name)
    ):
        return expr.args[0].id
    return None


def _loop_checks_keys_and_items(loop):
    """``for key, item in dict.items(x)`` whose body carries an exact-str
    refusal guard on ``key`` and passes ``item`` first into a sanitizer."""
    if not (isinstance(loop.target, ast.Tuple) and len(loop.target.elts) == 2
            and all(isinstance(e, ast.Name) for e in loop.target.elts)):
        return False
    key, item = (e.id for e in loop.target.elts)
    key_guard = any(
        isinstance(stmt, ast.If) and _guard(stmt.test, {}) == (key, "str", True)
        and not stmt.orelse and _refuses(stmt.body)
        for stmt in loop.body)
    item_sanitized = any(
        isinstance(n, ast.Call) and _callee_name(n.func) in SANITIZERS
        and n.args and isinstance(n.args[0], ast.Name) and n.args[0].id == item
        for n in ast.walk(loop))
    return key_guard and item_sanitized


def check_refusal_helper(tree, where):
    """``_input`` must be a function whose whole body is one call to
    ``record.fail(...)``; ``record.fail`` is the baseline's single raise
    (Task 4 pins it). A refusal in a sanitizer is therefore a raise."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_input":
            body = node.body
            if len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(
                body[0].value, ast.Call
            ) and isinstance(body[0].value.func, ast.Attribute) and (
                isinstance(body[0].value.func.value, ast.Name)
                and body[0].value.func.value.id == "record"
                and body[0].value.func.attr == "fail"
            ):
                return True
            raise Violation("%s: _input does not raise through record.fail" % where)
    raise Violation("%s: no _input refusal helper" % where)


def _length_guard(test):
    """The name x of a ``len(x) > <bound>`` test, else None."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Gt)):
        return None
    left = test.left
    if isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and (
        left.func.id == "len" and len(left.args) == 1
        and isinstance(left.args[0], ast.Name)
    ):
        return left.args[0].id
    return None


def _is_none_test(test, name):
    return (isinstance(test, ast.Compare) and isinstance(test.left, ast.Name)
            and test.left.id == name and len(test.ops) == 1
            and isinstance(test.ops[0], (ast.Is, ast.IsNot))
            and isinstance(test.comparators[0], ast.Constant)
            and test.comparators[0].value is None)


def _mentions(node, name):
    return any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Load)
               for n in ast.walk(node))


def _check_raw_surface(function, where):
    params = [a.arg for a in function.args.args if a.arg != "self"]
    for param in params:
        sanitized_at = None
        consumed = None
        for index, stmt in enumerate(function.body):
            if not _mentions(stmt, param):
                if sanitized_at is None or not isinstance(stmt, ast.If):
                    continue
            if sanitized_at is not None:
                if consumed and _mentions(stmt, param):
                    raise Violation("%s: raw %s read after %s consumed it at line %d"
                                    % (where, param, consumed, stmt.lineno))
                continue
            if isinstance(stmt, ast.If) and _is_none_test(stmt.test, param):
                for inner in stmt.body:
                    if not (isinstance(inner, ast.Assign) and _stores(inner) == {param}
                            and not _names(inner.value)):
                        raise Violation("%s: %s used before sanitization at line %d"
                                        % (where, param, inner.lineno))
                continue
            call = stmt.value if isinstance(stmt, (ast.Assign, ast.Expr)) else None
            if isinstance(call, ast.Call) and _callee_name(call.func) in SANITIZERS and (
                call.args and isinstance(call.args[0], ast.Name)
                and call.args[0].id == param
                and not any(_mentions(a, param) for a in call.args[1:])
            ):
                sanitized_at = index
                if _callee_name(call.func) in CONSUMING_SANITIZERS:
                    consumed = _callee_name(call.func)
                continue
            raise Violation("%s: raw parameter %s used before sanitization at line %d"
                            % (where, param, stmt.lineno))
        if sanitized_at is None:
            raise Violation("%s: raw parameter %s is never sanitized" % (where, param))


def module_known(tree):
    known = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            known.add(node.name)
        elif isinstance(node, ast.Import):
            known.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            known.update(a.asname or a.name for a in node.names)
        elif isinstance(node, ast.Assign):
            known |= _stores(node)
    return known


def check_function(function, known, where, raw_surface=False):
    if function.name in TIER1:
        _check_tier1(function, where)
        return
    _check_common(function, known, where)
    if raw_surface:
        _check_raw_surface(function, where)


def check_module(tree, where, raw_surface=()):
    known = module_known(tree)
    if TIER1 & known:
        check_refusal_helper(tree, where)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            count += 1
            check_function(node, known, "%s:%s" % (where, node.name),
                           raw_surface=node.name in raw_surface)
    return count


def check_functions(tree, names, where, raw_surface=()):
    known = module_known(tree)
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            seen.add(node.name)
            check_function(node, known, "%s:%s" % (where, node.name),
                           raw_surface=node.name in raw_surface)
            for inner in ast.walk(node):
                if isinstance(inner, ast.FunctionDef) and inner is not node:
                    check_function(inner, known, "%s:%s.%s" % (where, node.name,
                                                             inner.name))
    missing = set(names) - seen
    if missing:
        raise Violation("%s: functions not found: %s" % (where, sorted(missing)))
    return len(seen)


# Hostile probes: every one must FAIL the pin.
PROBES = (
    ("def f(adapter):\n    return adapter(1)\n", ()),
    ("def f(x):\n    return x['k'](1)\n", ()),
    ("def f(inputs):\n    return inputs.get('x')\n", ("f",)),
    ("def f(inputs):\n    return inputs['x'].get('y')\n", ("f",)),
    ("def f(inputs):\n    return list(inputs)\n", ("f",)),
    ("def f(inputs):\n    return type(inputs).__name__\n", ("f",)),
    ("def normalize_inputs(x):\n    return x\ndef f(normalize_inputs):\n"
     "    return normalize_inputs(1)\n", ()),
    ("def f(inputs):\n    y = inputs\n    return y.get('x')\n", ()),
    ("def f(inputs):\n    for k in inputs:\n        k.strip()\n", ()),
    ("def f(inputs):\n    return getattr(inputs, 'x')\n", ()),
    ("def f(inputs):\n    isinstance(inputs, dict)\n    return 1\n", ("f",)),
    ("def f(inputs):\n    z = str(inputs)\n    return z\n", ("f",)),
    ("def f(inputs):\n    if inputs is None:\n        inputs = {}\n"
     "    require_plain_data(inputs, 'i')\n    return inputs['x'].get('y')\n", ()),
    ("def f(inputs):\n    a, b = normalize_inputs(inputs)\n    return inputs\n", ("f",)),
    ("def require_exact_int(value, location):\n    if len(value) > 3:\n"
     "        _input(location)\n    return value\n", ()),
    ("def require_plain_data(value, location):\n    _input(str(value))\n", ()),
    ("def require_plain_data(value, location):\n    for k, v in dict.items(value):\n"
     "        pass\n", ()),
    ("def require_plain_data(value, location):\n    kind = type(value)\n"
     "    if kind is str:\n        return value\n    return value.bit_length()\n", ()),
    ("def require_plain_data(value, location):\n    kind = type(value)\n"
     "    _input(kind.__name__)\n", ()),
    ("def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        _input(location)\n    _input('%s' % value)\n", ()),
    # Round-4 finding 3: the three shapes the pin accepted.
    ("def require_plain_data(value, location):\n    if value:\n        pass\n"
     "    kind = type(value)\n    return value\n", ()),
    ("def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        pass\n    if len(value) > m:\n        _input(location)\n"
     "    return value\n", ()),
    ("def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        _input(location)\n    if len(value) > m:\n        pass\n"
     "    _input('%s' % value)\n", ()),
    ("def require_plain_data(value, location):\n    kind = type(value)\n"
     "    if kind is not dict:\n        pass\n"
     "    for key, item in dict.items(value):\n        pass\n", ()),
    ("def require_plain_data(value, location):\n    if value == 3:\n"
     "        _input(location)\n    return value\n", ()),
    ("def require_plain_data(value, location):\n    for x in value:\n"
     "        pass\n", ()),
    ("def require_plain_data(value, location):\n    return value[0]\n", ()),
    ("def require_plain_data(value, location):\n    _input('%s' % (location, value))\n",
     ()),
    ("def require_exact_context(value):\n"
     "    if type(value) is not record.AuthenticatedContext:\n        _input('c')\n"
     "    return record.AuthenticatedContext(transport=value.transport,\n"
     "        principal_kind='k', principal_ref='r')\n", ()),
    # Round-6 blocker 3: a sanitizer that RETURNS rejected or raw input.
    # The reviewer's exact substitution: the unsupported-type rejection of
    # the real require_plain_data replaced by ``return value``.
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_plain_data(value, location):\n    if value is None:\n"
     "        return value\n    kind = type(value)\n"
     "    if kind is str:\n        if len(value) > 3:\n            _input(location)\n"
     "        return value\n    if kind is not dict:\n        return value\n"
     "    for key, item in dict.items(value):\n        if type(key) is not str:\n"
     "            _input(location)\n        require_plain_data(item, location)\n"
     "    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        return value\n    if len(value) > m:\n        _input(location)\n"
     "    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        _input(location)\n    if len(value) > m:\n        return value\n"
     "    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_int(value, location):\n    if type(value) is not int:\n"
     "        _input(location)\n    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_plain_data(value, location):\n    kind = type(value)\n"
     "    if kind is not dict:\n        _input(location)\n    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_plain_data(value, location):\n    kind = type(value)\n"
     "    if kind is not dict:\n        _input(location)\n"
     "    for key, item in dict.items(value):\n        if type(key) is not str:\n"
     "            _input(location)\n    return value\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_plain_data(value, location):\n    return value\n", ()),
    # A refusal helper that does not raise.
    ("def _input(d):\n    return None\ndef require_exact_int(value, location):\n"
     "    if type(value) is not int:\n        _input(location)\n"
     "    if value.bit_length() > 63:\n        _input(location)\n    return value\n",
     ()),
    # Round-6 blocker 2: attribute lookup on the exact context.
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_context(value):\n"
     "    if type(value) is not record.AuthenticatedContext:\n        _input('c')\n"
     "    transport = require_exact_str(value.transport, 't', 8)\n"
     "    return record.AuthenticatedContext(transport=transport, principal_kind=transport,"
     " principal_ref=transport)\n"
     "def require_exact_str(value, location, m):\n    if type(value) is not str:\n"
     "        _input(location)\n    if len(value) > m:\n        _input(location)\n"
     "    return value\n", ()),
    # __dict__ read used anywhere but as the whole value of an assignment,
    # or on a name not established as the exact context.
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_context(value):\n"
     "    if type(value) is not record.AuthenticatedContext:\n        _input('c')\n"
     "    for key, item in dict.items(value.__dict__):\n        pass\n"
     "    return record.AuthenticatedContext(transport='t', principal_kind='k',"
     " principal_ref='r')\n", ()),
    ("import record\ndef _input(d):\n    record.fail('x', d)\n"
     "def require_exact_context(value):\n    instance = value.__dict__\n"
     "    return record.AuthenticatedContext(transport='t', principal_kind='k',"
     " principal_ref='r')\n", ()),
)
# Benign shapes: every one must PASS.
PASSING_PROBES = (
    ("import copy\ndef f(x):\n    return copy.deepcopy(x)\n", ()),
    ("import record\ndef require_exact_context(value):\n"
     "    if type(value) is not record.AuthenticatedContext:\n        _input('c')\n"
     "    instance = value.__dict__\n"
     "    if type(instance) is not dict:\n        _input('c')\n"
     "    transport = None\n    subject = None\n    seen = 0\n"
     "    for key, item in dict.items(instance):\n"
     "        if type(key) is not str:\n            _input('c')\n"
     "        if len(key) > 8:\n            _input('c')\n"
     "        if key == 'transport':\n            transport = item\n"
     "        elif key == 'configured_subject':\n            subject = item\n"
     "        else:\n            _input('c')\n        seen = seen + 1\n"
     "    if seen != 2:\n        _input('c')\n"
     "    transport = require_exact_str(transport, 'context.transport', 8)\n"
     "    subject = (None if subject is None\n"
     "               else require_exact_str(subject, 's', 8))\n"
     "    return record.AuthenticatedContext(transport=transport,\n"
     "        principal_kind=transport, principal_ref=transport,\n"
     "        configured_subject=subject)\n"
     "def require_exact_str(value, location, max_chars):\n"
     "    if type(value) is not str:\n        _input(location)\n"
     "    if len(value) > max_chars:\n        _input(location)\n    return value\n"
     "def require_exact_int(value, location):\n    if type(value) is not int:\n"
     "        _input(location)\n    if value.bit_length() > 63:\n"
     "        _input(location)\n    return value\n"
     "def _input(d):\n    record.fail('x', d)\n", ()),
    ("def f(x):\n    y = []\n    y.append(x)\n    return ', '.join(y)\n", ()),
    ("def f(x):\n    return dict.get(x, 'k')\n", ()),
    ("def g(x):\n    return x\ndef f(inputs):\n    if inputs is None:\n"
     "        inputs = {}\n    a, b = normalize_inputs(inputs)\n    return a\n"
     "def normalize_inputs(value):\n    require_plain_data(value, 'i')\n"
     "    return value, value\n"
     "def require_plain_data(value, location):\n"
     "    if value is None:\n        return value\n    kind = type(value)\n"
     "    if kind is str:\n        if len(value) > 3:\n            _input(location)\n"
     "        return value\n    if kind is int:\n"
     "        if value.bit_length() > 63:\n            _input(location)\n"
     "        return value\n    if kind is not dict:\n        _input(location)\n"
     "    for key, item in dict.items(value):\n        if type(key) is not str:\n"
     "            _input(location)\n        if len(key) > 3:\n            _input(location)\n"
     "        require_plain_data(item, _at(location, key))\n"
     "    return value\ndef _input(d):\n    record.fail('x', d)\ndef _at(a, b):\n"
     "    return a\nimport record\n",
     ("f", "normalize_inputs")),
)


def self_check():
    """The detector fires on every hostile probe and passes every benign
    one; returns the number of probes checked."""
    for source, raw in PROBES:
        try:
            check_module(ast.parse(source), "probe", raw_surface=raw)
        except Violation:
            continue
        raise AssertionError("non-invocation detector did not fire on: %r" % source)
    for source, raw in PASSING_PROBES:
        check_module(ast.parse(source), "benign", raw_surface=raw)
    return len(PROBES) + len(PASSING_PROBES)
