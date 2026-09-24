from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app import generation
from app.embeddings import EMBEDDING_MODEL
from app.generation import (
    DEFAULT_GENERATION_MODEL,
    GENERATION_REASON,
    GENERATION_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    SOURCE_VERIFICATION_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    GenerationError,
    build_prompt,
    generate_answer,
    get_generation_model,
    identify_supporting_sources,
)

QUESTION = "How many days do I have to return a product?"
CONTEXT = (
    "[Source 1]\n"
    "Document: return-policy.txt\n"
    "Chunk: 0\n\n"
    "Customers can return products within 30 days."
)
GENERATED_ANSWER = "You can return a product within 30 days."


def _completion(content: Any) -> SimpleNamespace:
    """Mimic a Groq chat completion: choices[0].message.content."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeGroqChatCompletions:
    def __init__(self, results: list[Any]):
        self.results = results
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self.results[len(self.calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result


class FakeGroqClient:
    def __init__(self, *results: Any):
        completions = FakeGroqChatCompletions(list(results))
        self.chat = SimpleNamespace(completions=completions)

    @property
    def calls(self):
        return self.chat.completions.calls


@pytest.fixture
def groq(monkeypatch):
    """Install a fake Groq client so no test ever reaches the real API."""

    def _install(*results: Any) -> FakeGroqClient:
        client = FakeGroqClient(*results)
        monkeypatch.setattr(generation, "get_groq_client", lambda: client)
        return client

    return _install


def test_generate_answer_returns_the_groq_message_content(groq):
    client = groq(_completion(f"  {GENERATED_ANSWER}\n\n"))

    answer = generate_answer(QUESTION, CONTEXT)

    assert answer == GENERATED_ANSWER
    call = client.calls[0]
    assert call["model"] == DEFAULT_GENERATION_MODEL == "openai/gpt-oss-120b"
    assert call["temperature"] == GENERATION_TEMPERATURE == 0.0
    assert call["max_completion_tokens"] == MAX_OUTPUT_TOKENS == 1024


def test_generation_sends_the_grounding_system_instruction_and_context(groq):
    client = groq(_completion(GENERATED_ANSWER))

    generate_answer(QUESTION, CONTEXT)

    call = client.calls[0]
    assert call["messages"][0] == {"role": "system", "content": SYSTEM_INSTRUCTION}
    assert call["messages"][1] == {"role": "user", "content": build_prompt(QUESTION, CONTEXT)}
    user_content = call["messages"][1]["content"]
    assert QUESTION in user_content
    assert "[Source 1]" in user_content
    assert "Document: return-policy.txt" in user_content
    assert "Customers can return products within 30 days." in user_content


def test_build_prompt_keeps_the_grounded_prompt_contract():
    assert build_prompt(QUESTION, CONTEXT) == (
        "Company knowledge context:\n\n"
        f"{CONTEXT}\n\n"
        "Answer the question using only the context above.\n"
        f"Question: {QUESTION}"
    )


def test_generate_answer_requires_a_configured_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    generation.get_groq_client.cache_clear()

    with pytest.raises(GenerationError) as exc_info:
        generate_answer(QUESTION, CONTEXT)

    assert exc_info.value.reason == GENERATION_REASON
    assert "GROQ_API_KEY" not in exc_info.value.reason


def test_groq_api_failures_return_a_safe_error(groq):
    groq(RuntimeError("503 rate_limit_exceeded for api key gsk_secret-key"))

    with pytest.raises(GenerationError) as exc_info:
        generate_answer(QUESTION, CONTEXT)

    reason = exc_info.value.reason
    assert reason == GENERATION_REASON
    for leaked in ("gsk_secret-key", "503", "rate_limit_exceeded", "RuntimeError"):
        assert leaked not in reason


@pytest.mark.parametrize(
    "result",
    [
        SimpleNamespace(choices=[]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=None))]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="   \n"))]),
        SimpleNamespace(choices=[SimpleNamespace(message=None)]),
    ],
)
def test_empty_groq_responses_return_a_safe_error(groq, result):
    client = groq(result)

    with pytest.raises(GenerationError) as exc_info:
        generate_answer(QUESTION, CONTEXT)

    assert exc_info.value.reason == GENERATION_REASON
    assert client.calls  # the model was actually called before the empty check fired


def test_generation_model_is_configurable(monkeypatch, groq):
    client = groq(_completion(GENERATED_ANSWER))
    monkeypatch.setenv("GROQ_GENERATION_MODEL", "openai/gpt-oss-20b")

    answer = generate_answer(QUESTION, CONTEXT)

    assert answer == GENERATED_ANSWER
    assert client.calls[0]["model"] == "openai/gpt-oss-20b"

    monkeypatch.setenv("GROQ_GENERATION_MODEL", "")
    assert get_generation_model() == DEFAULT_GENERATION_MODEL

    monkeypatch.delenv("GROQ_GENERATION_MODEL")
    assert get_generation_model() == DEFAULT_GENERATION_MODEL


def test_backend_env_template_documents_groq_without_a_value():
    template = (Path(__file__).parents[1] / ".env.example").read_text(encoding="utf-8")

    assert "GROQ_API_KEY=" in template
    assert re.search(r"GROQ_API_KEY=\S+", template) is None
    assert "GROQ_GENERATION_MODEL=openai/gpt-oss-120b" in template
    assert "GEMINI_API_KEY=" in template


def test_root_env_template_documents_groq_for_the_backend():
    template = (Path(__file__).parents[2] / ".env.example").read_text(encoding="utf-8")

    assert "GROQ_API_KEY=" in template
    assert re.search(r"GROQ_API_KEY=\S+", template) is None
    assert "GROQ_GENERATION_MODEL=openai/gpt-oss-120b" in template


def test_docker_compose_passes_the_groq_key_to_the_backend_container_only():
    compose = (Path(__file__).parents[2] / "docker-compose.yml").read_text(encoding="utf-8")

    frontend_section, backend_section = compose.split("  backend:", 1)
    assert "GROQ" not in frontend_section
    assert "GROQ_API_KEY: ${GROQ_API_KEY}" in backend_section
    assert "GROQ_GENERATION_MODEL: ${GROQ_GENERATION_MODEL}" in backend_section
    assert "NEXT_PUBLIC_GROQ" not in compose


def test_frontend_never_receives_the_groq_key():
    frontend = Path(__file__).parents[2] / "frontend"

    assert "GROQ" not in (frontend / ".env.example").read_text(encoding="utf-8")

    for directory in ("app", "components", "lib"):
        for path in (frontend / directory).rglob("*"):
            if path.is_file() and path.suffix in {".ts", ".tsx"}:
                assert "GROQ" not in path.read_text(encoding="utf-8")


def test_generation_module_reads_only_groq_configuration():
    source = (Path(__file__).parents[1] / "app/generation.py").read_text(encoding="utf-8")

    assert 'os.getenv("GROQ_API_KEY")' in source
    assert 'os.getenv("GROQ_GENERATION_MODEL")' in source
    assert "NEXT_PUBLIC_GROQ" not in source
    assert "google.genai" not in source
    assert "embeddings.get_gemini_client" not in source
    assert "GEMINI_GENERATION_MODEL" not in source


def test_embeddings_remain_on_gemini_after_the_generation_swap():
    source = (Path(__file__).parents[1] / "app/embeddings.py").read_text(encoding="utf-8")

    assert EMBEDDING_MODEL == "gemini-embedding-2"
    assert 'os.getenv("GEMINI_API_KEY")' in source


def test_identify_supporting_sources_returns_the_json_array_numbers(groq):
    client = groq(_completion("[1, 3]"))

    supporting = identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=3)

    assert supporting == [1, 3]
    call = client.calls[0]
    assert call["messages"][0] == {"role": "system", "content": SOURCE_VERIFICATION_INSTRUCTION}
    user_content = call["messages"][1]["content"]
    assert QUESTION in user_content
    assert GENERATED_ANSWER in user_content
    assert "[Source 1]" in user_content
    assert call["temperature"] == GENERATION_TEMPERATURE


def test_identify_supporting_sources_tolerates_markdown_fences(groq):
    groq(_completion("```json\n[2]\n```"))

    supporting = identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=2)

    assert supporting == [2]


def test_identify_supporting_sources_ignores_out_of_range_and_duplicate_numbers(groq):
    groq(_completion("[0, 2, 2, 9]"))

    supporting = identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=2)

    assert supporting == [2]


def test_identify_supporting_sources_accepts_an_empty_array(groq):
    groq(_completion("[]"))

    assert identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=2) == []


@pytest.mark.parametrize(
    "content",
    [
        "Sources: 1, 2",
        "",
        "[true]",
        '{"sources": [1]}',
    ],
)
def test_identify_supporting_sources_rejects_unusable_responses(groq, content):
    groq(_completion(content))

    with pytest.raises(GenerationError) as exc_info:
        identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=2)

    assert exc_info.value.reason == GENERATION_REASON


def test_identify_supporting_sources_reports_api_failures_safely(groq):
    groq(RuntimeError("429 rate_limit_exceeded for api key gsk_secret-key"))

    with pytest.raises(GenerationError) as exc_info:
        identify_supporting_sources(QUESTION, GENERATED_ANSWER, CONTEXT, source_count=2)

    reason = exc_info.value.reason
    assert reason == GENERATION_REASON
    for leaked in ("gsk_secret-key", "429", "rate_limit_exceeded", "RuntimeError"):
        assert leaked not in reason