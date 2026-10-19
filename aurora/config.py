"""Caminhos e configuração lidos do ambiente."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

DADOS = RAIZ / "dados"
ESTADO = RAIZ / "estado"
ESTADO.mkdir(exist_ok=True)

# Dados do condomínio (reservas, visitantes, dono de cada sessão).
BANCO_CONDOMINIO = ESTADO / "condominio.db"
# Sessões e eventos do ADK.
BANCO_SESSOES = ESTADO / "sessoes.db"

APP_NAME = "residencial_aurora"
MODELO = os.getenv("AURORA_MODELO") or "gemini-3.5-flash"
