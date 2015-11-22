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

from pymbolic.mapper.evaluator import EvaluationMapper as EvaluationMapperBase
from pymbolic.mapper.dependency import DependencyMapper
from pymbolic.mapper import CombineMapper, IdentityMapper
from pymbolic.mapper.unifier import UnidirectionalUnifier
from pymbolic.primitives import Variable, is_constant
from pymbolic.parser import Parser
from pymbolic.primitives import If as IfThenElse # noqa

import logging
import operator
import pytools.lex
import six.moves

logger = logging.getLogger(__name__)


# Precedence constant for IfThenElse.
PREC_IFTHENELSE = 1


class ExtendedDependencyMapper(DependencyMapper):
    """Extends DependencyMapper to handle values encountered in dagrt
    IR.
    """

    def map_foreign(self, expr):
        if expr is None or isinstance(expr, str):
            return frozenset()
        else:
            return super(ExtendedDependencyMapper, self).map_foreign(expr)


class EvaluationMapper(EvaluationMapperBase):

    def __init__(self, context, functions):
        """
        :arg context: a mapping from variable names to values
        :arg functions: a mapping from function names to functions
        """
        EvaluationMapperBase.__init__(self, context)
        self.functions = functions

    def map_variable(self, expr):
        if expr.name in self.context:
            return self.context[expr.name]
        elif expr.name in self.functions:
            return self.functions[expr.name]

    def map_generic_call(self, function_name, parameters, kw_parameters):
        if function_name in self.functions:
            function = self.functions[function_name]
        else:
            raise ValueError("Call to unknown function: " + str(function_name))
        evaluated_parameters = (self.rec(param) for param in parameters)
        evaluated_kw_parameters = dict(
                (param_id, self.rec(param))
                for param_id, param in six.iteritems(kw_parameters))
        return function(*evaluated_parameters, **evaluated_kw_parameters)

    def map_call(self, expr):
        return self.map_generic_call(expr.function.name, expr.parameters, {})

    def map_call_with_kwargs(self, expr):
        return self.map_generic_call(expr.function.name, expr.parameters,
                                     expr.kw_parameters)


class _ConstantFindingMapper(CombineMapper):
    """Classify subexpressions according to whether they are "constant"
    (have no free variables) or not.
    TODO: CSE caching
    """

    def __init__(self, free_variables):
        self.free_variables = free_variables
        self.node_stack = []

    def __call__(self, expr):
        self.is_constant = {}
        for variable in self.free_variables:
            self.is_constant[variable] = False
        self.node_stack.append(expr)
        CombineMapper.__call__(self, expr)
        return self.is_constant

    def rec(self, expr):
        self.node_stack.append(expr)
        return CombineMapper.rec(self, expr)

    def combine(self, exprs):
        current_expr = self.node_stack.pop()
        result = six.moves.reduce(operator.and_, exprs)
        self.is_constant[current_expr] = result
        return result

    def map_constant(self, expr):
        self.node_stack.pop()
        self.is_constant[expr] = True
        return True

    map_function_symbol = map_constant

    def map_variable(self, expr):
        self.node_stack.pop()
        result = expr not in self.free_variables
        self.is_constant[expr] = result
        return result


def _is_atomic(expr):
    return isinstance(expr, Variable) or is_constant(expr)


class _ExpressionCollapsingMapper(IdentityMapper):
    """Create a new expression that collapses constant expressions
    (subexpressions with no free variables). Return the new expression
    and an assignment that converts the input to the new expression.
    TODO: CSE caching
    """

    def __init__(self, free_variables):
        self.constant_finding_mapper = _ConstantFindingMapper(free_variables)

    def __call__(self, expr, new_var_func):
        self.new_var_func = new_var_func
        self.is_constant = self.constant_finding_mapper(expr)
        self.assignments = {}
        result = IdentityMapper.__call__(self, expr)
        return result, self.assignments

    def rec(self, expr):
        if _is_atomic(expr) or not self.is_constant[expr]:
            return IdentityMapper.rec(self, expr)
        else:
            new_var = self.new_var_func()
            self.assignments[new_var] = expr
            return new_var

    def map_commut_assoc(self, expr, combine_func):
        # Classify children according to whether they are constant or
        # non-constant. If children are non-constant, it's possible that
        # subexpressions of the children are still constant, so recurse
        # on the non-constant children.
        constants = []
        non_constants = []
        for child in expr.children:
            if self.is_constant[child]:
                constants.append(child)
            else:
                non_constants.append(self.rec(child))

        constants = tuple(constants)
        non_constants = tuple(non_constants)

        # Return the combined sum/product of the constants and
        # non-constants. Take special care to ensure that the
        # constructed sum/product is a binary expression. If not then in
        # place of returning the binary expression return whichever leaf
        # is non-empty.
        if not constants:
            assert non_constants
            if len(non_constants) > 1:
                return combine_func(non_constants)
            else:
                return self.non_constants[0]

        if len(constants) == 1 and _is_atomic(constants[0]):
            folded_constant = constants[0]
        else:
            new_var = self.new_var_func()
            self.assignments[new_var] = constants[0] \
                if len(constants) == 1 else combine_func(constants)
            folded_constant = new_var

        if non_constants:
            return combine_func(tuple([folded_constant]) + non_constants)
        else:
            return folded_constant

    def map_product(self, expr):
        from pymbolic.primitives import Product
        return self.map_commut_assoc(expr, Product)

    def map_sum(self, expr):
        from pymbolic.primitives import Sum
        return self.map_commut_assoc(expr, Sum)


def collapse_constants(expression, free_variables, assign_func, new_var_func):
    """
    Emit a sequence of calls that assign the constant subexpressions in
    the input to variables.  Return the expression that results from
    collapsing all the constant subexpressions into variables.

    :arg expression: A pymbolic expression
    :arg free_variables: The list of free variables in the expression
    :arg assign_func: A function to call to assign a variable to a constant
                      subexpression
    :arg new_var_func: A function to call to make a new variable
    """
    mapper = _ExpressionCollapsingMapper(free_variables)
    new_expression, variable_map = mapper(expression, new_var_func)
    for variable, expr in variable_map.items():
        assign_func(variable, expr)
    return new_expression


class _UnidirectionalUnifierWithFunctionCalls(UnidirectionalUnifier):
    """This class extends the unification mapper to handle terms with
    function calls. The function symbols are assumed to be constants.
    """

    def map_call(self, expr, other, urecs):

        if not isinstance(expr, type(other)):
            return []

        if expr.function != other.function:
            return []

        expr_parameters = expr.parameters
        other_parameters = other.parameters

        if len(expr_parameters) != len(other_parameters):
            return []

        from pymbolic.primitives import CallWithKwargs

        if isinstance(expr, CallWithKwargs):
            from operator import itemgetter

            if set(expr.kw_parameters.keys()) != set(other.kw_parameters.keys()):
                return []

            expr_parameters += tuple(val for key, val in
                                     sorted(expr.kw_parameters.items(),
                                            key=itemgetter(0)))
            other_parameters += tuple(val for key, val in
                                      sorted(other.kw_parameters.items(),
                                             key=itemgetter(0)))

        for expr_param, other_param in zip(expr_parameters, other_parameters):
            urecs = self.rec(expr_param, other_param, urecs)

        return urecs

    map_call_with_kwargs = map_call


def match(template, expression, free_variable_names):
    """Attempt to match the free variables found in `template` to terms in
    `expression`, modulo associativity and commutativity.

    This implements a one-way unification algorithm, matching free
    variables in `template` to subexpressions of `expression`.

    Return a map from variable names in `free_variable_names` to
    expressions.

    """
    unifier = _UnidirectionalUnifierWithFunctionCalls(free_variable_names)
    records = unifier(template, expression)
    if not records:
        raise ValueError("Cannot unify expressions.")
    return dict((key.name, val) for key, val in records[0].equations)


def _hack_lex_table(lex_table):
    new_lex_table = []
    for entry in lex_table:
        if entry[0] == "identifier":
            # Allow backticks to delimit identifiers.
            entry = ("identifier", ("|", entry[1],
                    pytools.lex.RE("`[<>:a-zA-Z0-9_]*`")))
        new_lex_table.append(entry)
    return new_lex_table


class _ExtendedParser(Parser):
    lex_table = _hack_lex_table(Parser.lex_table)


class _RenameVariableMapper(IdentityMapper):

    def __init__(self, rename_func):
        self.rename_func = rename_func

    def map_variable(self, expr):
        renamed = self.rename_func(expr.name)
        if renamed is None:
            return expr
        else:
            from pymbolic import var
            return var(renamed)


def parse(expr):
    """Return a pymbolic expression constructed from the string. Values
    between backticks ("`") are parsed as variable names.
    """
    from pymbolic import var

    def remove_backticks(expr):
        if not isinstance(expr, var):
            return expr
        varname = expr.name
        if varname.startswith('`') and varname.endswith('`'):
            return var(varname[1:-1])
        return expr

    from pymbolic.mapper.substitutor import SubstitutionMapper
    parser = _ExtendedParser()
    substitutor = SubstitutionMapper(remove_backticks)
    return substitutor(parser(expr))


# vim: foldmethod=marker
