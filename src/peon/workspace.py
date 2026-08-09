"""Port technique entre les Tools et le filesystem/processus reels.

`Workspace` ne porte aucune logique metier ni politique de securite (pas de
sandbox, pas de restriction de chemins, pas de whitelist de commandes) : ces
sujets appartiennent a un futur Policy Engine etendu, pas a ce module.
`read_file`/`list_directory`/`delete_file` reproduisent exactement le
comportement bas niveau utilise jusqu'ici directement par les Tools
(`pathlib.Path`), y compris leurs exceptions (`OSError`, `UnicodeDecodeError`
pour `read_file`) : c'est au Tool appelant de les capturer et de les traduire
en `ToolResult`, comme il le faisait deja avant cette indirection.
`run_command` laisse de meme remonter `OSError`, mais decode toujours
`stdout`/`stderr` en `str` sans jamais lever `UnicodeDecodeError` (encodage
explicite, octets invalides remplaces) : une sortie shell mal encodee reste
un cas normal, pas une exception a faire remonter au Tool appelant.
"""

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import NamedTuple


class CommandResult(NamedTuple):
    stdout: str
    stderr: str
    return_code: int


class Workspace(ABC):
    @abstractmethod
    def read_file(self, path: str) -> str: ...

    @abstractmethod
    def list_directory(self, path: str) -> list[str]: ...

    @abstractmethod
    def run_command(self, command: str) -> CommandResult: ...

    @abstractmethod
    def delete_file(self, path: str) -> None: ...


class LocalWorkspace(Workspace):
    def read_file(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")

    def list_directory(self, path: str) -> list[str]:
        return [entry.name for entry in Path(path).iterdir()]

    def delete_file(self, path: str) -> None:
        Path(path).unlink()

    def run_command(self, command: str) -> CommandResult:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return CommandResult(stdout=completed.stdout, stderr=completed.stderr, return_code=completed.returncode)
