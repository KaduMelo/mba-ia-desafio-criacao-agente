"""Armazenamento do condomínio em SQLite.

As regras que não podem depender do modelo moram aqui:
- exclusividade de área por data: índice UNIQUE parcial sobre reservas ativas,
  verificado pelo próprio SQLite no INSERT (Garantia 5);
- códigos de reserva nunca repetidos: chave primária sobre todas as reservas,
  inclusive as canceladas, que nunca são apagadas;
- toda leitura e escrita de reservas e visitantes é filtrada pelo apartamento
  recebido do chamador, que é sempre o dono da sessão (Garantia 2).
"""

import json
import secrets
import sqlite3
from contextlib import contextmanager

from aurora.config import BANCO_CONDOMINIO, DADOS

ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
    ON reservas (area, data) WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL
);
"""


class DataOcupada(Exception):
    """A área já tem reserva ativa na data."""


@contextmanager
def conexao():
    con = sqlite3.connect(BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    try:
        yield con
    finally:
        con.close()


def _ler_json(nome: str):
    return json.loads((DADOS / nome).read_text(encoding="utf-8"))


def inicializar() -> None:
    """Cria o esquema; na primeira execução, carrega o estado inicial."""
    with conexao() as con:
        con.executescript(ESQUEMA)
        vazio = con.execute("SELECT COUNT(*) FROM reservas").fetchone()[0] == 0
    if vazio:
        restaurar()


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/*.json (sessões ficam)."""
    with conexao() as con:
        con.executescript(ESQUEMA)
        con.execute("BEGIN IMMEDIATE")
        con.execute("DELETE FROM reservas")
        con.execute("DELETE FROM visitantes")
        con.executemany(
            "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
            [(r["codigo"], r["apartamento"], r["area"], r["data"]) for r in _ler_json("reservas.json")],
        )
        con.executemany(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            [(v["apartamento"], v["nome"], v["data"]) for v in _ler_json("visitantes.json")],
        )
        con.execute("COMMIT")


# --- dados fixos -------------------------------------------------------------

def areas() -> dict[str, dict]:
    return {a["id"]: a for a in _ler_json("areas.json")}


def apartamentos() -> set[str]:
    return {a["numero"] for a in _ler_json("apartamentos.json")}


# --- sessões -----------------------------------------------------------------

def registrar_sessao(session_id: str, apartamento: str) -> None:
    with conexao() as con:
        con.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)",
            (session_id, apartamento),
        )


def apartamento_da_sessao(session_id: str) -> str | None:
    with conexao() as con:
        linha = con.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return linha["apartamento"] if linha else None


# --- reservas ----------------------------------------------------------------

def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def data_ocupada(area: str, data: str) -> bool:
    """Só responde se há reserva ativa; nunca de quem é."""
    with conexao() as con:
        return con.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone() is not None


def _novo_codigo() -> str:
    return f"RSV-{secrets.token_hex(4).upper()}"


def criar_reserva(apartamento: str, area: str, data: str) -> str:
    """Grava a reserva. A exclusividade é decidida pelo índice UNIQUE no INSERT.

    Levanta DataOcupada se outra reserva ativa ocupar a área na data, mesmo
    que ela tenha sido gravada um instante antes por outra requisição.
    """
    with conexao() as con:
        while True:
            codigo = _novo_codigo()
            try:
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                    (codigo, apartamento, area, data),
                )
                return codigo
            except sqlite3.IntegrityError as erro:
                if "reservas.codigo" in str(erro):
                    continue  # colisão de código (improvável): gera outro
                raise DataOcupada from erro


def cancelar_reserva(apartamento: str, codigo: str) -> bool:
    """Cancela só se a reserva for do apartamento informado."""
    with conexao() as con:
        cursor = con.execute(
            "UPDATE reservas SET status = 'cancelada'"
            " WHERE codigo = ? AND apartamento = ? AND status = 'ativa'",
            (codigo, apartamento),
        )
    return cursor.rowcount == 1


# --- visitantes --------------------------------------------------------------

def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with conexao() as con:
        linhas = con.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, id",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    with conexao() as con:
        con.execute(
            "INSERT INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
