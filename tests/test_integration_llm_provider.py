import json
from http.server import HTTPServer

import pytest

from peon.models.context import Context
from peon.models.decision import ActionDecision, FinishDecision
from peon.models.mission import MissionStatus
from peon.prompts import PromptBuilder
from peon.providers.ollama import OllamaConfig, OllamaLLM
from peon.reasoner import InvalidLLMResponseError, LLMReasoner

from conftest import FakeOllamaHandler


def _context() -> Context:
    return Context(
        mission_goal="lire le contenu de a.txt",
        mission_status=MissionStatus.REASONING,
        mission_iteration_count=0,
    )


def _reasoner_for(server: HTTPServer) -> LLMReasoner:
    host, port = server.server_address
    config = OllamaConfig(model="llama3", base_url=f"http://{host}:{port}")
    return LLMReasoner(llm=OllamaLLM(config), prompt_builder=PromptBuilder())


def test_ollama_llm_round_trip_produces_an_action_decision(fake_ollama_server: HTTPServer) -> None:
    decision_json = json.dumps(
        {"kind": "action", "reasoning": "lire le fichier demande", "tool_name": "read_file", "arguments": {"path": "a.txt"}}
    )
    FakeOllamaHandler.response_body = json.dumps({"message": {"role": "assistant", "content": decision_json}}).encode(
        "utf-8"
    )

    decision = _reasoner_for(fake_ollama_server).decide(_context())

    assert isinstance(decision, ActionDecision)
    assert decision.tool_name == "read_file"
    assert decision.arguments == {"path": "a.txt"}


def test_ollama_llm_round_trip_produces_a_finish_decision(fake_ollama_server: HTTPServer) -> None:
    decision_json = json.dumps({"kind": "finished", "outcome": "success", "summary": "fichier lu", "confidence": 1.0})
    FakeOllamaHandler.response_body = json.dumps({"message": {"role": "assistant", "content": decision_json}}).encode(
        "utf-8"
    )

    decision = _reasoner_for(fake_ollama_server).decide(_context())

    assert isinstance(decision, FinishDecision)
    assert decision.outcome == "success"


def test_ollama_llm_round_trip_propagates_invalid_decision_as_reasoner_error(fake_ollama_server: HTTPServer) -> None:
    FakeOllamaHandler.response_body = json.dumps({"message": {"role": "assistant", "content": "pas du JSON valide"}}).encode(
        "utf-8"
    )

    with pytest.raises(InvalidLLMResponseError):
        _reasoner_for(fake_ollama_server).decide(_context())
