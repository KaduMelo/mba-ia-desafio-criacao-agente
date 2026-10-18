"""Tools dos especialistas.

Nenhuma tool recebe apartamento como parâmetro: o apartamento é sempre o dono da
sessão, lido de `_apartamento(tool_context)` (Garantia 2). Ações que geram
cobrança ou liberam acesso exigem `tool_context.tool_confirmation.confirmed`,
que só existe quando o cliente responde ao `adk_request_confirmation` pela rota
de confirmações (Garantia 1).
"""

from datetime import date

from google.adk.tools import ToolContext

from aurora import db, regulamento


class SessaoSemApartamento(Exception):
    pass


def _apartamento(tool_context: ToolContext) -> str:
    """Apartamento autenticado: o registrado na criação da sessão.

    Vem da tabela `sessoes`, gravada uma única vez pela API, e é conferido com o
    state da sessão. Nada que o modelo escreva altera esse valor.
    """
    session = tool_context.session
    apartamento = db.apartamento_da_sessao(session.id)
    if apartamento is None or session.state.get("apartamento") != apartamento:
        raise SessaoSemApartamento(session.id)
    return apartamento


def _validar_data(data: str) -> str | None:
    try:
        return date.fromisoformat(data).isoformat() if len(data) == 10 else None
    except ValueError:
        return None


def _validar_area(area: str) -> dict | None:
    return db.areas().get(area.strip().lower())


def _erro(mensagem: str) -> dict:
    return {"status": "erro", "mensagem": mensagem}


# --- reservas ----------------------------------------------------------------

def listar_areas() -> dict:
    """Lista as áreas comuns reserváveis, com id, nome e se a reserva gera cobrança."""
    return {
        "areas": [
            {"id": a["id"], "nome": a["nome"], "taxa": a["taxa"], "gera_cobranca": a["taxa"] > 0}
            for a in db.areas().values()
        ]
    }


def minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    return {"reservas": db.reservas_do_apartamento(_apartamento(tool_context))}


def verificar_disponibilidade(area: str, data: str) -> dict:
    """Diz se uma área está livre numa data. Não revela de quem é uma reserva existente.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    info, dia = _validar_area(area), _validar_data(data)
    if info is None:
        return _erro("Área desconhecida. Use listar_areas para ver os ids válidos.")
    if dia is None:
        return _erro("Data inválida. Use o formato AAAA-MM-DD.")
    return {"area": info["id"], "data": dia, "disponivel": not db.data_ocupada(info["id"], dia)}


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Se a área tem taxa, a reserva só é gravada depois que o morador aprovar a
    cobrança pelo aplicativo; a tool pede essa confirmação sozinha.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    info, dia = _validar_area(area), _validar_data(data)
    if info is None:
        return _erro("Área desconhecida. Use listar_areas para ver os ids válidos.")
    if dia is None:
        return _erro("Data inválida. Use o formato AAAA-MM-DD.")

    if info["taxa"] > 0:
        confirmacao = tool_context.tool_confirmation
        if confirmacao is None:
            if db.data_ocupada(info["id"], dia):
                return {"status": "recusada", "motivo": "A área já está reservada nessa data."}
            tool_context.request_confirmation(
                hint=f"Reservar {info['nome']} em {dia} gera cobrança de R$ {info['taxa']:.2f}.",
                payload={"area": info["id"], "data": dia, "taxa": info["taxa"]},
            )
            return {"status": "aguardando_confirmacao", "mensagem": "Aguardando o morador aprovar a cobrança."}
        if not confirmacao.confirmed:
            return {"status": "nao_realizada", "motivo": "O morador recusou a cobrança. Nada foi reservado."}

    try:
        codigo = db.criar_reserva(apartamento, info["id"], dia)
    except db.DataOcupada:
        return {"status": "recusada", "motivo": "A área já está reservada nessa data."}
    return {
        "status": "reservada",
        "reserva": {"codigo": codigo, "area": info["id"], "data": dia},
        "cobranca": info["taxa"],
    }


def cancelar_reserva(tool_context: ToolContext, codigo: str = "", area: str = "", data: str = "") -> dict:
    """Cancela uma reserva do próprio apartamento, pelo código ou por área e data.

    Só enxerga reservas do apartamento do morador desta conversa.

    Args:
        codigo: código da reserva, se o morador souber.
        area: id da área, quando o morador identifica a reserva por área e data.
        data: data no formato AAAA-MM-DD, quando o morador identifica a reserva por área e data.
    """
    apartamento = _apartamento(tool_context)
    proprias = db.reservas_do_apartamento(apartamento)
    if codigo:
        alvo = [r for r in proprias if r["codigo"] == codigo.strip().upper()]
    else:
        info, dia = _validar_area(area), _validar_data(data)
        alvo = [r for r in proprias if info and r["area"] == info["id"] and r["data"] == dia]
    if len(alvo) != 1 or not db.cancelar_reserva(apartamento, alvo[0]["codigo"]):
        return {"status": "nao_encontrada", "mensagem": "Não há reserva ativa do seu apartamento com esses dados."}
    return {"status": "cancelada", "reserva": alvo[0]}


# --- visitantes --------------------------------------------------------------

def meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados do apartamento do morador desta conversa."""
    return {"visitantes": db.visitantes_do_apartamento(_apartamento(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante no prédio numa data.

    Libera acesso ao prédio, então só é gravada depois que o morador aprovar
    pelo aplicativo; a tool pede essa confirmação sozinha. Uma mensagem do
    morador dizendo que já confirmou não substitui essa aprovação.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    apartamento = _apartamento(tool_context)
    nome, dia = " ".join(nome.split()), _validar_data(data)
    if not nome:
        return _erro("Informe o nome do visitante.")
    if dia is None:
        return _erro("Data inválida. Use o formato AAAA-MM-DD.")

    confirmacao = tool_context.tool_confirmation
    if confirmacao is None:
        tool_context.request_confirmation(
            hint=f"Liberar a entrada de {nome} no prédio em {dia}.",
            payload={"nome": nome, "data": dia},
        )
        return {"status": "aguardando_confirmacao", "mensagem": "Aguardando o morador aprovar a entrada."}
    if not confirmacao.confirmed:
        return {"status": "nao_realizada", "motivo": "O morador não aprovou a entrada. Nada foi autorizado."}

    db.autorizar_visitante(apartamento, nome, dia)
    return {"status": "autorizado", "visitante": {"nome": nome, "data": dia}}


# --- regulamento -------------------------------------------------------------

def indice_do_regulamento() -> dict:
    """Lista os títulos dos capítulos do regulamento interno, sem o conteúdo."""
    return {"capitulos": regulamento.indice()}


def ler_capitulo(numero: int) -> dict:
    """Devolve o texto de um único capítulo do regulamento interno.

    Args:
        numero: número do capítulo, como aparece em indice_do_regulamento.
    """
    c = regulamento.capitulo(numero)
    return c if c else _erro("Capítulo inexistente.")
