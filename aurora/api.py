"""API HTTP do assistente. Uso: uv run uvicorn aurora.api:api --port 8000"""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import errors, types
from pydantic import BaseModel

from aurora import db
from aurora.agentes import app as adk_app
from aurora.config import APP_NAME, BANCO_SESSOES

CONFIRMACAO = "adk_request_confirmation"

session_service = SqliteSessionService(str(BANCO_SESSOES))
runner = Runner(app=adk_app, session_service=session_service)
# Uma execução por vez em cada sessão; sessões diferentes rodam em paralelo.
travas: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.inicializar()
    yield
    await runner.close()


api = FastAPI(title="Residencial Aurora", lifespan=lifespan)


@api.exception_handler(errors.APIError)
async def erro_do_modelo(_: Request, erro: errors.APIError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"detail": f"Falha ao chamar o modelo: {erro.code} {erro.status}"})


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


# --- sessão e confirmações ---------------------------------------------------

async def carregar_sessao(session_id: str) -> Session:
    apartamento = db.apartamento_da_sessao(session_id)
    sessao = None
    if apartamento is not None:
        sessao = await session_service.get_session(
            app_name=APP_NAME, user_id=apartamento, session_id=session_id
        )
    if sessao is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada.")
    return sessao


def confirmacoes_pendentes(sessao: Session) -> list[dict]:
    """Pedidos adk_request_confirmation gravados na sessão que ainda não têm resposta.

    É derivado só dos eventos persistidos, então vale igual depois de reiniciar
    a API, e um id já respondido deixa de aparecer aqui.
    """
    pedidos: dict[str, dict] = {}
    respondidos: set[str] = set()
    for evento in sessao.events:
        for chamada in evento.get_function_calls():
            if chamada.name == CONFIRMACAO and chamada.id:
                pedidos[chamada.id] = chamada.args or {}
        for resposta in evento.get_function_responses():
            if resposta.name == CONFIRMACAO and resposta.id:
                respondidos.add(resposta.id)
    pendentes = []
    for id_, args in pedidos.items():
        if id_ in respondidos:
            continue
        original = args.get("originalFunctionCall", {})
        confirmacao = args.get("toolConfirmation", {})
        pendentes.append({
            "id": id_,
            "acao": original.get("name", ""),
            "detalhes": confirmacao.get("payload") or original.get("args", {}),
        })
    return pendentes


async def executar(sessao: Session, conteudo: types.Content) -> dict:
    textos: list[str] = []
    async for evento in runner.run_async(
        user_id=sessao.user_id, session_id=sessao.id, new_message=conteudo
    ):
        if evento.author == "user" or evento.partial or not evento.content:
            continue
        for parte in evento.content.parts or []:
            if parte.text and not parte.thought:
                textos.append(parte.text.strip())
    sessao = await carregar_sessao(sessao.id)
    return {
        "resposta": "\n\n".join(t for t in textos if t),
        "confirmacoes_pendentes": confirmacoes_pendentes(sessao),
    }


# --- rotas de conversa -------------------------------------------------------

@api.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao) -> dict:
    apartamento = corpo.apartamento.strip()
    if apartamento not in db.apartamentos():
        raise HTTPException(status_code=422, detail="Apartamento inexistente.")
    sessao = await session_service.create_session(
        app_name=APP_NAME, user_id=apartamento, state={"apartamento": apartamento}
    )
    db.registrar_sessao(sessao.id, apartamento)
    return {"session_id": sessao.id}


@api.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem) -> dict:
    await carregar_sessao(session_id)
    async with travas[session_id]:
        sessao = await carregar_sessao(session_id)
        conteudo = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
        return await executar(sessao, conteudo)


@api.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao) -> dict:
    await carregar_sessao(session_id)
    async with travas[session_id]:
        sessao = await carregar_sessao(session_id)
        if corpo.id not in {p["id"] for p in confirmacoes_pendentes(sessao)}:
            raise HTTPException(
                status_code=409,
                detail="Não existe confirmação pendente com esse id nesta sessão.",
            )
        # Mesmo formato que o cliente do ADK usa (google/adk/cli/cli.py): a resposta
        # vai como function_response do adk_request_confirmation, e o Runner,
        # com o App resumível, retoma o agente que pediu a confirmação.
        conteudo = types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(
                id=corpo.id, name=CONFIRMACAO, response={"confirmed": corpo.confirmado}
            )
        )])
        return await executar(sessao, conteudo)


@api.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str) -> list[dict]:
    sessao = await carregar_sessao(session_id)
    return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in sessao.events]


# --- rotas de verificação (leem o banco direto, sem o modelo) ----------------

@api.get("/apartamentos/{numero}/reservas")
def reservas(numero: str) -> list[dict]:
    return db.reservas_do_apartamento(numero)


@api.get("/apartamentos/{numero}/visitantes")
def visitantes(numero: str) -> list[dict]:
    return db.visitantes_do_apartamento(numero)
