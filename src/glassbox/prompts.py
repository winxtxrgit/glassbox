"""The instruction that turns a chat model into an auditable classifier.

The prompt pins the model to a fixed JSON shape and — crucially — demands that
every evidence string be copied **verbatim** from the document. That demand is
what makes the output checkable: :mod:`glassbox.grounding` can then confirm each
quote exists, and a model that paraphrases instead of quoting convicts itself.
"""

from __future__ import annotations

from dataclasses import replace

from .schema import Policy

# The instruction styles build_prompts understands. "plain" states the rules;
# "few-shot" also shows worked verbatim-quoting examples.
PROMPT_STYLES = ("plain", "few-shot")

# A security-triage default that fits a log/email/document pipeline. Swap it for
# any Policy with your own question and labels.
DEFAULT_POLICY = Policy(
    name="security-triage",
    question=(
        "Does this document show signs of a security threat — phishing, malware, "
        "credential theft, intrusion, or data exfiltration?"
    ),
    labels=("suspicious", "benign"),
    default_label="benign",
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

# Shown only under prompt_style="few-shot". Larger, more fluent models tend to
# reword their "evidence" into a nicer sentence instead of copying it, which
# breaks grounding (the quote can no longer be found in the source). Two worked
# examples that copy evidence character-for-character pull them back toward
# literal quoting. The examples assume the default suspicious/benign framing.
FEWSHOT_BLOCK = """\
Here are two examples of the exact output format. Note that every "evidence"
string is copied word-for-word from its DOCUMENT -- it is not reworded.

EXAMPLE 1
DOCUMENT:
Your account is locked. Confirm your password at http://login.example to restore access.
OUTPUT:
{"label": "suspicious", "confidence": 0.9, "rationale": "Asks the user to confirm a password at an external link -- classic credential phishing.", "evidence": ["Confirm your password at http://login.example"]}

EXAMPLE 2
DOCUMENT:
Hi team, the budget review is moved to 2pm on Friday. Agenda attached.
OUTPUT:
{"label": "benign", "confidence": 0.85, "rationale": "A routine internal scheduling note with no threat signals.", "evidence": ["the budget review is moved to 2pm on Friday"]}

Now do the same for the real DOCUMENT below."""


def build_prompts(document: str, policy: Policy = DEFAULT_POLICY) -> tuple[str, str]:
    """Return the ``(system, user)`` prompt pair for triaging *document* under *policy*.

    When ``policy.prompt_style == "few-shot"`` the system prompt also carries two
    worked examples demonstrating verbatim evidence quoting.
    """
    if policy.prompt_style == "few-shot" and set(policy.labels) != set(DEFAULT_POLICY.labels):
        # The worked examples answer "suspicious"/"benign"; shown under other
        # labels they would teach the model a label it is not allowed to use.
        raise ValueError("the few-shot prompt only fits the default suspicious/benign labels")
    labels = ", ".join(policy.labels)
    system = SYSTEM_TEMPLATE.format(labels=labels)
    if policy.prompt_style == "few-shot":
        system = f"{system}\n\n{FEWSHOT_BLOCK}"
    user = f"QUESTION: {policy.question}\n\nDOCUMENT:\n{document}"
    return system, user


def make_policy(
    labels: list[str] | tuple[str, ...] | str | None = None,
    question: str | None = None,
    default_label: str | None = None,
    prompt_style: str = "plain",
) -> Policy:
    """Build a :class:`Policy` for your own task, validating it.

    *labels* may be a sequence or a comma-separated string (``"spam,ham"``). With
    no *labels* and no *question* this is :data:`DEFAULT_POLICY`; giving only a
    *question* keeps the default labels. Custom labels need a question, since
    the default one is about security threats. Raises :class:`ValueError` with a
    message fit for the command line.
    """
    if prompt_style not in PROMPT_STYLES:
        raise ValueError(f"unknown prompt style {prompt_style!r}; choose from {PROMPT_STYLES}")

    if labels is None:
        if default_label is not None and default_label != DEFAULT_POLICY.default_label:
            raise ValueError("--default-label needs --labels (the default labels already set one)")
        policy = DEFAULT_POLICY
        if question is not None:
            if not question.strip():
                raise ValueError("--question must not be empty")
            policy = replace(policy, name="custom", question=question.strip())
        return replace(policy, prompt_style=prompt_style)

    raw = labels.split(",") if isinstance(labels, str) else list(labels)
    cleaned = [label.strip() for label in raw]
    if any(not label for label in cleaned):
        raise ValueError("--labels has an empty label (check for a stray comma)")
    if len(cleaned) < 2:
        raise ValueError("--labels needs at least two labels, e.g. --labels spam,ham")
    lowered = [label.lower() for label in cleaned]
    if len(set(lowered)) != len(lowered):
        # Replies are matched to labels case-insensitively, so these would collide.
        raise ValueError("--labels must be distinct (ignoring case)")
    if question is None or not question.strip():
        raise ValueError(
            "--labels needs --question too: the default question is about security threats"
        )
    if default_label is not None and default_label.strip().lower() not in lowered:
        raise ValueError(f"--default-label {default_label!r} is not one of the labels {cleaned}")
    default = (
        cleaned[lowered.index(default_label.strip().lower())] if default_label is not None else None
    )
    policy = Policy(
        name="custom",
        question=question.strip(),
        labels=tuple(cleaned),
        prompt_style=prompt_style,
        default_label=default,
    )
    build_prompts("", policy)  # surfaces an incompatible prompt style (few-shot) now
    return policy
