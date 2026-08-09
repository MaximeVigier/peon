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
from peon.tracing import Span, Tracer
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


# --- workspace_root (PathRestrictionRule reellement branchee) ----------------


def test_build_runtime_without_workspace_root_does_not_restrict_paths(tmp_path: Path) -> None:
    # Regression : comportement historique inchange quand l'appelant ne
    # fournit pas de racine, meme pour un chemin situe hors de tout dossier
    # "logique" du projet.
    outside = tmp_path / "outside.txt"
    outside.write_text("peu importe ou ce fichier vit", encoding="utf-8")

    llm = _ScriptedLLM(_read_then_finish_responses(str(outside)))
    runtime = build_runtime(llm=llm, tools=[ReadFileTool(LocalWorkspace())])

    mission = runtime.run(f"lire {outside}")

    assert mission.status is MissionStatus.SUCCEEDED
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT


def test_build_runtime_with_workspace_root_allows_a_path_inside_the_root(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"
    target.write_text("bonjour", encoding="utf-8")

    llm = _ScriptedLLM(_read_then_finish_responses(str(target)))
    runtime = build_runtime(llm=llm, tools=[ReadFileTool(LocalWorkspace())], workspace_root=tmp_path)

    mission = runtime.run(f"lire {target.name}")

    assert mission.status is MissionStatus.SUCCEEDED
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_RESULT
    assert runtime.observations[0].details is not None
    assert runtime.observations[0].details["output"] == "bonjour"


def test_build_runtime_with_workspace_root_denies_a_path_outside_the_root(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    llm = _ScriptedLLM(_read_then_finish_responses(str(outside)))
    runtime = build_runtime(llm=llm, tools=[ReadFileTool(LocalWorkspace())], workspace_root=workspace_root)

    mission = runtime.run("lire un fichier hors de la racine autorisee")

    assert mission.status is MissionStatus.SUCCEEDED
    assert runtime.observations[0].kind is ObservationKind.POLICY_REJECTION


# --- RetryingReasoner (reprise apres erreur LLM transitoire) ----------------


class _TransientlyFailingLLM(LLM):
    # Echoue avec une exception generique (jamais OllamaRequestError) pour
    # les `fail_times` premiers appels, puis se comporte comme _ScriptedLLM :
    # preuve, au niveau build_runtime() (donc bout en bout), que la reprise
    # ne suppose rien d'un fournisseur concret.
    def __init__(self, fail_times: int, responses: list[str]) -> None:
        self._fail_times = fail_times
        self._responses = list(responses)
        self.call_count = 0

    def generate(self, messages: list[dict[str, str]]) -> str:
        self.call_count += 1
        if self.call_count <= self._fail_times:
            raise RuntimeError("panne transitoire generique")
        return self._responses.pop(0)


def test_build_runtime_recovers_from_a_transient_llm_failure_by_default() -> None:
    llm = _TransientlyFailingLLM(fail_times=1, responses=[_finish_response("recupere apres une panne")])
    runtime = build_runtime(llm=llm, tools=[])

    mission = runtime.run("tache sujette a une panne transitoire")

    assert mission.status is MissionStatus.SUCCEEDED
    assert llm.call_count == 2


def test_build_runtime_reasoner_max_attempts_is_configurable() -> None:
    llm = _TransientlyFailingLLM(fail_times=1, responses=[_finish_response("jamais atteint")])

    runtime = build_runtime(llm=llm, tools=[], reasoner_max_attempts=1)
    mission = runtime.run("tache sujette a une panne transitoire, sans reprise")

    assert mission.status is MissionStatus.FAILED
    assert llm.call_count == 1


class _RecordedSpan(Span):
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, object] = {}

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        pass


class _RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.spans: list[_RecordedSpan] = []

    def _start_span(self, name: str) -> Span:
        span = _RecordedSpan(name)
        self.spans.append(span)
        return span

    def _end_span(self, span: Span) -> None:
        pass


def test_build_runtime_forwards_the_same_tracer_to_runtime_and_reasoner() -> None:
    tracer = _RecordingTracer()
    llm = _ScriptedLLM([_finish_response("ok")])
    runtime = build_runtime(llm=llm, tools=[], tracer=tracer)

    runtime.run("tache tracee")

    span_names = [span.name for span in tracer.spans]
    assert "runtime.run" in span_names
    assert "reasoner.decide.attempt" in span_names
