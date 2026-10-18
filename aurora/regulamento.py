"""Acesso ao regulamento por capítulo, para que só o trecho pertinente seja lido."""

import re
from functools import cache

from aurora.config import DADOS


@cache
def capitulos() -> list[dict]:
    """Lista de {numero, titulo, texto}, um item por '## Capítulo ...'."""
    texto = (DADOS / "regulamento.md").read_text(encoding="utf-8")
    partes = re.split(r"^## ", texto, flags=re.MULTILINE)[1:]
    resultado = []
    for numero, parte in enumerate(partes, start=1):
        titulo, _, corpo = parte.partition("\n")
        resultado.append({"numero": numero, "titulo": titulo.strip(), "texto": corpo.strip()})
    return resultado


def indice() -> list[dict]:
    """Só os títulos dos capítulos, sem conteúdo."""
    return [{"numero": c["numero"], "titulo": c["titulo"]} for c in capitulos()]


def capitulo(numero: int) -> dict | None:
    for c in capitulos():
        if c["numero"] == numero:
            return c
    return None

