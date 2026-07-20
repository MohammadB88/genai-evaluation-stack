"""Build clients for the OpenAI-compatible model and judge endpoints.

The application model is called through the plain OpenAI SDK; the judge is
wrapped in DeepEval's LocalModel so every DeepEval metric can use it.
"""

from __future__ import annotations

from openai import OpenAI

from .config import RunnerConfig
from .datasets import GoldenItem


def build_model_client(cfg: RunnerConfig) -> OpenAI:
    return OpenAI(base_url=cfg.model_endpoint, api_key=cfg.api_key)


def generate_answer(client: OpenAI, model_name: str, item: GoldenItem) -> str:
    """Run one golden prompt through the application model.

    For RAG items the retrieved contexts are prepended the same way a simple
    RAG app would — this keeps the slice self-contained until a real app
    pipeline is plugged in via predict_fn.
    """
    user_content = item.prompt
    if item.contexts:
        ctx = "\n\n".join(item.contexts)
        user_content = (
            f"Beantworte die Frage ausschließlich auf Basis des folgenden Kontexts.\n\n"
            f"Kontext:\n{ctx}\n\nFrage: {item.prompt}"
        )
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": user_content}],
        max_tokens=item.max_tokens,
        temperature=item.temperature,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"Empty completion for item {item.id}")
    return content


def build_judge(cfg: RunnerConfig):
    """DeepEval judge over the OpenAI-compatible judge endpoint.

    temperature=0 is a pinned-judge requirement (see CLAUDE.md), not a tunable.
    """
    from deepeval.models import LocalModel

    return LocalModel(
        model=cfg.judge_model,
        base_url=cfg.judge_endpoint,
        api_key=cfg.api_key,
        temperature=0,
    )
