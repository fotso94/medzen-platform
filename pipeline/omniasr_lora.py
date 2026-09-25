"""LoRA for fairseq2 omniASR models (design record T1).

fairseq2 0.6 ships no LoRA module and HF PEFT does not dispatch on
fairseq2's own Linear class, so this wrapper implements the standard
low-rank adaptation directly:

    forward(x) = wrapped(x) + dropout(x) @ A^T @ B^T * (alpha / rank)

with A ~ N(0, 1/rank) and B = 0, so a freshly wrapped model is
FUNCTIONALLY IDENTICAL to the frozen base — the equivalence tests assert
byte-equal outputs at initialization, and `merge_lora` folds B@A back
into the wrapped weight so the exported checkpoint serves on the exact
pipeline the evaluation suite live-proved (no adapter code at serving).

Works on any module exposing `weight: Tensor[out, in]` with a plain
linear forward (torch.nn.Linear and fairseq2.nn.Linear both qualify).
"""

from __future__ import annotations

from typing import Any, Iterable

import torch
from torch import nn

DEFAULT_TARGET_SUFFIXES = ("self_attn.q_proj", "self_attn.v_proj")


class LoRAWrapRefusal(RuntimeError):
    pass


class LoRALinear(nn.Module):
    def __init__(self, wrapped: nn.Module, rank: int, alpha: float, dropout: float = 0.0,
                 trainable_dtype: torch.dtype | None = None):
        super().__init__()
        weight = getattr(wrapped, "weight", None)
        if not isinstance(weight, torch.Tensor) or weight.dim() != 2:
            raise LoRAWrapRefusal(f"{type(wrapped).__name__} has no 2-D weight to adapt")
        if rank < 1 or rank > min(weight.shape):
            raise LoRAWrapRefusal(f"rank {rank} is outside 1..{min(weight.shape)}")
        self.wrapped = wrapped
        out_features, in_features = weight.shape
        self.rank = rank
        self.scaling = alpha / rank
        self.lora_dropout = nn.Dropout(dropout) if dropout > 0 else nn.Identity()
        # trainable_dtype=None keeps the adapters in the wrapped weight's dtype
        # (bf16 on the omniASR trainer). float32 keeps a full-precision master
        # copy: a bf16 parameter cannot absorb an Adam step smaller than half
        # its own spacing, which froze most of the probe's A matrix
        # (MEDZEN-BF16-UPDATE-LOSS-2026-001).
        factory = {"device": weight.device, "dtype": trainable_dtype or weight.dtype}
        self.lora_a = nn.Parameter(torch.empty(rank, in_features, **factory))
        self.lora_b = nn.Parameter(torch.zeros(out_features, rank, **factory))
        nn.init.normal_(self.lora_a, std=1.0 / rank)
        for parameter in self.wrapped.parameters():
            parameter.requires_grad_(False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        base = self.wrapped(x)
        lora_a, lora_b = self.lora_a, self.lora_b
        if lora_a.dtype != x.dtype:
            # full-precision master adapters compute in the activation dtype;
            # gradients flow back through the cast to the float32 copy
            lora_a, lora_b = lora_a.to(x.dtype), lora_b.to(x.dtype)
        update = self.lora_dropout(x) @ lora_a.transpose(0, 1) @ lora_b.transpose(0, 1)
        return base + update * self.scaling

    @torch.no_grad()
    def merged_weight(self) -> torch.Tensor:
        # float32 adapters promote the sum to float32, so the merge rounds to
        # the wrapped weight's dtype exactly once (in merge_lora's copy_)
        return self.wrapped.weight + (self.lora_b @ self.lora_a) * self.scaling


def _iter_targets(model: nn.Module, target_suffixes: tuple[str, ...], scope_prefix: str):
    for name, module in model.named_modules():
        if scope_prefix and not name.startswith(scope_prefix):
            continue
        if any(name.endswith(suffix) for suffix in target_suffixes):
            yield name, module


def wrap_lora(
    model: nn.Module,
    *,
    rank: int = 32,
    alpha: float = 64.0,
    dropout: float = 0.0,
    target_suffixes: tuple[str, ...] = DEFAULT_TARGET_SUFFIXES,
    scope_prefix: str = "llama_decoder.",
    trainable_dtype: torch.dtype | None = None,
) -> dict[str, Any]:
    """Freeze the model, wrap every matching projection, return an audit."""
    targets = list(_iter_targets(model, target_suffixes, scope_prefix))
    if not targets:
        raise LoRAWrapRefusal(
            f"no modules match suffixes {target_suffixes} under '{scope_prefix}'"
        )
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    wrapped_names = []
    for name, module in targets:
        if isinstance(module, LoRALinear):
            raise LoRAWrapRefusal(f"{name} is already wrapped")
        parent_name, _, attribute = name.rpartition(".")
        parent = model.get_submodule(parent_name) if parent_name else model
        setattr(parent, attribute, LoRALinear(module, rank, alpha, dropout,
                                              trainable_dtype=trainable_dtype))
        wrapped_names.append(name)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    audit = {
        "status": "PASS_LORA_WRAP",
        "wrapped_modules": wrapped_names,
        "rank": rank,
        "alpha": alpha,
        "trainable_parameters": trainable,
        "total_parameters": total,
        "trainable_fraction": trainable / total,
    }
    if trainable_dtype is not None:
        audit["trainable_dtypes"] = sorted({str(p.dtype).removeprefix("torch.")
                                            for p in model.parameters() if p.requires_grad})
    return audit


@torch.no_grad()
def merge_lora(model: nn.Module) -> dict[str, Any]:
    """Fold every adapter into its wrapped weight and restore the original
    modules, so the exported model contains no adapter code at all."""
    merged = []
    for name, module in list(model.named_modules()):
        if not isinstance(module, LoRALinear):
            continue
        module.wrapped.weight.copy_(module.merged_weight())
        parent_name, _, attribute = name.rpartition(".")
        parent = model.get_submodule(parent_name) if parent_name else model
        setattr(parent, attribute, module.wrapped)
        merged.append(name)
    if not merged:
        raise LoRAWrapRefusal("no LoRA modules present to merge")
    return {"status": "PASS_LORA_MERGE", "merged_modules": merged}


def lora_state_dict(model: nn.Module) -> dict[str, torch.Tensor]:
    return {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if ".lora_a" in name or ".lora_b" in name
    }
