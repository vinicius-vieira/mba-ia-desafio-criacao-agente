"""Ponte entre a API e o ADK: sessões persistidas, mensagens e confirmações."""

import asyncio
import logging
from collections import defaultdict

from google.adk.apps import App, ResumabilityConfig
from google.adk.events import Event
from google.adk.flows.llm_flows.functions import REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
from google.adk.runners import Runner
from google.adk.sessions import Session
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.tool_confirmation import ToolConfirmation
from google.genai import types

from . import agentes, banco, config, tools

logger = logging.getLogger(__name__)


class SessaoInexistente(Exception):
    pass


class ConfirmacaoInexistente(Exception):
    pass


class Assistente:
    def __init__(self, modelo=None):
        banco.inicializar()
        # Garantia 3: eventos e estado das conversas ficam em SQLite, em disco.
        self.sessoes = SqliteSessionService(str(config.BANCO_SESSOES))
        self.runner = Runner(
            app=App(
                name=config.NOME_APP,
                root_agent=agentes.criar_agente_principal(modelo),
                # Com a retomada ligada, o Runner entrega a resposta de uma
                # confirmação ao agente que fez a chamada original (o
                # especialista), e não ao agente principal.
                resumability_config=ResumabilityConfig(is_resumable=True),
            ),
            session_service=self.sessoes,
        )
        # Uma execução por vez em cada sessão (mensagens e confirmações).
        self._travas: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    # --- Sessões -----------------------------------------------------------

    async def criar_sessao(self, apartamento: str) -> str:
        """Garantia 2: o apartamento vira o `user_id` da sessão, uma única vez."""
        sessao = await self.sessoes.create_session(app_name=config.NOME_APP, user_id=apartamento)
        banco.registrar_sessao(sessao.id, apartamento)
        return sessao.id

    async def _carregar(self, session_id: str) -> Session:
        apartamento = banco.apartamento_da_sessao(session_id)
        sessao = apartamento and await self.sessoes.get_session(
            app_name=config.NOME_APP, user_id=apartamento, session_id=session_id
        )
        if not sessao:
            raise SessaoInexistente(session_id)
        return sessao

    async def eventos(self, session_id: str) -> list[dict]:
        sessao = await self._carregar(session_id)
        return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in sessao.events]

    # --- Conversa ----------------------------------------------------------

    async def enviar_mensagem(self, session_id: str, texto: str) -> dict:
        async with self._travas[session_id]:
            sessao = await self._carregar(session_id)
            # O que o morador escreve entra sempre como texto. Nenhum texto vira
            # resposta de confirmação.
            mensagem = types.Content(role="user", parts=[types.Part(text=texto)])
            resposta = await self._executar(sessao, mensagem)
            return await self._montar_resposta(session_id, resposta)

    async def responder_confirmacao(self, session_id: str, id_confirmacao: str, confirmado: bool) -> dict:
        async with self._travas[session_id]:
            sessao = await self._carregar(session_id)
            # Garantia 1: só vale um id que esteja pendente NESTA sessão. Id
            # inventado, de outra sessão ou já respondido para aqui (409).
            if id_confirmacao not in {p["id"] for p in confirmacoes_pendentes(sessao.events)}:
                raise ConfirmacaoInexistente(id_confirmacao)
            # Mesmo formato que o cliente do ADK usa para responder a um
            # `adk_request_confirmation`: um FunctionResponse com o id do pedido
            # e um ToolConfirmation como conteúdo.
            mensagem = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=id_confirmacao,
                            name=REQUEST_CONFIRMATION_FUNCTION_CALL_NAME,
                            response=ToolConfirmation(confirmed=confirmado).model_dump(by_alias=True),
                        )
                    )
                ],
            )
            try:
                resposta = await self._executar(sessao, mensagem)
            except Exception:
                # A resposta do morador já está gravada na sessão. Se algo falhar
                # depois disso (por exemplo, o modelo fora do ar ao redigir o
                # texto final), o morador recebe um aviso em vez de um erro 500.
                logger.exception("Falha ao retomar a execução após a confirmação %s", id_confirmacao)
                resposta = (
                    "Recebi a sua resposta, mas não consegui concluir o atendimento agora. "
                    "Confira suas reservas e visitantes antes de tentar de novo."
                )
            return await self._montar_resposta(session_id, resposta)

    async def _executar(self, sessao: Session, mensagem: types.Content) -> str:
        resposta = ""
        async for evento in self.runner.run_async(
            user_id=sessao.user_id, session_id=sessao.id, new_message=mensagem
        ):
            texto = _texto(evento)
            if texto and evento.author != "user":
                resposta = texto
        return resposta

    async def _montar_resposta(self, session_id: str, resposta: str) -> dict:
        sessao = await self._carregar(session_id)
        return {"resposta": resposta, "confirmacoes_pendentes": confirmacoes_pendentes(sessao.events)}


def _texto(evento: Event) -> str:
    if not evento.content or not evento.content.parts:
        return ""
    return "".join(p.text for p in evento.content.parts if p.text and not p.thought).strip()


def confirmacoes_pendentes(eventos: list[Event]) -> list[dict]:
    """Pedidos de confirmação gravados na sessão que ainda não foram respondidos.

    A lista é derivada só dos eventos persistidos, por isso continua correta
    depois de reiniciar a API.
    """
    respondidas = {
        resposta.id
        for evento in eventos
        if evento.author == "user"
        for resposta in evento.get_function_responses()
        if resposta.name == REQUEST_CONFIRMATION_FUNCTION_CALL_NAME
    }
    pendentes = []
    for evento in eventos:
        for chamada in evento.get_function_calls():
            if chamada.name != REQUEST_CONFIRMATION_FUNCTION_CALL_NAME or chamada.id in respondidas:
                continue
            original = (chamada.args or {}).get("originalFunctionCall") or {}
            acao = original.get("name", "")
            pendentes.append(
                {
                    "id": chamada.id,
                    "acao": acao,
                    "detalhes": tools.detalhes_da_confirmacao(acao, original.get("args") or {}),
                }
            )
    return pendentes
