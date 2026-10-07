"""Restaura os dados iniciais: `uv run python -m aurora.restaurar`.

Volta reservas e visitantes ao estado dos arquivos de `dados/` e apaga as
sessões de conversa, que falariam de dados que não existem mais. Usa DELETE em
vez de remover arquivos, então funciona com a API parada ou rodando.
"""

import sqlite3
from contextlib import closing

from . import banco, config


def _apagar_sessoes() -> None:
    if not config.BANCO_SESSOES.exists():
        return
    with closing(sqlite3.connect(config.BANCO_SESSOES, timeout=30)) as con:
        existentes = {l[0] for l in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        with con:
            for tabela in ("events", "sessions", "user_states", "app_states"):
                if tabela in existentes:
                    con.execute(f"DELETE FROM {tabela}")


def main() -> None:
    banco.restaurar()
    _apagar_sessoes()
    print(f"Dados restaurados em {config.DIR_VAR}")


if __name__ == "__main__":
    main()
