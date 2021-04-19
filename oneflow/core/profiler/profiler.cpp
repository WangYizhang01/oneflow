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

#include "oneflow/core/profiler/profiler.h"
#ifdef OF_ENABLE_PROFILER
#include <nvtx3/nvToolsExt.h>
#include <sys/syscall.h>
#include <iostream>
#endif  // OF_ENABLE_PROFILER

namespace oneflow {

namespace profiler {

namespace {

bool CaseInsensitiveStringEquals(const std::string& lhs, const std::string& rhs) {
  return lhs.size() == rhs.size()
         && std::equal(lhs.begin(), lhs.end(), rhs.begin(),
                       [](char a, char b) { return std::tolower(a) == std::tolower(b); });
}

bool StringToBool(const std::string& str) {
  return CaseInsensitiveStringEquals(str, "1") || CaseInsensitiveStringEquals(str, "true")
         || CaseInsensitiveStringEquals(str, "yes") || CaseInsensitiveStringEquals(str, "on")
         || CaseInsensitiveStringEquals(str, "y");
}

}  // namespace

void ParseBoolFlagFromEnv(const std::string& env_var, bool* flag) {
  const char* env_p = std::getenv(env_var.c_str());
  *flag = (env_p != nullptr && StringToBool(env_p));
}

void NameThisHostThread(const std::string& name) {
#ifdef OF_ENABLE_PROFILER
  nvtxNameOsThreadA(syscall(SYS_gettid), name.c_str());
#endif  // OF_ENABLE_PROFILER
}

void RangePush(const std::string& name) {
#ifdef OF_ENABLE_PROFILER
  nvtxRangePushA(name.c_str());
#endif  // OF_ENABLE_PROFILER
}

void RangePop() {
#ifdef OF_ENABLE_PROFILER
  nvtxRangePop();
#endif  // OF_ENABLE_PROFILER
}

#ifdef OF_ENABLE_PROFILER

class RangeGuardCtx {
 public:
  OF_DISALLOW_COPY_AND_MOVE(RangeGuardCtx);
  explicit RangeGuardCtx(nvtxRangeId_t range_id) : range_id_(range_id) {}
  ~RangeGuardCtx() = default;

  nvtxRangeId_t range_id() const { return range_id_; }

 private:
  nvtxRangeId_t range_id_;
};
#else
class RangeGuardCtx {};
#endif  // OF_ENABLE_PROFILER

RangeGuard::RangeGuard(const std::string& name) {
#ifdef OF_ENABLE_PROFILER
  nvtxRangeId_t range_id = nvtxRangeStartA(name.c_str());
  ctx_.reset(new RangeGuardCtx(range_id));
#endif  // OF_ENABLE_PROFILER
}

RangeGuard::~RangeGuard() {
#ifdef OF_ENABLE_PROFILER
  nvtxRangeEnd(ctx_->range_id());
#endif  // OF_ENABLE_PROFILER
}

void LogHostMemoryUsage(const std::string& name, int64_t* vm_size, int64_t* rss_size) {
#ifdef OF_ENABLE_PROFILER
  int64_t vm_pages;
  int64_t rss_pages;
  std::ifstream ifs("/proc/self/statm");
  ifs >> vm_pages >> rss_pages;
  ifs.close();
  const int64_t page_size = sysconf(_SC_PAGE_SIZE);
  const int64_t vm_size_ = vm_pages * page_size;
  const int64_t rss_size_ = rss_pages * page_size;
  if (vm_size != nullptr) { *vm_size = vm_size_; }
  if (rss_size != nullptr) { *rss_size = rss_size_; }
  LOG(INFO) << "[HostMemoryUsage]" << name << " VM " << vm_size_ << " RSS " << rss_size_;
#endif  // OF_ENABLE_PROFILER
}

void LogHostMemoryUsage(const std::string& name) { LogHostMemoryUsage(name, nullptr, nullptr); }

HostMemoryGuard::HostMemoryGuard(const std::string& name) {
  this->name_ = name;
  LogHostMemoryUsage("[START][" + name + "]", &start_vm_size_, &start_rss_size_);
}

HostMemoryGuard::~HostMemoryGuard() {
  int64_t end_vm_size_ = 0;
  int64_t end_rss_size_ = 0;
  LogHostMemoryUsage("[END][" + name_ + "]", &end_vm_size_, &end_rss_size_);
  LOG(INFO) << "[HostMemoryUsage][" + name_ + "][DIFF][VM]" << (end_vm_size_ - start_vm_size_);
  LOG(INFO) << "[HostMemoryUsage][" + name_ + "][DIFF][RSS]" << (end_rss_size_ - start_rss_size_);
}

}  // namespace profiler

}  // namespace oneflow
