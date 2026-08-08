from peon.context_builder import ContextBuilder
from peon.event_log import EventLog
from peon.executor import Executor
from peon.models.context import Context
from peon.models.decision import ActionDecision, Decision, FinishDecision
from peon.models.events import EventType
from peon.models.mission import MissionStatus
from peon.models.observation import ObservationKind
from peon.policy import PolicyEngine
from peon.reasoner import Reasoner
from peon.runtime import Runtime
from peon.tool_registry import ToolRegistry
from peon.tools.shell import ShellTool
from peon.workspace import LocalWorkspace


class _RunCommandThenFinishReasoner(Reasoner):
    # Commande fixee a la construction, jamais devinee a partir du Context :
    # reproduit une reponse LLM deja connue et verifiee a l'avance.
    def __init__(self, command: str) -> None:
        self._command = command
        self._call_count = 0

    def decide(self, context: Context) -> Decision:
        self._call_count += 1
        if self._call_count == 1:
            return ActionDecision(
                reasoning="executer la commande demandee par la mission",
                tool_name="run_command",
                arguments={"command": self._command},
            )
        return FinishDecision(outcome="success", summary="commande executee avec succes", confidence=1.0)


def test_full_cycle_runs_a_real_command_through_every_component() -> None:
    registry = ToolRegistry()
    registry.register(ShellTool(LocalWorkspace()))
    event_log = EventLog()
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_RunCommandThenFinishReasoner("echo bonjour"),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=event_log,
    )

    mission = runtime.run("executer une commande shell")

    assert mission.status is MissionStatus.SUCCEEDED
    assert mission.iteration_count == 2

    assert len(runtime.observations) == 1
    observation = runtime.observations[0]
    assert observation.kind is ObservationKind.EXECUTION_RESULT
    assert observation.details is not None
    assert observation.details["tool_name"] == "run_command"
    assert observation.details["output"]["stdout"].strip() == "bonjour"

    assert [event.type for event in event_log.list_events()] == [
        EventType.MISSION_CREATED,
        EventType.STATE_TRANSITIONED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.POLICY_EVALUATED,
        EventType.STATE_TRANSITIONED,
        EventType.TOOL_EXECUTION_COMPLETED,
        EventType.STATE_TRANSITIONED,
        EventType.OBSERVATION_PRODUCED,
        EventType.CONTEXT_BUILT,
        EventType.DECISION_RECEIVED,
        EventType.MISSION_SUCCEEDED,
        EventType.STATE_TRANSITIONED,
    ]


def test_full_cycle_reports_a_failing_command_as_an_execution_error() -> None:
    registry = ToolRegistry()
    registry.register(ShellTool(LocalWorkspace()))
    runtime = Runtime(
        context_builder=ContextBuilder(registry),
        reasoner=_RunCommandThenFinishReasoner("exit 1"),
        policy_engine=PolicyEngine(registry),
        executor=Executor(registry),
        event_log=EventLog(),
    )

    runtime.run("executer une commande shell qui echoue")

    assert len(runtime.observations) == 1
    assert runtime.observations[0].kind is ObservationKind.EXECUTION_ERROR
