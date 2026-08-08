import json
from http.server import HTTPServer
from pathlib import Path

import pytest

from peon.composition import build_runtime
from peon.llm import LLM
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.providers.ollama import OllamaConfig, OllamaLLM
from peon.runtime import StorageNotConfiguredError
from peon.storage import InMemoryStorage
from peon.tools.filesystem import ReadFileTool
from peon.workspace import LocalWorkspace

from conftest import FakeOllamaHandler


class _ScriptedLLM(LLM):
    # Reponses JSON brutes fixees a l'avance, jamais devinees depuis les
    # messages recus : reproduit un fournisseur LLM deterministe sans
    # dependre d'un provider concret precis - build_runtime ne doit se
    # soucier que du contrat LLM (llm.py), pas de son implementation.
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def generate(self, messages: list[dict[str, str]]) -> str:
        return self._responses.pop(0)


def _finish_response(summary: str) -> str:
    return json.dumps({"kind": "finished", "outcome": "success", "summary": summary, "confidence": 1.0})


def _read_then_finish_responses(path: str) -> list[str]:
    return [
        json.dumps(
            {"kind": "action", "reasoning": "lire le fichier", "tool_name": "read_file", "arguments": {"path": path}}
        ),
        _finish_response("fichier lu"),
    ]


def test_build_runtime_wires_a_working_runtime_with_any_llm(tmp_path: Path) -> None:
    target = tmp_path / "config.txt"
    target.write_text("contenu reel du fichier", encoding="utf-8")

    llm = _ScriptedLLM(_read_then_finish_responses(str(target)))
    runtime = build_runtime(llm=llm, tools=[ReadFileTool(LocalWorkspace())])

    mission = runtime.run(f"lire le contenu de {target.name}")

    assert mission.status is MissionStatus.SUCCEEDED
    assert len(runtime.observations) == 1
    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details is not None
    assert observation.details["output"] == "contenu reel du fichier"


def test_build_runtime_works_with_ollama_llm_without_real_network(fake_ollama_server: HTTPServer) -> None:
    FakeOllamaHandler.response_body = json.dumps(
        {"message": {"role": "assistant", "content": _finish_response("rien a lire")}}
    ).encode("utf-8")

    host, port = fake_ollama_server.server_address
    llm = OllamaLLM(OllamaConfig(model="llama3", base_url=f"http://{host}:{port}"))
    runtime = build_runtime(llm=llm, tools=[ReadFileTool(LocalWorkspace())])

    mission = runtime.run("verifier la disponibilite du provider Ollama")

    assert mission.status is MissionStatus.SUCCEEDED
    assert runtime.observations == []


def test_build_runtime_forwards_storage_when_given() -> None:
    llm = _ScriptedLLM([_finish_response("rien a faire")])
    storage = InMemoryStorage()

    runtime = build_runtime(llm=llm, tools=[], storage=storage)
    runtime.run("ne rien faire")
    runtime.persist_events()

    saved_events = storage.load_events()
    assert saved_events != []
    assert saved_events[0].type is EventType.MISSION_CREATED


def test_build_runtime_without_storage_raises_when_persisting() -> None:
    llm = _ScriptedLLM([_finish_response("rien a faire")])

    runtime = build_runtime(llm=llm, tools=[])
    runtime.run("ne rien faire")

    with pytest.raises(StorageNotConfiguredError):
        runtime.persist_events()
