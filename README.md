# Assistente virtual do Residencial Aurora

Assistente de condomínio construído com Google ADK e exposto por uma API FastAPI. Pelo chat, o morador reserva e cancela áreas comuns, autoriza visitantes e tira dúvidas sobre o regulamento. O modelo conduz a conversa; as regras críticas ficam no código e no banco, fora do alcance do que o morador escreve.

O enunciado original do desafio está em [`docs/enunciado.md`](docs/enunciado.md).

## Arquitetura

```
POST /sessoes/{id}/mensagens ─┐
POST /sessoes/{id}/confirmacoes ─┤
                              ▼
                 aurora/assistente.py  (Runner do ADK + SqliteSessionService)
                              │
                     assistente_aurora            agente principal
                      │       │       └─ tool consultar_regulamento ──► especialista_regulamento
        transferência │       │ transferência                           (Runner próprio, sessão em memória)
                      ▼       ▼
        especialista_reservas   especialista_visitantes
                      │               │
                      ▼               ▼
              aurora/tools.py ──► aurora/banco.py ──► var/condominio.db (SQLite)
```

| Arquivo | Papel |
| --- | --- |
| `aurora/api.py` | Rotas do contrato e rotas de verificação. |
| `aurora/assistente.py` | Runner, sessões persistidas, envio de mensagens e resposta a confirmações. |
| `aurora/agentes.py` | Agente principal e especialistas de reservas e visitantes. |
| `aurora/regulamento.py` | Especialista em regulamento e a execução isolada dele. |
| `aurora/tools.py` | Tools de reservas e visitantes. |
| `aurora/banco.py` | Esquema SQLite, carga dos dados iniciais e operações do condomínio. |
| `aurora/restaurar.py` | Comando de restauração. |

### Agentes

**`assistente_aurora` (principal).** Recebe o morador e distribui o trabalho. Não tem nenhuma tool de reserva ou de visitante: transfere a conversa para um especialista (`sub_agents`, com `transfer_to_agent`) ou chama a tool `consultar_regulamento`. O regulamento não aparece nas instruções dele.

**`especialista_reservas`.** Dono das tools `listar_areas`, `consultar_disponibilidade`, `listar_minhas_reservas`, `reservar_area` e `cancelar_reserva`. É acionado por transferência porque o pedido de reserva pode parar esperando confirmação, e a retomada precisa voltar para o agente que chamou a tool (veja a Garantia 1).

**`especialista_visitantes`.** Dono das tools `listar_meus_visitantes` e `autorizar_visitante`. Também é acionado por transferência, pelo mesmo motivo. Fica separado do especialista de reservas para que cada agente veja só as tools do próprio assunto, o que reduz a chance de o modelo escolher a tool errada.

**`especialista_regulamento`.** Lê o regulamento por capítulo com a tool `ler_capitulo` e devolve uma resposta curta. É acionado como tool (`consultar_regulamento`), e não por transferência, de propósito: ele roda em um Runner próprio com sessão em memória descartável, então o texto dos capítulos nunca entra na sessão do morador (Garantia 4). Os três outros agentes têm essa tool, para que uma dúvida de regra seja respondida sem depender de uma transferência extra.

### Decisões que valem registro

- **Especialistas sem bloqueio de transferência.** Com `disallow_transfer_to_parent` e `disallow_transfer_to_peers` ligados, o Runner do ADK 2.9.1 entrega a resposta de uma confirmação ao agente principal, que já encerrou a parte dele: a rota responde 200 e a tool não executa. Sem os bloqueios, a resposta chega ao especialista que pediu a confirmação. Isso foi testado com sessão persistida em SQLite e também aprovando depois de reiniciar a API.
- **Consequência:** o especialista que respondeu por último recebe a próxima mensagem do morador. Por isso as instruções de cada especialista mandam transferir para o colega ou para o principal quando o assunto muda.
- **Dois bancos SQLite em `var/`:** `condominio.db` (dados do condomínio e o dono de cada sessão) e `sessoes.db` (sessões e eventos do ADK, no esquema do próprio `SqliteSessionService`).
- **A restauração apaga também as sessões.** Uma conversa antiga falaria de reservas que deixaram de existir e poderia ter confirmações pendentes sobre dados restaurados.

## Garantias

### Garantia 1: cobrança ou acesso só com confirmação

- **Quem decide se há confirmação é o código**, em `aurora/tools.py`: `FunctionTool(reservar_area, require_confirmation=reserva_exige_confirmacao)` e `FunctionTool(autorizar_visitante, require_confirmation=visitante_exige_confirmacao)`. `reserva_exige_confirmacao` consulta a taxa da área no banco: taxa maior que zero pede confirmação, taxa zero (quadra) não. `cancelar_reserva` é uma função comum, sem confirmação.
- **O ADK pausa antes de executar a tool** e grava na sessão um evento `adk_request_confirmation`. `confirmacoes_pendentes()` em `aurora/assistente.py` deriva a lista de pendências desses eventos, e `detalhes_da_confirmacao()` em `aurora/tools.py` monta os `detalhes` a partir dos argumentos da chamada original.
- **A aprovação só entra pela rota de confirmações.** `Assistente.enviar_mensagem` monta sempre um `Part(text=...)`: o que o morador escreve nunca vira resposta de confirmação. `Assistente.responder_confirmacao` é o único ponto que cria o `FunctionResponse` de `adk_request_confirmation` com um `ToolConfirmation`, o mesmo formato que o cliente do ADK usa.
- **O `409`** sai da primeira checagem de `responder_confirmacao`: o `id` precisa estar na lista de pendências daquela sessão. Um `id` inventado, de outra sessão ou já respondido levanta `ConfirmacaoInexistente` antes de o Runner ser chamado. Depois da primeira resposta, o evento do morador fica gravado na sessão e o mesmo `id` deixa de ser pendente.
- **Negar encerra o assunto:** `_encerrar_apos_recusa` em `aurora/agentes.py` (um `before_model_callback` dos especialistas) responde com um texto fixo quando a confirmação é negada, sem consultar o modelo. Sem isso, o modelo recebia a recusa como erro da tool e chamava a tool de novo, abrindo outra pendência.
- **Reforço dentro das tools:** `reservar_area` e `autorizar_visitante` conferem `_aprovada(tool_context)` antes de gravar. Mesmo que a execução chegasse à função por outro caminho, área com taxa e visitante não seriam gravados sem um `ToolConfirmation` aprovado.

Não depende do modelo porque o modelo só consegue pedir a chamada da tool. A pausa, a pendência e a retomada são do ADK e da rota; "já estou confirmando aqui" é apenas texto.

### Garantia 2: cada sessão pertence a um apartamento

- **O apartamento é o `user_id` da sessão do ADK**, definido em `Assistente.criar_sessao` (`aurora/assistente.py`) e nunca mais alterado: não existe rota nem tool que troque o `user_id` de uma sessão.
- **As tools leem o apartamento da sessão** por `_apartamento(tool_context)` em `aurora/tools.py`, que devolve `tool_context.user_id`. Nenhuma tool tem parâmetro de apartamento, então o modelo não tem como indicar outro.
- **O filtro está no SQL** de `aurora/banco.py`: `reservas_do_apartamento`, `visitantes_do_apartamento` e `cancelar_reserva` usam `WHERE apartamento = ?`. Para `cancelar_reserva`, uma reserva de outro apartamento se comporta como uma reserva inexistente, e a resposta é a mesma nos dois casos.
- **Disponibilidade sem dono:** `consultar_disponibilidade` e `reservar_area` devolvem só `livre`, `ocupada` ou `recusada`. `data_livre` e `criar_reserva` nunca leem o apartamento nem o código da reserva que ocupa a data.

Não depende do modelo porque os dados de outros apartamentos nunca saem do banco para uma tool: o que não chega ao modelo não chega à conversa nem aos eventos.

### Garantia 3: nada se perde no reinício

- **Conversas:** `SqliteSessionService(str(config.BANCO_SESSOES))` em `Assistente.__init__` grava sessões e eventos em `var/sessoes.db`.
- **Dados do condomínio:** `var/condominio.db`, criado por `banco.inicializar()`. A carga dos arquivos de `dados/` acontece uma única vez, controlada pela chave `semeado` da tabela `meta`; subir a API de novo não recarrega nada.
- **Dono da sessão:** a tabela `sessoes` de `condominio.db` guarda o apartamento de cada `session_id`, e `Assistente._carregar` usa esse registro para reabrir a sessão.
- **Pendências:** como `confirmacoes_pendentes()` é calculada a partir dos eventos gravados, uma confirmação pedida antes do reinício pode ser respondida depois.
- **Códigos:** `codigo` é a chave primária de `reservas`, e reservas canceladas continuam na tabela com `status = 'cancelada'`. `criar_reserva` sorteia outro código quando o SQLite acusa repetição.

### Garantia 4: o regulamento é consultado, não carregado

- **Sessão separada e descartável:** `responder_duvida()` em `aurora/regulamento.py` cria um `Runner` com `InMemorySessionService` para cada pergunta. As chamadas de `ler_capitulo` e o texto dos capítulos ficam só nessa sessão, que é descartada ao final.
- **Para a sessão do morador volta apenas a resposta**, pela tool `consultar_regulamento` em `aurora/agentes.py`, limitada a `_MAX_CARACTERES_DA_RESPOSTA`.
- **Leitura por capítulo:** `ler_capitulo` devolve um capítulo por chamada e no máximo `_MAX_CAPITULOS_POR_PERGUNTA` por pergunta. As instruções do especialista têm só o índice de títulos (`INDICE`).
- **Agente principal sem regulamento:** `_instrucao_principal` em `aurora/agentes.py` não importa nem cita o texto do regulamento.

Não depende do modelo porque o isolamento é estrutural: o agente principal e os especialistas de reservas e visitantes não têm nenhuma tool que devolva texto do regulamento.

### Garantia 5: dois moradores, uma reserva

- **Índice único parcial** em `aurora/banco.py`: `CREATE UNIQUE INDEX uq_reserva_ativa_por_area_e_data ON reservas (area, data) WHERE status = 'ativa'`.
- **`criar_reserva` não confere antes de gravar:** ela faz o `INSERT` direto. Quando duas aprovações disputam a mesma área e data, o SQLite aceita uma e a outra recebe `IntegrityError`, que vira `None`.
- **A recusa é uma resposta normal:** `reservar_area` devolve `{"situacao": "recusada", ...}` para o agente, que explica ao morador. A rota responde `200`.

Não depende do modelo nem da ordem de chegada das requisições: a exclusividade é uma restrição do banco e vale no instante da gravação, inclusive com mais de um processo da API.

## Como rodar

### Pré-requisitos

- [uv](https://docs.astral.sh/uv/) instalado. O uv baixa o Python 3.12+ se for preciso.
- Uma chave do [Google AI Studio](https://aistudio.google.com/apikey).

Não há serviço externo: o armazenamento é SQLite em arquivos locais.

### Variáveis do `.env`

```bash
cp .env.example .env
```

| Variável | Obrigatória | Descrição |
| --- | --- | --- |
| `GOOGLE_API_KEY` | sim | Chave do Google AI Studio. |
| `AURORA_MODELO` | não | Modelo Gemini dos agentes. Padrão: `gemini-3.5-flash-lite`. |
| `AURORA_DIR_DADOS` | não | Diretório dos bancos SQLite. Padrão: `./var`. |
| `AURORA_FUSO_HORARIO` | não | Fuso usado para calcular "hoje" e "amanhã". Padrão: `America/Sao_Paulo`. |

### Comandos

Instalar as dependências:

```bash
uv sync
```

Restaurar os dados iniciais (reservas e visitantes voltam ao estado de `dados/`; as sessões são apagadas):

```bash
uv run python -m aurora.restaurar
```

Subir a API em `http://localhost:8000`:

```bash
uv run uvicorn aurora.api:app --port 8000
```

Na primeira subida, se o banco ainda não existir, os dados iniciais são carregados automaticamente.

O plano gratuito do Google AI Studio limita as chamadas por minuto ao modelo. Ao atingir o limite, a API espera e tenta de novo (`modelo_padrao` em `aurora/agentes.py`), então uma resposta pode demorar até alguns minutos em vez de falhar.

### Interface de desenvolvimento (adk web)

Para conversar com os mesmos agentes pela interface do ADK, em outra porta:

```bash
uv run adk web adk_web --port 8001 --session_service_uri "sqlite:///var/adk_web.db"
```

Abra `http://localhost:8001/dev-ui/?app=assistente_aurora&userId=101`. O `userId` é o apartamento do morador: sem ele, a interface usa `user` e as tools não encontram nenhum dado. O ponto de entrada fica em `adk_web/assistente_aurora/agent.py` e usa o mesmo `var/condominio.db` da API.

### Exemplo de uso

```bash
curl -s -X POST localhost:8000/sessoes -H 'content-type: application/json' \
  -d '{"apartamento": "101"}'

curl -s -X POST localhost:8000/sessoes/$S/mensagens -H 'content-type: application/json' \
  -d '{"texto": "Reserve o salão de festas para 2030-04-20."}'

curl -s -X POST localhost:8000/sessoes/$S/confirmacoes -H 'content-type: application/json' \
  -d '{"id": "<id da confirmação pendente>", "confirmado": true}'

curl -s localhost:8000/apartamentos/101/reservas
```

Versões fixadas: Google ADK `2.9.1` (em `pyproject.toml`) e as demais dependências em `uv.lock`.
