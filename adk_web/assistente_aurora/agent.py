"""Ponto de entrada para o `adk web` (interface de desenvolvimento do ADK).

Usa os mesmos agentes, tools e banco da API. No adk web, o apartamento do
morador é o "User ID" da interface: abra com `?userId=101` na URL.
"""

import sys
from pathlib import Path

# O adk web coloca só a pasta `adk_web/` no caminho de importação.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from google.adk.apps import App, ResumabilityConfig  # noqa: E402

from aurora import agentes, banco  # noqa: E402

banco.inicializar()

root_agent = agentes.criar_agente_principal()
app = App(
    name="assistente_aurora",
    root_agent=root_agent,
    resumability_config=ResumabilityConfig(is_resumable=True),
)
