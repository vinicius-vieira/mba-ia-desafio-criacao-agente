"""API HTTP do assistente do Residencial Aurora."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import banco
from .assistente import Assistente, ConfirmacaoInexistente, SessaoInexistente


class NovaSessao(BaseModel):
    apartamento: str


class Mensagem(BaseModel):
    texto: str


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    app.state.assistente = Assistente()
    yield
    await app.state.assistente.runner.close()


app = FastAPI(title="Assistente do Residencial Aurora", lifespan=ciclo_de_vida)


@app.exception_handler(SessaoInexistente)
async def sessao_inexistente(request: Request, erro: SessaoInexistente):
    return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada."})


@app.exception_handler(ConfirmacaoInexistente)
async def confirmacao_inexistente(request: Request, erro: ConfirmacaoInexistente):
    return JSONResponse(
        status_code=409,
        content={"detail": "Não existe confirmação pendente com esse id nesta sessão."},
    )


def _assistente(request: Request) -> Assistente:
    return request.app.state.assistente


# --- Conversa --------------------------------------------------------------


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao, request: Request):
    apartamento = corpo.apartamento.strip()
    if not apartamento:
        raise HTTPException(status_code=422, detail="Informe o apartamento.")
    return {"session_id": await _assistente(request).criar_sessao(apartamento)}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: Mensagem, request: Request):
    return await _assistente(request).enviar_mensagem(session_id, corpo.texto)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao, request: Request):
    return await _assistente(request).responder_confirmacao(session_id, corpo.id, corpo.confirmado)


@app.get("/sessoes/{session_id}/eventos")
async def ver_eventos(session_id: str, request: Request):
    return await _assistente(request).eventos(session_id)


# --- Verificação (leem o banco direto, sem passar pelo modelo) -------------


@app.get("/apartamentos/{apartamento}/reservas")
def reservas_do_apartamento(apartamento: str):
    return banco.reservas_do_apartamento(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
def visitantes_do_apartamento(apartamento: str):
    return banco.visitantes_do_apartamento(apartamento)
