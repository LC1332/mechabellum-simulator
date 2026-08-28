# Distributed / GPU allowlist plumbing (任务书 §9).
#
# The user-frozen resource boundary: this task may ONLY use physical GPUs
# 1..7; physical GPU 0 is reserved for debugging and other tasks. Every
# launcher resolves CUDA_VISIBLE_DEVICES through assert_visible_against_
# allowlist() and records the logical-rank -> physical-GPU/UUID audit in the
# run manifest (§9.1). All checks here are pure string/env logic so they are
# unit-testable without CUDA.
from __future__ import annotations

import os

from .token_contract import TRAINING_GPU_ALLOWLIST, RESERVED_PHYSICAL_GPUS

DEFAULT_ENV = "CUDA_VISIBLE_DEVICES"


class GPUAllowlistError(ValueError):
    pass


def parse_visible_devices(env_value: str | None) -> list[int]:
    """CUDA_VISIBLE_DEVICES -> physical gpu ids. Empty/unset = all physical
    GPUs visible (represented by None, never an empty list)."""
    if env_value is None or str(env_value).strip() == "":
        return None
    out = []
    for part in str(env_value).split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            out.append(int(part))
        except ValueError:
            # MIG/UUID syntax cannot be validated against the numeric
            # allowlist — refuse it
            raise GPUAllowlistError(
                "CUDA_VISIBLE_DEVICES 含非数字项 %r (本任务要求显式物理编号)"
                % part)
    return out


def assert_visible_against_allowlist(env_value: str | None,
                                     allowlist=None,
                                     reserved=None) -> list[int]:
    """§1.2/§9: the visible set must sit INSIDE the training allowlist and
    must NEVER touch a reserved GPU. Returns the physical ids."""
    allowlist = list(TRAINING_GPU_ALLOWLIST if allowlist is None
                     else allowlist)
    reserved = list(RESERVED_PHYSICAL_GPUS if reserved is None else reserved)
    visible = parse_visible_devices(env_value)
    if visible is None:
        raise GPUAllowlistError(
            "CUDA_VISIBLE_DEVICES 未设置: 会把全部物理 GPU(含保留的 %s)暴露给"
            "本任务 — 显式设置为 %s" % (reserved, ",".join(map(str, allowlist))))
    for g in visible:
        if g in reserved:
            raise GPUAllowlistError(
                "物理 GPU %d 是保留卡,本任务禁止占用" % g)
        if g not in allowlist:
            raise GPUAllowlistError(
                "物理 GPU %d 不在本任务 allowlist %s 内" % (g, allowlist))
    return visible


def suggested_env(world_size: int) -> str:
    return ",".join(map(str, TRAINING_GPU_ALLOWLIST[:world_size]))


def enforce_env(world_size: int | None = None) -> list[int]:
    """Set + verify CUDA_VISIBLE_DEVICES for the CURRENT process (launchers
    call this before torch initializes CUDA).

    A CPU-only run (no CUDA requested) may skip the allowlist — pass
    require=False when the process will never touch torch.cuda."""
    env_value = os.environ.get(DEFAULT_ENV) or None   # "" == unset
    if env_value is None and os.environ.get("TRANSFORMER_ALLOW_CPU") == "1":
        return []
    physical = assert_visible_against_allowlist(env_value)
    if world_size is not None and len(physical) != world_size:
        raise GPUAllowlistError(
            "CUDA_VISIBLE_DEVICES=%s 提供了 %d 张卡, 与 world_size=%d 不符"
            % (env_value, len(physical), world_size))
    os.environ[DEFAULT_ENV] = ",".join(map(str, physical))
    return physical


def audit_devices() -> list[dict]:
    """logical index -> physical GPU audit for the run manifest (§9.1).
    Requires CUDA; callers on CPU-only shells get an empty list."""
    try:
        import torch
        if not torch.cuda.is_available():
            return []
    except ImportError:
        return []
    rows = []
    env_value = os.environ.get(DEFAULT_ENV, "")
    visible = parse_visible_devices(env_value) or []
    for logical in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(logical)
        physical = visible[logical] if logical < len(visible) else None
        rows.append({
            "logical": logical, "physical": physical,
            "uuid": getattr(props, "uuid", None) or str(getattr(
                props, "uuid", "")) or None,
            "name": props.name,
            "total_mem_gb": round(props.total_memory / 2 ** 30, 1),
        })
    for row in rows:
        if row["physical"] is None:
            continue
        if row["physical"] in RESERVED_PHYSICAL_GPUS:
            raise GPUAllowlistError(
                "进程可见物理 GPU %d (保留卡) — 立即退出" % row["physical"])
        if row["physical"] not in TRAINING_GPU_ALLOWLIST:
            raise GPUAllowlistError(
                "进程可见物理 GPU %d 不在 allowlist 内" % row["physical"])
    return rows


# ---------------------------------------------------------------- DDP
def setup_distributed(backend: str | None = None) -> dict:
    """env:// init for torchrun launches; single-process runs get
    (rank 0, world 1, no process group)."""
    import torch
    import torch.distributed as dist
    rank = int(os.environ.get("RANK", "0"))
    world = int(os.environ.get("WORLD_SIZE", "1"))
    local = int(os.environ.get("LOCAL_RANK", "0"))
    info = {"rank": rank, "world_size": world, "local_rank": local,
            "distributed": world > 1, "backend": None}
    if world > 1:
        if backend is None:
            backend = "nccl" if torch.cuda.is_available() else "gloo"
        if not dist.is_initialized():
            dist.init_process_group(backend=backend)
        info["backend"] = backend
        if torch.cuda.is_available():
            torch.cuda.set_device(local)
            info["device"] = "cuda:%d" % local
        else:
            info["device"] = "cpu"
    else:
        info["device"] = "cuda" if torch.cuda.is_available() else "cpu"
    return info


def is_rank0(info: dict) -> bool:
    return info.get("rank", 0) == 0


def barrier(info: dict):
    if info.get("distributed"):
        import torch.distributed as dist
        dist.barrier()


def all_reduce_sum(t, info: dict):
    if info.get("distributed"):
        import torch.distributed as dist
        dist.all_reduce(t)
    return t


def reduce_metrics(metrics: dict, info: dict, weight: float = 1.0) -> dict:
    """Sum-weighted metric reduction across ranks (caller divides by the
    all-reduced weight)."""
    import torch
    keys = sorted(metrics)
    t = torch.as_tensor([float(metrics[k]) for k in keys] + [float(weight)],
                        dtype=torch.float64)
    all_reduce_sum(t, info)
    vals = t.tolist()
    out = {k: v for k, v in zip(keys, vals[:-1])}
    out["_weight"] = vals[-1]
    return out


def wrap_ddp(model, info: dict):
    if not info.get("distributed"):
        return model
    import torch
    from torch.nn.parallel import DistributedDataParallel as DDP
    # TValue routes each batch to ONE domain head (§8.1) and TPolicy stages
    # are verb-conditional — unused-params detection is REQUIRED for DDP
    return DDP(model,
               device_ids=[info["local_rank"]]
               if str(info.get("device", "")).startswith("cuda") else None,
               find_unused_parameters=True)


def unwrap(model):
    return getattr(model, "module", model)
