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
from __future__ import absolute_import

import copy
import functools
import re
import inspect
import traceback
from typing import Any, Callable, Optional, Union

import oneflow.python.framework.session_context as session_ctx
import oneflow.python.framework.hob as hob
import oneflow.python.lib.core.enable_if as enable_if
from oneflow.python.oneflow_export import oneflow_export, oneflow_deprecate
from oneflow.python.framework.function_desc import FunctionDesc
import oneflow.python.framework.placement_context as placement_ctx
import oneflow.python.framework.distribute_context as distribute_ctx
import oneflow.python.framework.placement_context as placement_ctx
import oneflow.python.framework.session_context as session_ctx
import oneflow.python.framework.typing_util as oft_util
import oneflow.python.lib.core.pb_util as pb_util
import oneflow.python.framework.attr_util as attr_util
import oneflow.python.framework.stage as stage_util
from oneflow.python.framework.function_desc import FunctionDesc
from oneflow.python.oneflow_export import oneflow_export
import oneflow
import traceback
import sys


@oneflow_export("FunctionConfig", "function_config")
class FunctionConfig(object):
    r"""OneFlow function's configurations.
    """

    def __init__(self) -> None:
        self.function_desc = FunctionDesc()

    def __getattr__(
        self, attr_name: str
    ) -> Callable[[Optional[Union[bool, int, float, str]]], None]:
        name2default = session_ctx.GetDefaultSession().function_flag_name2default_val
        assert attr_name in name2default
        flag_name2flag_value = self.function_desc.job_config_proto.flag_name2flag_value
        default_val = name2default[attr_name]

        def FunctionConfigSetter(
            py_value: Optional[Union[bool, int, float, str, list]] = None
        ) -> None:
            attr_util.SetAttrValue(
                flag_name2flag_value[attr_name], py_value, default_val
            )

        return FunctionConfigSetter

    def ssp_placement(self, *stages, stage_partition_strategy="naive_sequantial"):
        self.enable_stage_partition(True)
        self.stage_partition_scope_ids(_GetScopeSymbolIds(stages))
        self.stage_partition_strategy(stage_partition_strategy)
        self.enable_ssp_variable_proxy(True)
        self.enable_stage_buffer(True)


@oneflow_export("global_function")
def api_oneflow_function(
    type: str = "predict", function_config: FunctionConfig = None,
) -> Callable[[Callable], Callable]:
    r"""Creates a callable OneFlow global function from a Python function.

    For instance::

        @oneflow.global_function(flow.FunctionConfig())
        def train():
            # your model

    Args:
        function_config (FunctionConfig, optional): a `FunctionConfig` object. Defaults to FunctionConfig().

    Returns:
        Callable[[Callable], Callable]: a callable which is called to execute the compiled function
    """
    if isinstance(type, FunctionConfig):
        function_config = type
        print(
            """WARNING: flow.global_function(func_config) is deprecated. Please replace it with flow.global_function(type, func_config).
            """
        )
        print(traceback.format_stack()[-2])
    else:
        assert type in ["train", "predict"]
        if function_config is None:
            function_config = FunctionConfig()
        if type == "train":
            function_config.function_desc.job_config_proto.train_conf.SetInParent()
        else:
            function_config.function_desc.job_config_proto.predict_conf.SetInParent()
    api = enable_if.unique([eager_oneflow_function, lazy_oneflow_function])
    return api(function_config)


@enable_if.condition(hob.in_normal_mode & hob.eager_execution_enabled)
def eager_oneflow_function(function_config=FunctionConfig()):
    assert isinstance(function_config, FunctionConfig)

    def Decorator(job_func):
        if not hasattr(job_func, "__oneflow_function_signature__"):
            job_func.__oneflow_function_signature__ = inspect.signature(job_func)
        oft_util.CheckGlobalFunctionAnnotation(job_func.__oneflow_function_signature__)
        sess = session_ctx.GetDefaultSession()
        function_desc = _CloneFunctionDesc(function_config.function_desc, job_func)

        @functools.wraps(job_func)
        def Func(*args, **kwargs):
            return _RunEagerJob(sess, function_desc, *args, **kwargs)

        for x in dir(job_func):
            if x.startswith("__oneflow_"):
                setattr(Func, x, getattr(job_func, x))
        return Func

    return Decorator


@enable_if.condition(
    hob.in_normal_mode & ~hob.eager_execution_enabled & ~hob.session_initialized
)
def lazy_oneflow_function(function_config=FunctionConfig()):
    assert isinstance(function_config, FunctionConfig)

    def Decorator(job_func):
        if not hasattr(job_func, "__oneflow_function_signature__"):
            job_func.__oneflow_function_signature__ = inspect.signature(job_func)
        oft_util.CheckGlobalFunctionAnnotation(job_func.__oneflow_function_signature__)
        sess = session_ctx.GetDefaultSession()

        @functools.wraps(job_func)
        def Func(*args, **kwargs):
            return _RunLazyJob(sess, job_func, *args, **kwargs)

        sess.AddJob(_CloneFunctionDesc(function_config.function_desc, job_func))
        for x in dir(job_func):
            if x.startswith("__oneflow_"):
                setattr(Func, x, getattr(job_func, x))
        return Func

    return Decorator


def _CloneFunctionDesc(func_desc, job_func):
    new_func_desc = FunctionDesc(job_func=job_func)
    new_func_desc.job_config_proto.CopyFrom(func_desc.job_config_proto)
    new_func_desc.function_attribute = copy.deepcopy(func_desc.function_attribute)
    return new_func_desc


def oneflow_function_config(*field_paths):
    def Decorator(func):
        global _class_property2return_obj_class
        for field_path in field_paths:
            fields = field_path.split(".")
            assert len(fields) > 0
            cls = FunctionConfig
            for index, field in enumerate(fields):
                assert field != "function_desc"
                assert re.match("^[_\w]+[_\w\d]*$", field)
                if (cls, field) not in _class_property2return_obj_class:
                    class_name = ".".join(["function_config"] + fields[: index + 1])

                    def Init(self, function_desc):
                        self.function_desc = function_desc

                    config_class = type(class_name, (object,), dict(__init__=Init))
                    setattr(cls, field, _MakeInnerJobConfigClassProperty(config_class))
                    _class_property2return_obj_class[cls, field] = config_class
                cls = _class_property2return_obj_class[cls, field]
            cls.__call__ = _MakeLeafJobConfigCall(func)
        return func

    return Decorator


_class_property2return_obj_class = {}


def _MakeInnerJobConfigClassProperty(return_obj_class):
    return property(lambda self: return_obj_class(self.function_desc))


def _MakeLeafJobConfigCall(method):
    return lambda self, *argv, **kwarg: method(self.function_desc, *argv, **kwarg)


def _RunEagerJob(session, function_desc, *args):
    return session.TryInit().EagerRun(function_desc, *args)


def _RunLazyJob(session, job_func, *args, **kwargs):
    return session.TryInit().LazyRun(job_func, *args, **kwargs)


@oneflow_function_config("default_data_type")
def set_default_data_type(func_desc, value):
    r"""Set default data type for job

    Args:
        func_desc ([type]): job function
        value ([type]): data type. e.g. flow.float
    """
    func_desc.job_config_proto.default_data_type = value.oneflow_proto_dtype


@oneflow_function_config("default_initializer_conf")
def set_default_initializer_conf(func_desc, value):
    r"""Set default initial configuration for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    assert type(value) is dict
    pb_util.PythonDict2PbMessage(
        value, func_desc.job_config_proto.default_initializer_conf
    )


@oneflow_function_config("exp_run_conf")
def set_exp_run_conf(value):
    r"""Set experimental configuration for job

    Args:
        value ([type]): [description]
    """
    assert type(func_desc, value) is dict
    pb_util.PythonDict2PbMessage(value, func_desc.job_config_proto.exp_run_conf)


@oneflow_function_config("use_memory_allocation_algorithm_v2")
def set_use_memory_allocation_algorithm_v2(func_desc, value):
    r"""Set to use memory allocation algorithm(v2)

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.use_memory_allocation_algorithm_v2 = value


@oneflow_function_config("static_mem_alloc_policy_white_list.has")
def static_mem_alloc_policy_white_list_has_policy(func_desc, policy):
    r"""Get items from white list related to static memory allocation policy

    Args:
        func_desc ([type]): [description]
        policy ([type]): [description]

    Returns:
        [type]: [description]
    """
    return getattr(func_desc.job_config_proto.memory_allocation_algorithm_conf, policy)


@oneflow_function_config("static_mem_alloc_policy_white_list.add")
def static_mem_alloc_policy_white_list_add_policy(func_desc, policy):
    r"""Add item to white list related to static memory allocation policy

    Args:
        func_desc ([type]): [description]
        policy ([type]): [description]
    """
    setattr(func_desc.job_config_proto.memory_allocation_algorithm_conf, policy, True)


@oneflow_function_config("static_mem_alloc_policy_white_list.remove")
def static_mem_alloc_policy_white_list_remove_policy(func_desc, policy):
    r"""Remove item of white list related to static memory allocation policy

    Args:
        func_desc ([type]): [description]
        policy ([type]): [description]
    """
    setattr(func_desc.job_config_proto.memory_allocation_algorithm_conf, policy, False)


@oneflow_function_config("static_mem_alloc_policy_white_list.policy_mem_size_first")
def policy_mem_size_first(func_desc):
    r"""A static memory allocation policy called: mem_size_first

    Args:
        func_desc ([type]): [description]

    Returns:
        [type]: [description]
    """
    return "use_mem_size_first_algo"


@oneflow_function_config(
    "static_mem_alloc_policy_white_list.policy_mutual_exclusion_first"
)
def policy_mutual_exclusion_first(func_desc):
    r"""A static memory allocation policy called: mutual_exclusion_first

    Args:
        func_desc ([type]): [description]

    Returns:
        [type]: [description]
    """
    return "use_mutual_exclusion_first_algo"


@oneflow_function_config("static_mem_alloc_policy_white_list.policy_time_line")
def policy_time_line(func_desc):
    r"""A static memory allocation policy called: time_line

    Args:
        func_desc ([type]): [description]

    Returns:
        [type]: [description]
    """
    return "use_time_line_algo"


@oneflow_function_config("static_mem_alloc_algo_white_list.show")
def show_static_mem_alloc_algo_white_list(func_desc):
    r"""Show configuration of  static memory allocation policy,
          including: "use_mem_size_first_algo", "use_mutual_exclusion_first_algo", "use_time_line_algo"

    Args:
        func_desc ([type]): [description]

    Returns:
        [type]: [description]
    """
    return [
        "use_mem_size_first_algo",
        "use_mutual_exclusion_first_algo",
        "use_time_line_algo",
    ]


@oneflow_function_config("enable_cudnn")
def set_enable_cudnn(func_desc, value=True):
    r"""Whether use cudnn to accelerate job or not.

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_cudnn = value


@oneflow_function_config("cudnn_buf_limit_mbyte")
def set_cudnn_buf_limit_mbyte(func_desc, value):
    r"""Set cudnn buffer limit, e.g. 1024mb

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_buf_limit_mbyte = value


@oneflow_function_config("cudnn_conv_force_fwd_algo")
def set_cudnn_conv_force_fwd_algo(func_desc, value):
    r"""Set value to cudnn conv_force_forward algorithm

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_force_fwd_algo = value


@oneflow_function_config("cudnn_conv_force_bwd_data_algo")
def set_cudnn_conv_force_bwd_data_algo(func_desc, value):
    r"""Set value to cudnn conv_force_backward_data algorithm

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_force_bwd_data_algo = value


@oneflow_function_config("cudnn_conv_force_bwd_filter_algo")
def set_cudnn_conv_force_bwd_filter_algo(func_desc, value):
    r"""Set value to cudnn conv_force_backward_filter algorithm

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_force_bwd_filter_algo = value


@oneflow_function_config("cudnn_conv_heuristic_search_algo")
def set_cudnn_conv_heuristic_search_algo(func_desc, value):
    r"""Set value to cudnn conv_heuristic_search algorithm

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_heuristic_search_algo = value


@oneflow_function_config("enable_cudnn_fused_normalization_add_relu")
def set_enable_cudnn_fused_normalization_add_relu(func_desc, value):
    r"""Whether enable cudnn_fused_normalization_add_relu.

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.enable_cudnn_fused_normalization_add_relu = value


@oneflow_function_config("enable_fuse_add_to_output")
def set_enable_fuse_add_to_output(func_desc, value):
    r"""Whether enable fuse_add_to_output.
            If enabled, try to fuse a binary element-wise add to one of the predecessors to improve performance.

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.enable_fuse_add_to_output = value


@oneflow_function_config("cudnn_conv_use_deterministic_algo_only")
def set_cudnn_conv_use_deterministic_algo_only(func_desc, value):
    r"""Set value to cudnn conv_use_deterministic_only algorithm

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_use_deterministic_algo_only = value


@oneflow_function_config("enable_reused_mem")
def set_enable_reused_mem(func_desc, value=True):
    r"""Whether enable reuse memory or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_reused_mem = value


@oneflow_function_config("enable_inplace")
def set_enable_inplace(func_desc, value=True):
    r"""Whether enable inplace  or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_inplace = value


@oneflow_function_config("enable_inplace_in_reduce_struct")
def set_enable_inplace_in_reduce_struct(func_desc, value=True):
    print(
        "'enable_inplace_in_reduce_struct' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("enable_nccl")
def set_enable_nccl(func_desc, value=True):
    print(
        "'enable_nccl' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("use_nccl_inter_node_communication")
def set_use_nccl_inter_node_communication(func_desc, value=True):
    print(
        "'use_nccl_inter_node_communication' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("use_boxing_v2")
def set_use_boxing_v2(func_desc, value=True):
    print(
        "'use_boxing_v2' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("do_parallel_cast_before_widening_type_cast")
def set_do_parallel_cast_before_widening_type_cast(func_desc, value=True):
    func_desc.job_config_proto.do_parallel_cast_before_widening_type_cast = value


@oneflow_function_config("enable_all_reduce_group")
def set_enable_all_reduce_group(func_desc, value=True):
    print(
        "'enable_all_reduce_group' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("all_reduce_group_num")
def set_all_reduce_group_num(func_desc, value):
    print(
        "'all_reduce_group_num' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("all_reduce_lazy_ratio")
def set_all_reduce_lazy_ratio(func_desc, value):
    print(
        "'all_reduce_lazy_ratio' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("all_reduce_group_min_mbyte")
def set_all_reduce_group_min_mbyte(func_desc, value):
    print(
        "'all_reduce_group_min_mbyte' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("all_reduce_group_size_warmup")
def set_all_reduce_group_size_warmup(func_desc, value):
    print(
        "'all_reduce_group_size_warmup' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("all_reduce_fp16")
def set_all_reduce_fp16(func_desc, value=True):
    print(
        "'all_reduce_fp16' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("enable_non_distributed_optimizer")
def set_enable_non_distributed_optimizer(func_desc, value=True):
    r"""Whether enable non_distributed optimizer or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_non_distributed_optimizer = value


@oneflow_function_config("disable_all_reduce_sequence")
def set_disable_all_reduce_sequence(func_desc, value=True):
    print(
        "'disable_all_reduce_sequence' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config("prune_parallel_cast_ops")
def set_prune_parallel_cast_ops(func_desc, value=True):
    r"""Whether prune parallel cast  operations or not.

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.prune_parallel_cast_ops = value


@oneflow_function_config("prune_cast_to_static_shape_ops")
def set_prune_cast_to_static_shape_ops(func_desc, value=True):
    r"""Whether or not set prune_cast to static shape opretions

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.prune_cast_to_static_shape_ops = value


@oneflow_function_config("non_distributed_optimizer_group_size_mbyte")
def set_non_distributed_optimizer_group_size_mbyte(func_desc, value):
    print(
        "'non_distributed_optimizer_group_size_mbyte' has been deprecated, has no effect and will be removed in the future."
    )


@oneflow_function_config(
    "enable_true_half_config_when_conv", "cudnn_conv_enable_true_half"
)
def set_cudnn_conv_enable_true_half(func_desc, value=True):
    r"""Whether  use true_half mode or not during  convolution calculation process while using cudnn.

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.cudnn_conv_enable_pseudo_half = not value


@oneflow_function_config(
    "cudnn_conv_enable_pseudo_half", "enable_cudnn_conv_pseudo_half"
)
def set_cudnn_conv_enable_pseudo_half(func_desc, value):
    r"""Whether  enable pseudo_half mode or not during  convolution calculation process while using cudnn

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.cudnn_conv_enable_pseudo_half = value


@oneflow_function_config("enable_float_compute_for_half_gemm")
def set_enable_float_compute_for_half_gemm(func_desc, value=True):
    r"""Whether  enable float_compute or not ,
          if True, means that the type of intermedia value is float when compute half gemm.

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_float_compute_for_half_gemm = value


@oneflow_function_config("enable_auto_mixed_precision")
def set_enable_auto_mixed_precision(func_desc, value=True):
    r"""If true, then job will use mixed precision mode, it means use both float16 and float32 during model training.

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_auto_mixed_precision = value


@oneflow_function_config("enable_keep_header_only")
def set_enable_keep_header_only(func_desc, value=True):
    r"""Whether keep header only or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.enable_keep_header_only = value


@oneflow_function_config("concurrency_width")
def set_concurrency_width(func_desc, value):
    r"""Set up concurrency width

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.concurrency_width = value


@oneflow_function_config("train.model_update_conf")
def set_model_update_conf(func_desc, value):
    r"""Set up optimizer and update method of learning rate  for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    print(
        """WARNING: func_config.train.* has been deprecated. Please replace it by the new optimizer api.
        """
    )
    print(traceback.format_stack()[-3])
    assert type(value) is dict
    pb_msg = func_desc.job_config_proto.train_conf.model_update_conf
    pb_util.PythonDict2PbMessage(value, pb_msg)


@oneflow_function_config("indexed_slices_optimizer_conf")
def set_indexed_slices_optimizer_conf(func_desc, value):
    r"""Set indexed slices configuration of optimizer

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    assert type(value) is dict
    pb_msg = func_desc.job_config_proto.indexed_slices_optimizer_conf
    pb_util.PythonDict2PbMessage(value, pb_msg)


@oneflow_function_config("enable_fuse_model_update_ops")
def set_enable_fuse_model_update_ops(func_desc, value=True):
    r"""Whether enable fuse_model_update_ops.
            If enabled, try to fuse cast + scale + l1_l2_regularize_gradient + model_update to one op to improve performance.

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.job_config_proto.enable_fuse_model_update_ops = value


@oneflow_function_config("train.loss_scale_factor")
def set_loss_scale_factor(func_desc, value):
    r"""Set scale factor for loss

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    print(
        """WARNING: func_config.train.* has been deprecated. Please replace it by the new optimizer api.
        """
    )
    print(traceback.format_stack()[-3])
    func_desc.job_config_proto.train_conf.loss_scale_factor = value


@oneflow_function_config("train.primary_lr")
def set_primary_lr(func_desc, value):
    r"""Set the primary leaning rate for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    print(
        """WARNING: func_config.train.* has been deprecated. Please replace it by the new optimizer api.
        """
    )
    print(traceback.format_stack()[-3])
    func_desc.job_config_proto.train_conf.primary_lr = value


@oneflow_function_config("train.secondary_lr")
def set_secondary_lr(func_desc, value):
    r"""Set the secondary leaning rate for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    print(
        """WARNING: func_config.train.* has been deprecated. Please replace it by the new optimizer api.
        """
    )
    print(traceback.format_stack()[-3])
    func_desc.job_config_proto.train_conf.secondary_lr = value


@oneflow_function_config("default_placement_scope")
def set_default_placement(func_desc, value):
    r"""Set the default placement for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    assert isinstance(value, placement_ctx.EmptyPlacementScope)
    func_desc.function_attribute.default_placement_scope = value


@oneflow_function_config("use_xla_jit")
def set_use_xla_jit(func_desc, value=True):
    r"""Whether use xla  or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.xrt_config.use_xla_jit = value


@oneflow_function_config("use_tensorrt")
def set_use_tensorrt(func_desc, value=True):
    r"""Whether use tensorrt or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    func_desc.job_config_proto.xrt_config.use_tensorrt = value


@oneflow_function_config("tensorrt.use_fp16")
def set_tensorrt_use_fp16(func_desc, value=True):
    r"""Whether use tensorrt fp16  or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    set_use_tensorrt(func_desc, True)
    func_desc.job_config_proto.xrt_config.tensorrt_config.use_fp16 = value


@oneflow_function_config("tensorrt.use_int8")
def set_tensorrt_use_int8(func_desc, value=True):
    r"""Whether use tensorrt int8 mode or not

    Args:
        func_desc ([type]): [description]
        value (bool, optional): [description]. Defaults to True.
    """
    set_use_tensorrt(func_desc, True)
    func_desc.job_config_proto.xrt_config.tensorrt_config.use_int8 = value


@oneflow_function_config("tensorrt.int8_calibration")
def set_tensorrt_int8_calibration(func_desc, value):
    r"""Set up calibration of tensorrt int8

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    assert func_desc.job_config_proto.xrt_config.tensorrt_config.use_int8
    func_desc.job_config_proto.xrt_config.tensorrt_config.int8_calibration = value


@oneflow_function_config("default_logical_view")
def set_default_distribute_strategy(func_desc, value):
    r"""Set up default distribute strategy for job

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    assert isinstance(value, distribute_ctx.DistributeStrategy)
    func_desc.function_attribute.default_distribute_strategy = value


@oneflow_function_config("allow_cpu_return_op")
def allow_cpu_return_op(func_desc, value):
    r"""Whether allow operaions returned from cpu or  not

    Args:
        func_desc ([type]): [description]
        value ([type]): [description]
    """
    func_desc.function_attribute.allow_cpu_return_op = value


@oneflow_function_config("default_distribute_strategy")
@oneflow_deprecate()
def deprecated_set_default_distribute_strategy(*args, **kwargs):
    print(
        "WARNING:",
        "function_config.default_distribute_strategy",
        "has been deprecated. Please use {} instead.".format(
            "function_config.default_logical_view"
        ),
    )
    print(traceback.format_stack()[-3], file=sys.stderr)
    set_default_distribute_strategy(*args, **kwargs)


def _GetScopeSymbolIds(stages):
    scope_symbol_ids = []
    assert all(x.stage_placement_id is None for x in stages) or all(
        x.stage_placement_id is not None for x in stages
    )
    assert all(x.stage_weight_buffer_size is None for x in stages) or all(
        x.stage_weight_buffer_size is not None for x in stages
    )
    num = len(stages)
    for i, stage in enumerate(stages):
        assert isinstance(stage, stage_util.Stage)
        stage_placement_id = stage.stage_placement_id
        if stage_placement_id is None:
            stage_placement_id = i
        else:
            assert isinstance(stage_placement_id, int)
            assert stage_placement_id >= 0
            assert stage_placement_id < num
        stage_weight_buffer_size = stage.stage_weight_buffer_size
        if stage_weight_buffer_size is None:
            stage_weight_buffer_size = num - i
        else:
            assert isinstance(stage_weight_buffer_size, int)
            assert stage_weight_buffer_size > 0

        for placement in stage.placements:
            with placement, oneflow.experimental.scope.config(
                stage_weight_buffer_size=stage_weight_buffer_size,
                stage_placement_id=stage_placement_id,
            ):
                scope_symbol_ids.append(oneflow.current_scope().symbol_id)
    return scope_symbol_ids
