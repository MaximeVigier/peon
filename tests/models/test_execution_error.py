import pytest
from pydantic import ValidationError

from peon.models.execution_error import ErrorCategory, ExecutionError


def test_invalid_request_does_not_require_a_tool_name() -> None:
    error = ExecutionError(category=ErrorCategory.INVALID_REQUEST, message="outil inconnu: does_not_exist")

    assert error.tool_name is None
    assert error.is_expected is True


def test_tool_failure_requires_a_tool_name() -> None:
    error = ExecutionError(
        category=ErrorCategory.TOOL_FAILURE,
        message="fichier introuvable",
        tool_name="read_file",
    )

    assert error.is_expected is True


@pytest.mark.parametrize("tool_name", [None, "", "   "])
def test_tool_failure_without_tool_name_is_rejected(tool_name: str | None) -> None:
    with pytest.raises(ValidationError):
        ExecutionError(category=ErrorCategory.TOOL_FAILURE, message="echec outil", tool_name=tool_name)


def test_system_error_does_not_require_a_tool_name() -> None:
    error = ExecutionError(category=ErrorCategory.SYSTEM_ERROR, message="connexion Ollama refusee")

    assert error.tool_name is None
    assert error.is_expected is False


def test_system_error_may_still_reference_a_tool() -> None:
    error = ExecutionError(
        category=ErrorCategory.SYSTEM_ERROR,
        message="exception non geree dans l'outil",
        tool_name="run_command",
    )

    assert error.tool_name == "run_command"
    assert error.is_expected is False


@pytest.mark.parametrize("message", ["", "   "])
def test_blank_message_is_rejected(message: str) -> None:
    with pytest.raises(ValidationError):
        ExecutionError(category=ErrorCategory.SYSTEM_ERROR, message=message)


def test_message_is_stripped() -> None:
    error = ExecutionError(category=ErrorCategory.SYSTEM_ERROR, message="  boom  ")

    assert error.message == "boom"


def test_details_defaults_to_none_and_accepts_arbitrary_diagnostics() -> None:
    without_details = ExecutionError(category=ErrorCategory.SYSTEM_ERROR, message="boom")
    with_details = ExecutionError(
        category=ErrorCategory.TOOL_FAILURE,
        message="commande en echec",
        tool_name="run_command",
        details={"return_code": 1, "stderr": "not found"},
    )

    assert without_details.details is None
    assert with_details.details == {"return_code": 1, "stderr": "not found"}


def test_invalid_category_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ExecutionError(category="network_timeout", message="x")
