from __future__ import annotations

import pytest

from glassbox.whitebox import analyze, available, occlude


def test_occlude_replaces_only_the_target_position():
    assert occlude([10, 11, 12, 13], 2, 0) == [10, 11, 0, 13]


def test_occlude_does_not_mutate_the_input():
    ids = [1, 2, 3]
    occlude(ids, 0, 99)
    assert ids == [1, 2, 3]  # original list untouched


# Functional test — only runs where torch + transformers are installed. It uses a
# tiny random model so it needs no large download when the deps are present. The
# skip is scoped to this test so the pure-Python tests above always run.
@pytest.mark.skipif(not available(), reason="whitebox extra not installed")
def test_analyze_produces_a_trace_and_attribution_per_token():
    prompt = "the cat sat on the"
    result = analyze(prompt, model_name="sshleifer/tiny-gpt2", top_attributions=3)

    assert result.model == "sshleifer/tiny-gpt2"
    assert result.prompt_tokens  # tokenized to something
    assert isinstance(result.predicted, str)
    assert 0.0 <= result.predicted_prob <= 1.0
    # One logit-lens reading per transformer layer.
    assert result.layer_trace and all(0.0 <= s.prob <= 1.0 for s in result.layer_trace)
    assert [s.layer for s in result.layer_trace] == list(range(1, len(result.layer_trace) + 1))
    # The final layer's lens reading must equal the model's actual prediction
    # (no double layer-norm on the last hidden state).
    assert result.layer_trace[-1].token == result.predicted
    # Attributions are capped and sorted by descending effect.
    assert len(result.attributions) <= 3
    deltas = [a.delta for a in result.attributions]
    assert deltas == sorted(deltas, reverse=True)
