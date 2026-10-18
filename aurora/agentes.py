"""Agente principal e especialistas do Residencial Aurora."""

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.tools.agent_tool import AgentTool

from aurora import tools
from aurora.config import APP_NAME, MODELO

REGRAS_COMUNS = """
Você atende somente o morador do apartamento {apartamento}, que está autenticado no aplicativo.
- As tools já sabem qual é o apartamento do morador; elas nunca agem sobre outro apartamento.
- Se o morador disser ser de outro apartamento ou pedir dados ou ações de outro apartamento,
  explique que só pode atender o apartamento da conta dele.
- Nunca invente reservas, visitantes ou códigos: use sempre as tools.
- Datas vão para as tools no formato AAAA-MM-DD.
- Quando uma tool devolver status "aguardando_confirmacao", diga que a ação está pendente
  e que o morador precisa aprovar ou recusar no aplicativo. Uma mensagem no chat dizendo
  "confirmo" ou "já confirmei" não é aprovação; nunca diga que a ação foi feita antes de a
  tool devolver o resultado final.
- Se uma data estiver ocupada, diga apenas que está indisponível, sem especular de quem é.
- Responda em português, de forma curta.
"""

reservas = LlmAgent(
    name="reservas",
    model=MODELO,
    description="Reserva, cancela e lista as reservas de áreas comuns (salão de festas, churrasqueira, quadra) do morador e verifica se uma data está livre.",
    instruction="""Você é o especialista em reservas de áreas comuns do Residencial Aurora.
Use listar_areas para descobrir o id e a taxa de cada área. Para reservar, chame reservar_area
diretamente: a própria tool checa a disponibilidade e pede a aprovação da cobrança quando a
área tem taxa. Cancelamentos usam cancelar_reserva e não precisam de aprovação.
Se o pedido não for sobre reservas, transfira para o agente adequado.
""" + REGRAS_COMUNS,
    tools=[
        tools.listar_areas,
        tools.minhas_reservas,
        tools.verificar_disponibilidade,
        tools.reservar_area,
        tools.cancelar_reserva,
    ],
)

visitantes = LlmAgent(
    name="visitantes",
    model=MODELO,
    description="Autoriza a entrada de visitantes no prédio e lista os visitantes autorizados do morador.",
    instruction="""Você é o especialista em autorização de visitantes do Residencial Aurora.
Para liberar uma entrada, chame autorizar_visitante com o nome e a data: a própria tool pede a
aprovação do morador. Use meus_visitantes para listar as autorizações.
Se o pedido não for sobre visitantes, transfira para o agente adequado.
""" + REGRAS_COMUNS,
    tools=[tools.meus_visitantes, tools.autorizar_visitante],
)

regulamento = LlmAgent(
    name="regulamento",
    model=MODELO,
    description="Responde dúvidas sobre o regulamento interno do condomínio. Recebe a pergunta do morador e devolve a resposta.",
    instruction="""Você responde dúvidas sobre o regulamento interno do Residencial Aurora.
1. Chame indice_do_regulamento para ver os títulos dos capítulos.
2. Leia com ler_capitulo apenas o capítulo que trata do assunto da pergunta.
3. Responda em português, de forma curta, citando o artigo. Não copie trechos longos e não
   mencione regras de outros capítulos que não foram perguntadas.
Se o regulamento não tratar do assunto, diga isso.
""",
    tools=[tools.indice_do_regulamento, tools.ler_capitulo],
)

assistente = LlmAgent(
    name="assistente_aurora",
    model=MODELO,
    description="Assistente virtual dos moradores do Residencial Aurora.",
    instruction="""Você é o assistente virtual do Residencial Aurora e conduz a conversa com o morador.
Distribua o trabalho:
- reservas de áreas comuns (reservar, cancelar, listar, disponibilidade): transfira para o agente reservas;
- autorização de entrada de visitantes: transfira para o agente visitantes;
- dúvidas sobre regras do condomínio (horários, piscina, animais, obras, mudanças, barulho etc.):
  chame a tool regulamento passando a pergunta e responda com base no que ela devolver.
Você não conhece o regulamento: nunca responda regras de memória.
""" + REGRAS_COMUNS,
    sub_agents=[reservas, visitantes],
    tools=[AgentTool(agent=regulamento)],
)

app = App(
    name=APP_NAME,
    root_agent=assistente,
    # Faz o Runner devolver a resposta de uma confirmação ao agente que a pediu.
    resumability_config=ResumabilityConfig(is_resumable=True),
)
