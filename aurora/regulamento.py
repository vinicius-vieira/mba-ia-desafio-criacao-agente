"""Consulta ao regulamento interno, isolada da sessão do morador.

O especialista em regulamento roda em um Runner próprio, com sessão em memória
criada e descartada a cada pergunta. O texto dos capítulos só existe nessa
sessão descartável: para a conversa do morador volta apenas a resposta curta.
"""

import re
import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types

from . import config

_APP = "aurora_regulamento"
_MAX_CAPITULOS_POR_PERGUNTA = 2
_MAX_CARACTERES_DA_RESPOSTA = 1200
_CONTADOR = "capitulos_lidos"


def _carregar_capitulos() -> dict[int, tuple[str, str]]:
    """Divide o regulamento em capítulos: {número: (título, texto)}."""
    texto = config.REGULAMENTO.read_text(encoding="utf-8")
    blocos = re.split(r"^## ", texto, flags=re.MULTILINE)[1:]
    capitulos = {}
    for numero, bloco in enumerate(blocos, start=1):
        titulo, _, corpo = bloco.partition("\n")
        capitulos[numero] = (titulo.strip(), corpo.strip())
    return capitulos


CAPITULOS = _carregar_capitulos()
INDICE = "\n".join(f"{numero}. {titulo}" for numero, (titulo, _) in CAPITULOS.items())


def ler_capitulo(numero: int, tool_context: ToolContext) -> dict:
    """Devolve o texto de um capítulo do regulamento interno.

    Args:
        numero: número do capítulo conforme o índice (1 a 14).
    """
    if numero not in CAPITULOS:
        return {"erro": f"Capítulo inexistente. Use um número de 1 a {len(CAPITULOS)}."}
    lidos = tool_context.state.get(_CONTADOR, 0)
    if lidos >= _MAX_CAPITULOS_POR_PERGUNTA:
        return {"erro": "Limite de capítulos por pergunta atingido. Responda com o que já leu."}
    tool_context.state[_CONTADOR] = lidos + 1
    titulo, texto = CAPITULOS[numero]
    return {"capitulo": titulo, "texto": texto}


def criar_especialista_regulamento(modelo) -> LlmAgent:
    return LlmAgent(
        name="especialista_regulamento",
        model=modelo,
        description="Responde dúvidas sobre o regulamento interno do Residencial Aurora.",
        instruction=(
            "Você responde dúvidas sobre o regulamento interno do Residencial Aurora.\n"
            "Índice de capítulos:\n"
            f"{INDICE}\n\n"
            "Como trabalhar:\n"
            "1. Escolha pelo índice o capítulo que trata do assunto perguntado e leia-o "
            "com a tool ler_capitulo. Leia um segundo capítulo só se o primeiro não responder.\n"
            "2. Responda em português, em até três frases, somente o que foi perguntado, "
            "com os horários, prazos e números exatos do texto e o artigo de onde vieram.\n"
            "3. Não copie trechos que não respondem à pergunta e não comente outros assuntos.\n"
            "4. Se o regulamento não tratar do assunto, diga isso. Nunca invente regra."
        ),
        tools=[ler_capitulo],
    )


async def responder_duvida(especialista: LlmAgent, pergunta: str) -> str:
    """Roda o especialista em uma sessão em memória descartável e devolve só a resposta."""
    sessoes = InMemorySessionService()
    runner = Runner(agent=especialista, app_name=_APP, session_service=sessoes)
    session_id = uuid.uuid4().hex
    await sessoes.create_session(app_name=_APP, user_id="consulta", session_id=session_id)
    resposta = ""
    async for evento in runner.run_async(
        user_id="consulta",
        session_id=session_id,
        new_message=types.Content(role="user", parts=[types.Part(text=pergunta)]),
    ):
        if evento.content and evento.content.parts and evento.is_final_response():
            texto = "".join(p.text for p in evento.content.parts if p.text and not p.thought)
            if texto.strip():
                resposta = texto.strip()
    return resposta[:_MAX_CARACTERES_DA_RESPOSTA]
