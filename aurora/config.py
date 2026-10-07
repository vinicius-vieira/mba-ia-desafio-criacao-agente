"""Configuração: caminhos, modelo e variáveis de ambiente."""

import os
from pathlib import Path

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

# Estado inicial do condomínio (somente leitura).
DIR_DADOS_INICIAIS = RAIZ / "dados"
REGULAMENTO = DIR_DADOS_INICIAIS / "regulamento.md"

# Onde ficam os bancos SQLite com as mudanças feitas pelo assistente.
DIR_VAR = Path(os.getenv("AURORA_DIR_DADOS") or RAIZ / "var")
BANCO_CONDOMINIO = DIR_VAR / "condominio.db"
BANCO_SESSOES = DIR_VAR / "sessoes.db"

MODELO = os.getenv("AURORA_MODELO") or "gemini-3.5-flash-lite"
NOME_APP = "residencial_aurora"
FUSO_HORARIO = os.getenv("AURORA_FUSO_HORARIO") or "America/Sao_Paulo"
