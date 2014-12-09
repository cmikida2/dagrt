#! /usr/bin/env python

from __future__ import division, with_statement

__copyright__ = "Copyright (C) 2014 Matt Wala"

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

import sys
from leap.vm.language import (CodeBuilder, SimpleCodeBuilder,
                              TimeIntegratorCode)
from pymbolic import var
from pymbolic.primitives import Comparison

from leap.vm.exec_numpy import NumpyInterpreter  # noqa
from leap.vm.codegen import PythonCodeGenerator  # noqa

from utils import (  # noqa
        python_method_impl_interpreter as pmi_int,
        python_method_impl_codegen as pmi_cg)

from utils import execute_and_return_single_result


def test_SimpleCodeBuilder_yield(python_method_impl):
    cb = CodeBuilder()
    with SimpleCodeBuilder(cb) as builder:
        yield_ = builder.yield_state(1, 'x', 0, 'final')
    code = TimeIntegratorCode.create_with_init_and_step(
            [], yield_, cb.instructions, True)
    result = execute_and_return_single_result(python_method_impl, code)
    assert result == 1


def test_SimpleCodeBuilder_assign(python_method_impl):
    cb = CodeBuilder()
    with SimpleCodeBuilder(cb) as builder:
        builder.assign(var('x'), 1)
        yield_ = builder.yield_state(var('x'), 'x', 0, 'final')
    code = TimeIntegratorCode.create_with_init_and_step(
            [], yield_, cb.instructions, True)
    result = execute_and_return_single_result(python_method_impl, code)
    assert result == 1


def test_SimpleCodeBuilder_condition(python_method_impl):
    cb = CodeBuilder()
    with SimpleCodeBuilder(cb) as builder:
        builder.assign(var('x'), 1)
        with builder.condition(Comparison(var('x'), '==', 1)):
            builder.assign(var('x'), 2)
        yield_ = builder.yield_state(var('x'), 'x', 0, 'final')
    code = TimeIntegratorCode.create_with_init_and_step(
            [], yield_, cb.instructions, True)
    result = execute_and_return_single_result(python_method_impl, code)
    assert result == 2


def test_SimpleCodeBuilder_nested_condition(python_method_impl):
    cb = CodeBuilder()
    with SimpleCodeBuilder(cb) as builder:
        builder.assign(var('x'), 1)
        with builder.condition(Comparison(var('x'), '==', 1)):
            builder.assign(var('x'), 2)
            with builder.condition(Comparison(var('x'), '==', 2)):
                builder.assign(var('x'), 3)
            yield_ = builder.yield_state(var('x'), 'x', 0, 'final')
    code = TimeIntegratorCode.create_with_init_and_step(
            [], yield_, cb.instructions, True)
    result = execute_and_return_single_result(python_method_impl, code)
    assert result == 3


def test_SimpleCodeBuilder_dependencies(python_method_impl):
    cb = CodeBuilder()
    with SimpleCodeBuilder(cb) as builder:
        dependency = builder.assign(var('x'), 1)
    with SimpleCodeBuilder(cb, dependency) as builder:
        yield_ = builder.yield_state(var('x'), 'x', 0, 'final')
    code = TimeIntegratorCode.create_with_init_and_step(
            [], yield_, cb.instructions, True)
    result = execute_and_return_single_result(python_method_impl, code)
    assert result == 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exec(sys.argv[1])
    else:
        from py.test.cmdline import main
        main([__file__])
