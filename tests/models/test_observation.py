import pytest
from pydantic import ValidationError

from peon.models.observation import Observation, ObservationKind


def test_defaults_are_sensible() -> None:
    observation = Observation(kind=ObservationKind.EXECUTION_RESULT, summary="fichier lu avec succes")

    assert observation.details is None


def test_details_carries_arbitrary_structured_data() -> None:
    observation = Observation(
        kind=ObservationKind.EXECUTION_ERROR,
        summary="lecture du fichier en echec",
        details={"tool_name": "read_file", "return_code": 1},
    )

    assert observation.details == {"tool_name": "read_file", "return_code": 1}


@pytest.mark.parametrize("summary", ["", "   "])
def test_blank_summary_is_rejected(summary: str) -> None:
    with pytest.raises(ValidationError):
        Observation(kind=ObservationKind.SYSTEM_INFO, summary=summary)


def test_summary_is_stripped() -> None:
    observation = Observation(kind=ObservationKind.SYSTEM_INFO, summary="  limite d'iterations bientot atteinte  ")

    assert observation.summary == "limite d'iterations bientot atteinte"


def test_invalid_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Observation(kind="not_a_real_kind", summary="x")


def test_observation_is_frozen() -> None:
    observation = Observation(kind=ObservationKind.POLICY_REJECTION, summary="commande refusee")

    with pytest.raises(ValidationError):
        observation.summary = "autre chose"  # type: ignore[misc]


@pytest.mark.parametrize(
    "kind",
    [
        ObservationKind.EXECUTION_RESULT,
        ObservationKind.EXECUTION_ERROR,
        ObservationKind.POLICY_REJECTION,
        ObservationKind.CONFIRMATION_DENIED,
        ObservationKind.SYSTEM_INFO,
    ],
)
def test_every_kind_accepts_a_minimal_observation(kind: ObservationKind) -> None:
    observation = Observation(kind=kind, summary="fait quelconque")

    assert observation.kind is kind
