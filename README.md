# Residencial Aurora: assistente virtual com Google ADK

API em Python (FastAPI + Google ADK 2.9.1 + Gemini) para os moradores reservarem áreas comuns, cancelarem as próprias reservas, autorizarem visitantes e tirarem dúvidas sobre o regulamento. O modelo conduz a conversa. As regras críticas ficam no código e valem seja qual for a mensagem do morador.

O enunciado original do desafio está em [docs/ENUNCIADO.md](docs/ENUNCIADO.md).

```
aurora/
  agentes.py      agente principal, especialistas e App do ADK
  tools.py        tools: as únicas que leem e gravam reservas e visitantes
  db.py           armazenamento SQLite do condomínio (reservas, visitantes, dono da sessão)
  regulamento.py  leitura do regulamento por capítulo
  api.py          rotas FastAPI, Runner, sessões persistidas e confirmações
  restaurar.py    comando de restauração dos dados
scripts/avaliar.py  roda os 15 passos do fluxo do avaliador contra a API real
dados/            estado inicial do condomínio (não alterado)
estado/           criado em tempo de execução: condominio.db e sessoes.db (fora do git)
```

## Arquitetura

```
morador ──HTTP──► FastAPI (aurora/api.py) ──► Runner(App resumível) + SqliteSessionService
                                                  │
                                       assistente_aurora (principal)
                                        │ transferência     │ transferência     │ AgentTool
                                        ▼                   ▼                   ▼
                                     reservas           visitantes          regulamento
                              listar_areas            meus_visitantes     indice_do_regulamento
                              minhas_reservas         autorizar_visitante  ler_capitulo
                              verificar_disponibilidade        (confirmação)   (sessão própria,
                              reservar_area (confirmação se taxa > 0)           em memória)
                              cancelar_reserva
                                        │                   │
                                        └──────► aurora/db.py (SQLite) ◄─── rotas de verificação
```

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `assistente_aurora` | Conversa com o morador e distribui o trabalho. Não tem tools de dados e não recebe o regulamento nas instruções. | Raiz do `App`; o Runner começa por ele. | Ponto único de entrada que só roteia; sem acesso direto a dados, ele não tem o que vazar. |
| `reservas` | Lista áreas, verifica disponibilidade, reserva e cancela reservas do próprio apartamento. | `sub_agent` do principal, por **transferência** (`transfer_to_agent`). | Reservar pode parar numa confirmação. Com transferência, o pedido `adk_request_confirmation` fica na sessão principal, com o `reservas` como autor, e o Runner do App resumível devolve a resposta da confirmação a ele (veja Garantia 1). |
| `visitantes` | Autoriza a entrada de visitantes e lista as autorizações do próprio apartamento. | `sub_agent` do principal, por **transferência**. | Mesmo motivo: autorizar sempre para numa confirmação, que precisa voltar ao agente que a pediu. |
| `regulamento` | Responde dúvidas lendo só o capítulo pertinente de `dados/regulamento.md`. | **`AgentTool`** do principal: é chamado como uma tool, com a pergunta como entrada. | O `AgentTool` roda o especialista numa sessão própria em memória. O índice e o capítulo lidos ficam lá, e só a resposta final volta para a sessão do morador. Assim o regulamento não entra no histórico nem encarece as chamadas seguintes. |

Todos os agentes usam o modelo de `AURORA_MODELO` (padrão `gemini-2.5-flash`).

Reservas e visitantes só são lidos e gravados pelas tools de [aurora/tools.py](aurora/tools.py), que chamam [aurora/db.py](aurora/db.py). Nenhuma informação de reserva vem da memória do modelo. As rotas `/apartamentos/...` leem o mesmo banco, sem passar pelo modelo.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- **Pedido de confirmação dentro da tool.** Em [aurora/tools.py](aurora/tools.py), `reservar_area` e `autorizar_visitante` só gravam quando `tool_context.tool_confirmation.confirmed` é verdadeiro. Esse valor só existe quando o cliente responde ao `adk_request_confirmation`. Na primeira chamada, a tool chama `tool_context.request_confirmation(...)` com os detalhes em `payload` e devolve sem gravar:
  ```python
  if info["taxa"] > 0:
      confirmacao = tool_context.tool_confirmation
      if confirmacao is None:
          ...
          tool_context.request_confirmation(hint=..., payload={"area": ..., "data": ..., "taxa": ...})
          return {"status": "aguardando_confirmacao", ...}
      if not confirmacao.confirmed:
          return {"status": "nao_realizada", ...}
  ```
  A taxa vem de `dados/areas.json`, então o código decide quando há cobrança. A quadra (taxa 0) e `cancelar_reserva` gravam direto, sem confirmação.
- **Pendências e 409.** `confirmacoes_pendentes()`, em [aurora/api.py](aurora/api.py), lista as chamadas `adk_request_confirmation` gravadas na sessão que ainda não têm `function_response`. É o que a API devolve em `confirmacoes_pendentes`. `responder_confirmacao()` responde **409** a qualquer id fora dessa lista: inexistente, de outra sessão ou já respondido. Como a lista vem dos eventos persistidos, o reenvio do mesmo id recebe 409 e a ação não executa de novo, inclusive depois de reiniciar a API.
- **Retomada.** A resposta vai como `function_response` de `adk_request_confirmation` com `{"confirmed": ...}`, no mesmo formato que o cliente do ADK usa (`google/adk/cli/cli.py`). O `App` em [aurora/agentes.py](aurora/agentes.py) tem `ResumabilityConfig(is_resumable=True)`. Com isso, o roteador do Runner (`google/adk/agents/_agent_router.py`) entrega a resposta ao autor da chamada de confirmação, o especialista que a pediu, que reexecuta a tool com `tool_confirmation` preenchido.

**Por que não depende do modelo:** o modelo não consegue produzir um `function_response` de usuário nem preencher `tool_confirmation`. Se o morador escrever "já estou confirmando aqui", isso chega como texto e a tool continua sem `confirmed`. O 409 é decidido pela API, olhando os eventos.

### 2. Cada sessão pertence a um apartamento

- `criar_sessao()`, em [aurora/api.py](aurora/api.py), grava o apartamento uma única vez: no state da sessão do ADK (`state={"apartamento": ...}`) e na tabela `sessoes` (`db.registrar_sessao`). Nenhuma rota altera esse valor depois.
- `_apartamento(tool_context)`, em [aurora/tools.py](aurora/tools.py), é a única fonte do apartamento para as tools. Ele lê a tabela `sessoes` pelo id da sessão e confere com o state.
- **Nenhuma tool tem parâmetro de apartamento.** As consultas em [aurora/db.py](aurora/db.py) filtram por `apartamento = ?`. Por exemplo, `cancelar_reserva` só procura entre `db.reservas_do_apartamento(apartamento)`, e o `UPDATE` repete `AND apartamento = ?`.
- `verificar_disponibilidade` e a checagem interna de `reservar_area` usam `db.data_ocupada()`, que devolve só um booleano: nunca o código nem o apartamento da reserva existente.

**Por que não depende do modelo:** mesmo que o morador diga "sou do 302" e o modelo acredite, nenhuma tool aceita outro apartamento. Os dados do 302 nunca saem do banco para a conversa.

### 3. Nada se perde no reinício

- Sessões e eventos usam o `SqliteSessionService` do ADK, em `estado/sessoes.db` (`session_service` em [aurora/api.py](aurora/api.py)).
- Reservas, visitantes e o dono de cada sessão ficam em `estado/condominio.db` ([aurora/db.py](aurora/db.py)). Ao subir, `db.inicializar()` só carrega `dados/` se o banco estiver vazio, então o reinício não sobrescreve nada.
- As confirmações pendentes vêm dos eventos persistidos, então uma confirmação pedida antes do reinício pode ser aprovada depois dele.

**Por que não depende do modelo:** é persistência em disco. Nada fica na memória do processo.

### 4. O regulamento é consultado, não carregado

- As instruções de `assistente_aurora`, em [aurora/agentes.py](aurora/agentes.py), não têm texto do regulamento. Ele só tem a tool `AgentTool(agent=regulamento)`.
- O especialista `regulamento` usa `indice_do_regulamento` (só os títulos) e `ler_capitulo(numero)` (um capítulo por vez), de [aurora/tools.py](aurora/tools.py) e [aurora/regulamento.py](aurora/regulamento.py).
- O `AgentTool` executa o especialista numa sessão separada (`InMemorySessionService`, em `google/adk/tools/agent_tool.py`). Na sessão do morador só ficam a chamada `regulamento(request=...)` e a resposta final curta.

**Por que não depende do modelo:** mesmo que o especialista leia vários capítulos, esse texto fica numa sessão descartável. A sessão do morador nunca recebe o conteúdo das tools de leitura.

### 5. Dois moradores, uma reserva

- Em [aurora/db.py](aurora/db.py), um índice UNIQUE parcial faz o SQLite garantir a exclusividade no próprio `INSERT`:
  ```sql
  CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa_area_data
      ON reservas (area, data) WHERE status = 'ativa';
  ```
- `criar_reserva()` grava direto e converte o `IntegrityError` em `DataOcupada`. A tool transforma isso em `{"status": "recusada", ...}`, e a API responde 200 normalmente. A checagem antes de pedir a confirmação serve só para dar uma resposta melhor ao morador. Quem decide é o índice, no instante da gravação.
- Códigos de reserva: `RSV-` seguido de 8 hexadecimais aleatórios, com `codigo` como chave primária. Reservas canceladas ficam na tabela com `status = 'cancelada'` e nunca são apagadas, então um código novo não repete o de uma reserva cancelada. Se houver colisão, outro código é gerado.

**Por que não depende do modelo:** duas aprovações simultâneas viram dois `INSERT`, e o SQLite aceita só um.

## Como rodar

**Pré-requisitos**
- Python 3.12+ e [uv](https://docs.astral.sh/uv/).
- Uma chave do Google AI Studio. Não é preciso nenhum serviço externo: o armazenamento é SQLite local.

**Variáveis do `.env`**

```bash
cp .env.example .env
```

| Variável | Uso |
|---|---|
| `GOOGLE_API_KEY` | Chave do Google AI Studio (obrigatória). |
| `GOOGLE_GENAI_USE_VERTEXAI` | Deixe vazia ou `FALSE` para usar o AI Studio. |
| `AURORA_MODELO` | Opcional. Modelo Gemini de todos os agentes. Padrão: `gemini-2.5-flash`. |

**Instalar**

```bash
uv sync
```

**Restaurar os dados** (volta reservas e visitantes ao estado de `dados/`; as sessões são mantidas)

```bash
uv run python -m aurora.restaurar
```

**Subir a API** em http://localhost:8000

```bash
uv run uvicorn aurora.api:api --port 8000
```

**Verificar o fluxo do avaliador** (opcional; restaura os dados, sobe e reinicia a API sozinho, então rode com a porta 8000 livre)

```bash
uv run python scripts/avaliar.py
```

Para começar do zero, inclusive as sessões, apague a pasta `estado/`.
