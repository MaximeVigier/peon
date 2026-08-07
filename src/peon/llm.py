"""Abstraction pure pour un fournisseur de LLM.

Aucune dependance externe, aucun client concret : cette phase pose uniquement
le contrat que les futures implementations (Ollama, OpenAI, Anthropic...)
devront respecter. Meme philosophie que `Tool` (`tools/base.py`) et
`Reasoner` (`reasoner.py`) : une ABC, un seul point d'entree, zero
connaissance du modele ou du transport utilise en dessous.

Format des messages : liste de dicts `{"role": str, "content": str}`
(convention "chat" popularisee par OpenAI, reprise depuis par la plupart des
fournisseurs, y compris Ollama). Ce format est fige ici pour que
`prompts.py` et les futures implementations concretes de `LLM` s'accordent
sans ambiguite. `generate()` retourne la reponse texte brute du modele : elle
ne parse rien et ne valide rien, cette responsabilite appartient a l'appelant
(le Reasoner).
"""

from abc import ABC, abstractmethod


class LLM(ABC):
    @abstractmethod
    def generate(self, messages: list[dict[str, str]]) -> str:
        ...
