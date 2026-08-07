import pytest

from peon.llm import LLM


def test_llm_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        LLM()  # type: ignore[abstract]


def test_subclass_missing_generate_cannot_be_instantiated() -> None:
    class _MissingGenerate(LLM):
        pass

    with pytest.raises(TypeError):
        _MissingGenerate()  # type: ignore[abstract]


def test_conforming_subclass_can_generate() -> None:
    class _StubLLM(LLM):
        def generate(self, messages: list[dict[str, str]]) -> str:
            return "ok"

    assert _StubLLM().generate([{"role": "user", "content": "hello"}]) == "ok"
