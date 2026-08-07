import pytest
from pydantic import ValidationError

from peon.models.tool_spec import RiskLevel, ToolSpec


def _read_file_spec(**overrides: object) -> ToolSpec:
    fields = {
        "name": "read_file",
        "description": "Lit le contenu d'un fichier texte.",
        "parameters_schema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "risk_level": RiskLevel.LOW,
    }
    fields.update(overrides)
    return ToolSpec(**fields)


def test_valid_tool_spec() -> None:
    spec = _read_file_spec()

    assert spec.cost_estimate is None


def test_cost_estimate_is_optional_but_bounded() -> None:
    spec = _read_file_spec(cost_estimate=0.002)

    assert spec.cost_estimate == 0.002

    with pytest.raises(ValidationError):
        _read_file_spec(cost_estimate=-0.1)


@pytest.mark.parametrize("name", ["ReadFile", "read file", "1read_file", "read-file"])
def test_invalid_names_are_rejected(name: str) -> None:
    with pytest.raises(ValidationError):
        _read_file_spec(name=name)


def test_blank_description_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _read_file_spec(description="")


def test_risk_level_is_required() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            name="read_file",
            description="x",
            parameters_schema={},
        )


def test_parameters_schema_is_required() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name="read_file", description="x", risk_level=RiskLevel.LOW)
