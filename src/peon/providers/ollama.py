"""OllamaLLM : premier fournisseur LLM concret derriere l'ABC LLM (llm.py).

Traduit `generate(messages)` en un appel HTTP vers l'API chat d'Ollama
(`POST {base_url}/api/chat`) et retourne le texte brut produit par le
modele. Ne connait ni Context, ni Decision, ni Reasoner, ni Runtime : ne
parse jamais la reponse comme une Decision (responsabilite du Reasoner, voir
`reasoner.py`), n'implemente que le contrat `LLM.generate()`.

Utilise uniquement la bibliotheque standard (`urllib`) : aucune dependance
HTTP supplementaire n'est necessaire pour ce premier fournisseur, conforme a
la philosophie "peu de dependances" du projet (voir `CONTEXT.md`).

Configuration explicite via `OllamaConfig` : `model` (obligatoire, aucune
valeur par defaut), `base_url` (par defaut l'installation locale standard
d'Ollama) et `timeout_seconds`. Aucune cle API hardcodee : Ollama, execute en
local, n'en requiert pas ; un futur fournisseur qui en aurait besoin (OpenAI,
Anthropic...) devra suivre le meme principe -- jamais de secret en dur dans
le code, uniquement dans la configuration fournie explicitement par
l'appelant.
"""

import json
import urllib.error
import urllib.request

from pydantic import BaseModel, Field, field_validator

from peon.llm import LLM

_DEFAULT_BASE_URL = "http://localhost:11434"
_DEFAULT_TIMEOUT_SECONDS = 60.0


class OllamaConfig(BaseModel):
    model: str = Field(min_length=1)
    base_url: str = Field(default=_DEFAULT_BASE_URL, min_length=1)
    timeout_seconds: float = Field(default=_DEFAULT_TIMEOUT_SECONDS, gt=0)

    @field_validator("model", "base_url")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("ne peut pas etre vide")
        return stripped


class OllamaRequestError(Exception):
    pass


class OllamaLLM(LLM):
    def __init__(self, config: OllamaConfig) -> None:
        self._config = config

    def generate(self, messages: list[dict[str, str]]) -> str:
        request = urllib.request.Request(
            url=f"{self._config.base_url.rstrip('/')}/api/chat",
            data=json.dumps({"model": self._config.model, "messages": messages, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_seconds) as response:
                raw_body = response.read()
        except urllib.error.URLError as exc:
            raise OllamaRequestError(f"appel a Ollama ({self._config.base_url}) impossible : {exc}") from exc

        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise OllamaRequestError(f"reponse Ollama invalide (pas un JSON) : {exc}") from exc

        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaRequestError(f"reponse Ollama sans champ 'message.content' exploitable : {body!r}") from exc
