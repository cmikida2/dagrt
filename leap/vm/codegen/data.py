"""Mini-type inference for leap methods"""

from __future__ import division, with_statement

__copyright__ = """
Copyright (C) 2013 Andreas Kloeckner
Copyright (C) 2014 Matt Wala
"""

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import leap.vm.language as lang
from leap.vm.utils import is_state_variable
from pytools import RecordWithoutPickling
from pymbolic.mapper import Mapper


# {{{ symbol information

class SymbolKind(RecordWithoutPickling):
    def __eq__(self, other):
        return (
                type(self) == type(other)
                and self.__getinitargs__() == other.__getinitargs__())

    def __ne__(self, other):
        return not self.__eq__(other)

    def __getinitargs__(self):
        return ()


class Boolean(SymbolKind):
    pass


class Scalar(SymbolKind):
    """
    .. attribute:: is_real_valued

        Whether the value is definitely real-valued
    """

    def __init__(self, is_real_valued):
        super(Scalar, self).__init__(is_real_valued=is_real_valued)

    def __getinitargs__(self):
        return (self.is_real_valued,)


class Array(SymbolKind):
    """A variable-sized one-dimensional array.

    .. attribute:: is_real_valued

        Whether the value is definitely real-valued
    """

    def __init__(self, is_real_valued):
        super(Array, self).__init__(is_real_valued=is_real_valued)

    def __getinitargs__(self):
        return (self.is_real_valued,)


class ODEComponent(SymbolKind):
    def __init__(self, component_id):
        super(ODEComponent, self).__init__(component_id=component_id)

    def __getinitargs__(self):
        return (self.component_id,)

# }}}


class SymbolKindTable(object):
    """
    .. attribute:: global_table

        a mapping from symbol names to :class:`SymbolKind` instances,
        for global symbols

    .. attribute:: a nested mapping ``[function][symbol_name]``
        to :class:`SymbolKind` instances
    """

    def __init__(self):
        self.global_table = {
                "<t>": Scalar(is_real_valued=True),
                "<dt>": Scalar(is_real_valued=True),
                }
        self.per_function_table = {}

    def set(self, func_name, name, kind):
        if is_state_variable(name):
            tbl = self.global_table
        else:
            tbl = self.per_function_table.setdefault(func_name, {})

        if name in tbl:
            if tbl[name] != kind:
                raise RuntimeError(
                        "inconsistent 'kind' derived for '%s' in "
                        "'%s': '%s' vs '%s'"
                        % (name, func_name,
                            type(kind).__name__,
                            type(tbl[name]).__name__))
        else:
            tbl[name] = kind

    def get(self, func_name, name):
        if is_state_variable(name):
            tbl = self.global_table
        else:
            tbl = self.per_function_table.setdefault(func_name, {})

        return tbl[name]

    def __str__(self):
        def format_table(tbl, indent="  "):
            return "\n".join(
                    "%s%s: %s" % (indent, name, kind)
                    for name, kind in tbl.items())

        return "\n".join(
                ["global:\n%s" % format_table(self.global_table)] + [
                    "func '%s':\n%s" % (func_name, format_table(tbl))
                    for func_name, tbl in self.per_function_table.items()])


# {{{ kind inference mapper

class UnableToInferKind(Exception):
    pass


def unify(kind_a, kind_b):
    if kind_a is None:
        return kind_b
    if kind_b is None:
        return kind_a

    if isinstance(kind_a, Boolean):
        raise ValueError("arithmetic with flags is not permitted")
    if isinstance(kind_b, Boolean):
        raise ValueError("arithmetic with flags is not permitted")

    if isinstance(kind_a, ODEComponent):
        assert isinstance(kind_b, (ODEComponent, Scalar))

        if isinstance(kind_b, ODEComponent):
            if kind_a.component_id != kind_b.component_id:
                raise ValueError(
                        "encountered arithmetic with mismatched "
                        "ODE components")

        return kind_a

    if isinstance(kind_a, Array):
        assert isinstance(kind_b, (Array, Scalar))

        return Array(
                not (not kind_a.is_real_valued or not kind_b.is_real_valued))

    elif isinstance(kind_a, Scalar):
        if isinstance(kind_b, ODEComponent):
            return kind_b
        if isinstance(kind_b, Array):
            return Array(
                    not (not kind_a.is_real_valued or not kind_b.is_real_valued))

        assert isinstance(kind_b, Scalar)
        return Scalar(
                not (not kind_a.is_real_valued or not kind_b.is_real_valued))

    raise NotImplementedError("unknown kind '%s'" % type(kind_a).__name__)


class KindInferenceMapper(Mapper):
    """
    .. attribute:: global_table

        The :class:`SymbolKindTable` for the global scope.

    .. attribute:: local_table

        The :class:`SymbolKindTable` for the :class:`leap.vm.ir.Function`
        currently being processed.
    """

    def __init__(self, global_table, local_table, function_registry):
        self.global_table = global_table
        self.local_table = local_table
        self.function_registry = function_registry

    def map_constant(self, expr):
        if isinstance(expr, complex):
            return Scalar(is_real_valued=False)
        else:
            return Scalar(is_real_valued=True)

    def map_variable(self, expr):
        try:
            return self.global_table[expr.name]
        except KeyError:
            pass

        try:
            return self.local_table[expr.name]
        except KeyError:
            pass

        raise UnableToInferKind()

    def map_sum(self, expr):
        kind = None
        for ch in expr.children:
            try:
                ch_kind = self.rec(ch)
            except UnableToInferKind:
                pass
            else:
                kind = unify(kind, ch_kind)

        if kind is None:
            raise UnableToInferKind()
        else:
            return kind

    def map_product_like(self, children):
        kind = None
        for ch in children:
            kind = unify(kind, self.rec(ch))

        return kind

    def map_product(self, expr):
        return self.map_product_like(expr.children)

    def map_quotient(self, expr):
        return self.map_product_like((expr.numerator, expr.denominator))

    def map_power(self, expr):
        if not isinstance(self.rec(expr.exponent), Scalar):
            raise ValueError(
                    "exponentiation by '%s'"
                    "is meaningless"
                    % type(self.rec(expr.exponent)).__name__)

    def map_generic_call(self, function_id, arg_dict):
        func = self.function_registry[function_id]
        arg_kinds = {}
        for key, val in arg_dict.items():
            try:
                arg_kinds[key] = self.rec(val)
            except UnableToInferKind:
                arg_kinds[key] = None

        z = func.get_result_kind(arg_kinds)
        return z

    def map_call(self, expr):
        return self.map_generic_call(expr.function.name,
                dict(enumerate(expr.parameters)))

    def map_call_with_kwargs(self, expr):
        arg_dict = dict(enumerate(expr.parameters))
        arg_dict.update(expr.kw_parameters)
        return self.map_generic_call(expr.function.name, arg_dict)

    def map_comparison(self, expr):
        return Boolean()

    def map_logical_or(self, expr):
        for ch in expr.children:
            ch_kind = self.rec(ch)
            if not isinstance(ch_kind, Boolean):
                raise ValueError(
                        "logical operations on '%s' are undefined"
                        % type(ch_kind).__name__)

        return Boolean()

    map_logical_and = map_logical_or

    def map_logical_not(self, expr):
        ch_kind = self.rec(expr.child)
        if not isinstance(ch_kind, Boolean):
            raise ValueError(
                    "logical operations on '%s' are undefined"
                    % type(ch_kind).__name__)

        return Boolean()

    def map_max(self, expr):
        return Scalar(is_real_valued=True)

    map_min = map_max

    def map_subscript(self, expr):
        agg_kind = self.rec(expr.aggregate)
        if not isinstance(agg_kind, Array):
            raise ValueError(
                    "only arrays can be subscripted, not '%s' "
                    "which is a '%s'"
                    % (expr.aggregate, type(agg_kind).__name__))

        return Scalar(is_real_valued=agg_kind.is_real_valued)

# }}}


# {{{ symbol kind finder

class SymbolKindFinder(object):
    def __init__(self, function_registry):
        self.function_registry = function_registry

    def __call__(self, names, functions):
        """Return a :class:`SymbolKindTable`.
        """

        result = SymbolKindTable()

        from .ast_ import get_instructions_in_ast

        insn_queue = []
        for name, func in zip(names, functions):
            insn_queue.extend((name, insn) for insn in get_instructions_in_ast(func))

        insn_queue_push_buffer = []
        made_progress = False

        while insn_queue or insn_queue_push_buffer:
            if not insn_queue:
                if not made_progress:
                    raise RuntimeError("failed to infer types")

                insn_queue = insn_queue_push_buffer
                insn_queue_push_buffer = []
                made_progress = False

            func_name, insn = insn_queue.pop()

            if isinstance(insn, lang.AssignSolved):
                made_progress = True
                from leap.vm.utils import TODO
                raise TODO()

            elif isinstance(insn, lang.AssignExpression):
                kim = KindInferenceMapper(
                        result.global_table,
                        result.per_function_table.get(func_name, {}),
                        self.function_registry)

                try:
                    kind = kim(insn.expression)
                except UnableToInferKind:
                    insn_queue_push_buffer.append((func_name, insn))
                else:
                    made_progress = True
                    result.set(func_name, insn.assignee, kind=kind)

            else:
                # We only care about assignments.
                pass

        return result

# }}}

# vim: foldmethod=marker
