import pytest
from pydantic import ValidationError

from peon.models.tool_result import ToolResult


def test_successful_result_needs_no_error() -> None:
    result = ToolResult(success=True, output="contenu du fichier")

    assert result.error is None
    assert result.return_code is None
    assert result.duration_ms is None


def test_success_with_error_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(success=True, error="ne devrait pas etre la")


@pytest.mark.parametrize("error", [None, ""])
def test_failure_without_error_is_rejected(error: str | None) -> None:
    with pytest.raises(ValidationError):
        ToolResult(success=False, error=error)


def test_failure_with_error_is_valid() -> None:
    result = ToolResult(success=False, error="fichier introuvable", return_code=1)

    assert result.success is False
    assert result.error == "fichier introuvable"


def test_negative_duration_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolResult(success=True, duration_ms=-1)
