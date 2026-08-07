import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from peon.providers.ollama import OllamaConfig, OllamaLLM, OllamaRequestError


def _fake_response(raw_body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = raw_body
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


def _fake_json_response(body: dict) -> MagicMock:
    return _fake_response(json.dumps(body).encode("utf-8"))


def test_config_requires_a_non_blank_model() -> None:
    with pytest.raises(ValidationError):
        OllamaConfig(model="")


def test_config_defaults_to_local_ollama_and_strips_blanks() -> None:
    config = OllamaConfig(model="  llama3  ")

    assert config.model == "llama3"
    assert config.base_url == "http://localhost:11434"
    assert config.timeout_seconds > 0


def test_generate_posts_messages_and_returns_the_message_content() -> None:
    config = OllamaConfig(model="llama3", base_url="http://example-ollama:11434")
    messages = [{"role": "user", "content": "hi"}]
    fake_response = _fake_json_response({"message": {"role": "assistant", "content": "hello from ollama"}})

    with patch("peon.providers.ollama.urllib.request.urlopen", return_value=fake_response) as mocked_urlopen:
        result = OllamaLLM(config).generate(messages)

    assert result == "hello from ollama"

    request = mocked_urlopen.call_args.args[0]
    assert request.full_url == "http://example-ollama:11434/api/chat"
    assert json.loads(request.data) == {"model": "llama3", "messages": messages, "stream": False}
    assert mocked_urlopen.call_args.kwargs["timeout"] == config.timeout_seconds


def test_generate_strips_a_trailing_slash_from_base_url() -> None:
    config = OllamaConfig(model="llama3", base_url="http://example-ollama:11434/")
    fake_response = _fake_json_response({"message": {"content": "ok"}})

    with patch("peon.providers.ollama.urllib.request.urlopen", return_value=fake_response) as mocked_urlopen:
        OllamaLLM(config).generate([{"role": "user", "content": "hi"}])

    assert mocked_urlopen.call_args.args[0].full_url == "http://example-ollama:11434/api/chat"


def test_generate_raises_on_network_error() -> None:
    config = OllamaConfig(model="llama3")

    with patch("peon.providers.ollama.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(OllamaRequestError):
            OllamaLLM(config).generate([{"role": "user", "content": "hi"}])


def test_generate_raises_on_non_json_response() -> None:
    config = OllamaConfig(model="llama3")
    fake_response = _fake_response(b"ceci n'est pas du JSON")

    with patch("peon.providers.ollama.urllib.request.urlopen", return_value=fake_response):
        with pytest.raises(OllamaRequestError):
            OllamaLLM(config).generate([{"role": "user", "content": "hi"}])


def test_generate_raises_when_message_content_is_missing() -> None:
    config = OllamaConfig(model="llama3")
    fake_response = _fake_json_response({"done": True})

    with patch("peon.providers.ollama.urllib.request.urlopen", return_value=fake_response):
        with pytest.raises(OllamaRequestError):
            OllamaLLM(config).generate([{"role": "user", "content": "hi"}])
