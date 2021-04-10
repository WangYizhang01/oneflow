"""
Copyright 2020 The OneFlow Authors. All rights reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""
import oneflow as flow
from oneflow.python.oneflow_export import oneflow_export
from oneflow.python.framework.tensor import Tensor
from oneflow.python.nn.module import Module
from oneflow.python.nn.common_types import _size_1_t, _size_2_t, _size_3_t
from typing import Optional, List, Tuple
import math


@oneflow_export("nn.Identity")
class Identity(Module):
    r"""A placeholder identity operator that is argument-insensitive.

    Args:
        args: any argument (unused)
        kwargs: any keyword argument (unused)

    For example: 

    .. code-block:: python 

        import numpy as np
        import oneflow as flow

        m = flow.nn.Identity(54, unused_argument1=0.1, unused_argument2=False)
        input = flow.Tensor(2, 3, 4, 5)
        output = m(input)

    """

    def __init__(self, *args, **kwargs):
        super(Identity, self).__init__()

    def forward(self, input: Tensor) -> Tensor:
        return input


@oneflow_export("nn.Linear")
class Linear(Module):
    def __init__(self, in_features: int, out_features: int, bias: bool = True) -> None:
        super().__init__()
        self.use_bias = bias
        self.weight = flow.nn.Parameter(flow.Tensor(out_features, in_features))

        if bias:
            self.bias = flow.nn.Parameter(flow.Tensor(out_features))
            self._bias_add_op = (
                flow.builtin_op("bias_add")
                .Input("a")
                .Input("b")
                .Output("out")
                .Attr("axis", 1)
                .Build()
            )
        else:
            self.register_parameter("bias", None)

        self._op = (
            flow.builtin_op("matmul")
            .Input("a")
            .Input("b")
            .Output("out")
            .Attr("transpose_a", False)
            .Attr("transpose_b", True)
            .Build()
        )
        self.reset_parameters()

    def reset_parameters(self) -> None:
        flow.nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
        if self.bias is not None:
            fan_in, _ = flow.nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / math.sqrt(fan_in)
            flow.nn.init.uniform_(self.bias, -bound, bound)

    def forward(self, x):
        if self.use_bias:
            res = self._bias_add_op(self._op(x, self.weight)[0], self.bias)[0]
        else:
            res = self._op(x, self.weight)[0]
        return res
