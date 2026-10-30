"""The instruction that turns a chat model into an auditable classifier.

The prompt pins the model to a fixed JSON shape and — crucially — demands that
every evidence string be copied **verbatim** from the document. That demand is
what makes the output checkable: :mod:`glassbox.grounding` can then confirm each
quote exists, and a model that paraphrases instead of quoting convicts itself.
"""

from __future__ import annotations

from .schema import Policy

# A security-triage default that fits a log/email/document pipeline. Swap it for
# any Policy with your own question and labels.
DEFAULT_POLICY = Policy(
    name="security-triage",
    question=(
        "Does this document show signs of a security threat — phishing, malware, "
        "credential theft, intrusion, or data exfiltration?"
    ),
    labels=("suspicious", "benign"),
)

SYSTEM_TEMPLATE = """\
You are a careful triage classifier. You will be given a DOCUMENT and a QUESTION.

Decide the single best label from this exact set: {labels}.

Reply with ONLY a JSON object, no prose around it, in this shape:
{{
  "label": one of {labels},
  "confidence": a number from 0 to 1,
  "rationale": a short plain-language explanation of your decision,
  "evidence": a list of short strings, each copied CHARACTER-FOR-CHARACTER from
              the DOCUMENT, that justify the label
}}

Rules:
- Every string in "evidence" MUST appear verbatim in the DOCUMENT. Do not
  paraphrase, summarize, translate, or fix typos in a quote.
- If nothing in the document supports a label, return an empty "evidence" list
  and lower your confidence accordingly.
- Quote the shortest spans that carry the signal, not whole paragraphs."""


def build_prompts(document: str, policy: Policy = DEFAULT_POLICY) -> tuple[str, str]:
    """Return the ``(system, user)`` prompt pair for triaging *document* under *policy*."""
    labels = ", ".join(policy.labels)
    system = SYSTEM_TEMPLATE.format(labels=labels)
    user = f"QUESTION: {policy.question}\n\nDOCUMENT:\n{document}"
    return system, user
