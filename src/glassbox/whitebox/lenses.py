"""Logit lens and occlusion attribution over a small causal LM.

Two classic white-box views, implemented on plain Hugging Face ``transformers``
(no transformer-lens dependency):

* **Logit lens** — decode the residual stream at each layer through the model's
  own output head to watch the next-token prediction form, layer by layer.
* **Occlusion attribution** — mask each input token in turn and measure how far
  the probability of the predicted token falls. This is the mechanistic analogue
  of the black-box counterfactual in :mod:`glassbox.faithfulness`: there we erased
  a span from the document and re-ran the model; here we erase a token from the
  context and read the effect straight off the logits.

Everything that needs torch is imported lazily, so this module imports cleanly
without the optional dependencies installed — :func:`available` reports whether
they're present, and :func:`occlude` is pure Python you can test on its own.
"""

from __future__ import annotations

from dataclasses import dataclass


def available() -> bool:
    """True when the optional white-box dependencies (torch, transformers) import."""
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError:
        return False
    return True


def occlude(ids: list[int], index: int, fill: int) -> list[int]:
    """Return *ids* with position *index* replaced by the mask token *fill*.

    Keeping the sequence length fixed (rather than deleting the token) holds every
    other position in place, so the only thing that changes in the forward pass is
    the masked token — a clean one-variable occlusion.
    """
    masked = list(ids)
    masked[index] = fill
    return masked


@dataclass(frozen=True)
class LayerPrediction:
    """The logit lens's top next-token guess read off one layer's residual stream."""

    layer: int
    token: str
    prob: float


@dataclass(frozen=True)
class TokenAttribution:
    """How much masking one input token lowered the predicted token's probability."""

    index: int
    token: str
    delta: float  # baseline probability minus probability after occlusion


@dataclass(frozen=True)
class LensResult:
    model: str
    prompt_tokens: list[str]
    predicted: str
    predicted_prob: float
    layer_trace: list[LayerPrediction]
    attributions: list[TokenAttribution]


def analyze(prompt: str, model_name: str = "gpt2", top_attributions: int = 8) -> LensResult:
    """Run the logit lens and occlusion attribution for *prompt* on *model_name*.

    Returns the model's predicted next token, the top token at each layer as the
    prediction forms, and the input tokens ranked by how much masking each one
    lowers the predicted token's probability. Requires the ``whitebox`` extra;
    call :func:`available` first.
    """
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    # Refuse pickle-format (.bin) weights: loading those runs arbitrary code
    # embedded in the pickle, so a malicious or typo-squatted model name could
    # execute code. safetensors is a non-executable format; mainstream models
    # (gpt2, the Qwen/Llama families) all ship it. trust_remote_code is left at
    # its safe default of False, blocking custom modeling code as well.
    model = AutoModelForCausalLM.from_pretrained(model_name, use_safetensors=True)
    model.eval()

    ids = tokenizer(prompt, return_tensors="pt").input_ids
    mask_id = tokenizer.eos_token_id if tokenizer.eos_token_id is not None else 0

    with torch.no_grad():
        out = model(ids, output_hidden_states=True)
    final_logits = out.logits[0, -1]
    probs = torch.softmax(final_logits, dim=-1)
    target = int(torch.argmax(final_logits))
    baseline = float(probs[target])

    # Logit lens: push each layer's final-position residual through the model's
    # own final norm + unembedding, and read the top token. The last entry in
    # hidden_states has already been through ``ln_f``, so for it we use the model's
    # real output logits directly (applying ln_f twice would distort the reading).
    norm = model.transformer.ln_f
    head = model.lm_head
    layers = out.hidden_states[1:]
    n_layers = len(layers)
    layer_trace: list[LayerPrediction] = []
    with torch.no_grad():
        for layer, hidden in enumerate(layers, start=1):
            logits = final_logits if layer == n_layers else head(norm(hidden[0, -1]))
            layer_probs = torch.softmax(logits, dim=-1)
            top = int(torch.argmax(logits))
            layer_trace.append(
                LayerPrediction(
                    layer=layer,
                    token=_text(tokenizer, top),
                    prob=float(layer_probs[top]),
                )
            )

    # Occlusion attribution: mask each token, re-read P(target).
    id_list = ids[0].tolist()
    attributions: list[TokenAttribution] = []
    with torch.no_grad():
        for index in range(len(id_list)):
            masked = occlude(id_list, index, mask_id)
            masked_logits = model(torch.tensor([masked])).logits[0, -1]
            masked_prob = float(torch.softmax(masked_logits, dim=-1)[target])
            attributions.append(
                TokenAttribution(
                    index=index,
                    token=_text(tokenizer, id_list[index]),
                    delta=baseline - masked_prob,
                )
            )
    attributions.sort(key=lambda a: a.delta, reverse=True)

    return LensResult(
        model=model_name,
        prompt_tokens=[_text(tokenizer, i) for i in id_list],
        predicted=_text(tokenizer, target),
        predicted_prob=baseline,
        layer_trace=layer_trace,
        attributions=attributions[:top_attributions],
    )


def _text(tokenizer, token_id: int) -> str:
    """Decode a single token id to a readable string."""
    return tokenizer.decode([token_id])
