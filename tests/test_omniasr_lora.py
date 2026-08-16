"""T1 equivalence tests: wrap-identity, gradient scoping, merge-identity.

These need torch; on hosts without it they skip with a declared reason and
run inside the runtime/trainer image (the same policy as the other
training-host suites).
"""

import importlib.util

import pytest

_needs_torch = pytest.mark.skipif(
    importlib.util.find_spec("torch") is None,
    reason="torch is a training/runtime-host dependency, absent on the engineering host",
)

if importlib.util.find_spec("torch") is not None:
    import torch
    from torch import nn

    from pipeline.omniasr_lora import (
        LoRAWrapRefusal,
        lora_state_dict,
        merge_lora,
        wrap_lora,
    )

    class Toy(nn.Module):
        """Mimics the omniASR layout: scoped decoder with q/v projections."""

        def __init__(self):
            super().__init__()
            self.llama_decoder = nn.ModuleDict({
                "layers": nn.ModuleList([
                    nn.ModuleDict({"self_attn": nn.ModuleDict({
                        "q_proj": nn.Linear(16, 16, bias=False),
                        "k_proj": nn.Linear(16, 16, bias=False),
                        "v_proj": nn.Linear(16, 16, bias=False),
                    })}) for _ in range(2)
                ])
            })
            self.encoder = nn.ModuleDict({"q_proj": nn.Linear(16, 16, bias=False)})

        def forward(self, x):
            for layer in self.llama_decoder["layers"]:
                attn = layer["self_attn"]
                x = attn["q_proj"](x) + attn["v_proj"](x) + attn["k_proj"](x)
            return x


@_needs_torch
def test_wrap_is_functionally_identical_at_init():
    torch.manual_seed(0)
    model = Toy()
    x = torch.randn(4, 16)
    before = model(x).detach().clone()
    audit = wrap_lora(model, rank=4, alpha=8.0)
    after = model(x)
    assert torch.equal(before, after), "B=0 init must leave outputs byte-identical"
    assert len(audit["wrapped_modules"]) == 4  # q+v in 2 layers; k and encoder untouched
    assert all(".self_attn.q_proj" in n or ".self_attn.v_proj" in n
               for n in audit["wrapped_modules"])


@_needs_torch
def test_only_lora_parameters_train_and_count_matches_formula():
    model = Toy()
    audit = wrap_lora(model, rank=4, alpha=8.0)
    trainable = [n for n, p in model.named_parameters() if p.requires_grad]
    assert trainable and all(".lora_a" in n or ".lora_b" in n for n in trainable)
    # rank*(in+out) per wrapped module
    assert audit["trainable_parameters"] == 4 * (4 * (16 + 16))
    assert len(lora_state_dict(model)) == 8


@_needs_torch
def test_merge_restores_plain_modules_and_preserves_outputs():
    torch.manual_seed(1)
    model = Toy()
    wrap_lora(model, rank=4, alpha=8.0)
    # push the adapters away from zero so the merge actually moves weight
    for name, parameter in model.named_parameters():
        if ".lora_b" in name:
            nn.init.normal_(parameter, std=0.05)
    x = torch.randn(4, 16)
    adapted = model(x).detach().clone()
    merge_lora(model)
    merged = model(x)
    assert torch.allclose(adapted, merged, atol=1e-5)
    from pipeline.omniasr_lora import LoRALinear
    assert not any(isinstance(m, LoRALinear) for m in model.modules())


@_needs_torch
def test_wrap_refuses_empty_scope_and_double_wrap():
    model = Toy()
    with pytest.raises(LoRAWrapRefusal, match="no modules match"):
        wrap_lora(model, scope_prefix="nonexistent.")
    wrap_lora(model, rank=2, alpha=4.0)
    with pytest.raises(LoRAWrapRefusal, match="already wrapped"):
        wrap_lora(model, rank=2, alpha=4.0)
