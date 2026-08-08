"""Tests de l'instrumentation optionnelle du Runtime (tracing.py, Phase 4).

`RecordingTracer`/`_RecordedSpan` sont des doubles de test definis localement
(meme convention que les autres modules de tests, ex. `_StubTool`/
`_ScriptedReasoner` dans test_runtime.py) : ils ne font pas partie du code de
production, `peon/tracing.py` n'expose que `Tracer`/`Span`/`NoOpTracer`.

`RecordingTracer._end_span` verifie une fermeture en ordre LIFO : un span
ferme dans le mauvais ordre casse le test avant meme l'assertion finale,
c'est le mecanisme qui prouve le "debut/fin coherents" des spans.
"""

from typing import Any, Literal

import pytest

from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.confirmation import ConfirmationResponse
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.tool_result import ToolResult
from peon.models.tool_spec import RiskLevel, ToolSpec
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.base import Tool
from peon.tracing import NoOpTracer, Span, Tracer


class _RecordedSpan:
    def __init__(self, name: str) -> None:
        self.name = name
        self.attributes: dict[str, Any] = {}
        self.exception: BaseException | None = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exc: BaseException) -> None:
        self.exception = exc


class RecordingTracer(Tracer):
    def __init__(self) -> None:
        self.spans: list[_RecordedSpan] = []
        self.events: list[tuple[str, str]] = []
        self._open: list[_RecordedSpan] = []

    def _start_span(self, name: str) -> Span:
        span = _RecordedSpan(name)
        self.spans.append(span)
        self._open.append(span)
        self.events.append(("start", name))
        return span

    def _end_span(self, span: Span) -> None:
        assert isinstance(span, _RecordedSpan)
        assert self._open and self._open[-1] is span, "spans must close in LIFO order"
        self._open.pop()
        self.events.append(("end", span.name))


class _StubTool(Tool):
    def __init__(self, name: str, risk_level: RiskLevel = RiskLevel.LOW) -> None:
        self._spec = ToolSpec(
            name=name,
            description=f"Outil {name}",
            parameters_schema={"type": "object", "properties": {}},
            risk_level=risk_level,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def execute(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(success=True, output="ok")


class _ScriptedReasoner(Reasoner):
    def __init__(self, decisions: list[Decision]) -> None:
        self._decisions = list(decisions)

    def decide(self, context: Context) -> Decision:
        return self._decisions.pop(0)


class _RaisingReasoner(Reasoner):
    def decide(self, context: Context) -> Decision:
        raise RuntimeError("boom")


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _action_decision(tool_name: str, **arguments: Any) -> ActionDecision:
    return ActionDecision(reasoning="x", tool_name=tool_name, arguments=arguments)


def _finish(outcome: Literal["success", "failure"] = "success") -> FinishDecision:
    return FinishDecision(outcome=outcome, summary="termine", confidence=1.0)


def _runtime(registry: ToolRegistry, reasoner: Reasoner, tracer: Tracer | None = None) -> tuple[Runtime, EventLog]:
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=reasoner,
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
        tracer=tracer,
    )
    return runtime, event_log


def _normalized_events(event_log: EventLog) -> list[tuple[str, dict[str, Any]]]:
    # mission_id est un UUID genere aleatoirement par Mission() a chaque run() :
    # exclu pour comparer deux runs independants (un avec tracer, un sans).
    normalized = []
    for event in event_log.list_events():
        payload = dict(event.payload)
        payload.pop("mission_id", None)
        normalized.append((event.type.value, payload))
    return normalized


# 1. Runtime sans tracer -> comportement inchange (le param `tracer` est
# optionnel ; les 328 tests preexistants qui ne le passent jamais restent la
# preuve principale, ce test documente juste le cas explicitement).
def test_runtime_behaves_normally_when_no_tracer_is_given() -> None:
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    runtime, event_log = _runtime(_registry(_StubTool("read_file")), reasoner)

    mission = runtime.run("lire un fichier")

    assert mission.status.value == "succeeded"
    assert mission.iteration_count == 2
    assert len(runtime.observations) == 1


# 2 & 3. tracer branche -> les operations attendues sont observees, avec un
# debut et une fin coherents (sequence exacte, bien parenthesee).
def test_spans_have_consistent_start_and_end_for_a_full_mission() -> None:
    tracer = RecordingTracer()
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("read_file")), reasoner, tracer)

    runtime.run("lire un fichier")

    assert tracer.events == [
        ("start", "runtime.run"),
        ("start", "reasoner.decide"),
        ("end", "reasoner.decide"),
        ("start", "executor.run"),
        ("end", "executor.run"),
        ("start", "reasoner.decide"),
        ("end", "reasoner.decide"),
        ("end", "runtime.run"),
    ]
    assert tracer._open == []


def test_executor_span_carries_the_tool_name_attribute() -> None:
    tracer = RecordingTracer()
    reasoner = _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("read_file")), reasoner, tracer)

    runtime.run("lire un fichier")

    executor_spans = [span for span in tracer.spans if span.name == "executor.run"]
    assert len(executor_spans) == 1
    assert executor_spans[0].attributes == {"tool_name": "read_file"}


def test_resume_confirmation_is_traced_as_its_own_span() -> None:
    tracer = RecordingTracer()
    reasoner = _ScriptedReasoner([_action_decision("git_push"), _finish("success")])
    runtime, _ = _runtime(_registry(_StubTool("git_push", RiskLevel.HIGH)), reasoner, tracer)

    mission = runtime.run("publier")
    tracer.events.clear()
    tracer.spans.clear()

    request = runtime.pending_confirmation
    assert request is not None
    runtime.resume_confirmation(mission, ConfirmationResponse(request_id=request.id, granted=True))

    assert tracer.events == [
        ("start", "runtime.resume_confirmation"),
        ("start", "executor.run"),
        ("end", "executor.run"),
        ("start", "reasoner.decide"),
        ("end", "reasoner.decide"),
        ("end", "runtime.resume_confirmation"),
    ]


# 4. une exception n'empeche pas la fermeture du span (elle se propage
# quand meme, sans laisser de span ouvert).
def test_exception_in_reasoner_still_closes_the_span_and_propagates() -> None:
    tracer = RecordingTracer()
    runtime, _ = _runtime(_registry(), _RaisingReasoner(), tracer)

    with pytest.raises(RuntimeError):
        runtime.run("mission qui plante")

    assert tracer.events == [
        ("start", "runtime.run"),
        ("start", "reasoner.decide"),
        ("end", "reasoner.decide"),
        ("end", "runtime.run"),
    ]
    assert tracer._open == []
    reasoner_span = next(span for span in tracer.spans if span.name == "reasoner.decide")
    assert isinstance(reasoner_span.exception, RuntimeError)


# 5. EventLog strictement identique avec ou sans tracer.
def test_event_log_is_identical_with_or_without_a_tracer() -> None:
    plain_runtime, plain_log = _runtime(
        _registry(_StubTool("read_file")), _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    )
    traced_runtime, traced_log = _runtime(
        _registry(_StubTool("read_file")),
        _ScriptedReasoner([_action_decision("read_file"), _finish("success")]),
        RecordingTracer(),
    )

    plain_runtime.run("lire un fichier")
    traced_runtime.run("lire un fichier")

    assert _normalized_events(plain_log) == _normalized_events(traced_log)


# 6. aucune donnee de tracing ne modifie les Observation ou les Event.
def test_observations_are_identical_with_or_without_a_tracer() -> None:
    plain_runtime, _ = _runtime(
        _registry(_StubTool("read_file")), _ScriptedReasoner([_action_decision("read_file"), _finish("success")])
    )
    traced_runtime, _ = _runtime(
        _registry(_StubTool("read_file")),
        _ScriptedReasoner([_action_decision("read_file"), _finish("success")]),
        RecordingTracer(),
    )

    plain_runtime.run("lire un fichier")
    traced_runtime.run("lire un fichier")

    assert [(o.kind, o.summary, o.details) for o in plain_runtime.observations] == [
        (o.kind, o.summary, o.details) for o in traced_runtime.observations
    ]


# Contrat Tracer/NoOpTracer lui-meme, independamment du Runtime.
def test_noop_tracer_yields_a_usable_span_without_error() -> None:
    tracer = NoOpTracer()
    with tracer.start_span("anything", key="value") as span:
        span.set_attribute("more", 1)


def test_noop_tracer_still_propagates_exceptions() -> None:
    tracer = NoOpTracer()
    with pytest.raises(ValueError):
        with tracer.start_span("op"):
            raise ValueError("boom")
