"""Explainable LLM triage that cites its evidence, and checks its own honesty.

glassbox runs a document past an open-weights model (Qwen on Ollama by default)
and returns a verdict whose every claim is pinned to a verbatim span of the
source. A rationale that quotes text the document never contained is not an
explanation — it's a tell. glassbox finds those tells.
"""

__version__ = "0.7.0"
