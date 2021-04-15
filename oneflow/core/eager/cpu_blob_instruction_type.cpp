/*
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
*/
#include "oneflow/core/eager/blob_instruction_type.h"
#include "oneflow/core/vm/cpu_stream_type.h"
#include "oneflow/core/vm/copy_blob_to_other_device_phy_instr_operand.h"
#include "oneflow/core/vm/stream.msg.h"
#include "oneflow/core/eager/eager_blob_object.h"
#include "oneflow/core/kernel/kernel_util.h"

namespace oneflow {
namespace eager {
class CpuLazyReferenceInstructionType : public LazyReferenceInstructionType {
 public:
  CpuLazyReferenceInstructionType() = default;
  ~CpuLazyReferenceInstructionType() override = default;

  using stream_type = vm::CpuStreamType;
};

COMMAND(vm::RegisterInstructionType<CpuLazyReferenceInstructionType>("cpu.LazyReference"));

class CpuAccessBlobByCallbackInstructionType final : public AccessBlobByCallbackInstructionType {
 public:
  CpuAccessBlobByCallbackInstructionType() = default;
  ~CpuAccessBlobByCallbackInstructionType() override = default;

  using stream_type = vm::CpuStreamType;
};
COMMAND(vm::RegisterInstructionType<CpuAccessBlobByCallbackInstructionType>(
    "cpu.AccessBlobByCallback"));

class CpuCopyBlobToCpuInstructionType final : public vm::InstructionType {
 public:
  CpuCopyBlobToCpuInstructionType() = default;
  ~CpuCopyBlobToCpuInstructionType() override = default;

  using stream_type = vm::CpuStreamType;

  void Compute(vm::Instruction* instruction) const override {
    const vm::InstructionMsg& instr_msg = instruction->instr_msg();
    const auto& phy_instr_operand = instr_msg.phy_instr_operand();
    CHECK(static_cast<bool>(phy_instr_operand));
    const auto* ptr =
        dynamic_cast<const vm::CopyBlobToOtherDevicePhyInstrOperand*>(phy_instr_operand.get());
    CHECK_NOTNULL(ptr);
    DeviceCtx* device_ctx = instruction->stream().device_ctx().get();
    Blob* src_blob = ptr->src_eager_blob_object()->mut_blob();
    Blob* dst_blob = ptr->dst_eager_blob_object()->mut_blob();
    SyncAutoMemcpy(device_ctx, dst_blob->mut_dptr(), src_blob->dptr(),
                   src_blob->ByteSizeOfBlobBody(), src_blob->mem_case(), dst_blob->mem_case());
  }

  void Infer(vm::Instruction* instruction) const override {
    // do nothing
  }
};
COMMAND(
    vm::RegisterInstructionType<CpuCopyBlobToCpuInstructionType>("cpu.cpu.CopyBlobToOtherDevice"));

}  // namespace eager
}  // namespace oneflow
