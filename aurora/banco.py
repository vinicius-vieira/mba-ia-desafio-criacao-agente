"""Dados do condomínio em SQLite: apartamentos, áreas, reservas e visitantes.

Todas as funções de reserva e visitante recebem o apartamento como argumento
obrigatório. Quem chama (as tools) sempre passa o apartamento da sessão.
"""

import json
import secrets
import sqlite3
import unicodedata
from contextlib import closing
from datetime import date

from . import config

ESQUEMA = """
CREATE TABLE IF NOT EXISTS apartamentos (
    numero  TEXT PRIMARY KEY,
    morador TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS areas (
    id   TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    taxa REAL NOT NULL
);

-- Reservas canceladas continuam na tabela: a chave primária impede que um
-- código volte a ser usado, inclusive o de uma reserva cancelada.
CREATE TABLE IF NOT EXISTS reservas (
    codigo      TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area        TEXT NOT NULL REFERENCES areas(id),
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ativa' CHECK (status IN ('ativa', 'cancelada'))
);

-- Garantia 5: no máximo uma reserva ATIVA por área e data. Quem decide a
-- disputa é o próprio SQLite, no instante do INSERT.
CREATE UNIQUE INDEX IF NOT EXISTS uq_reserva_ativa_por_area_e_data
    ON reservas (area, data) WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento TEXT NOT NULL,
    nome        TEXT NOT NULL,
    data        TEXT NOT NULL,
    UNIQUE (apartamento, nome, data)
);

-- Dono de cada sessão de conversa, gravado uma única vez na criação.
CREATE TABLE IF NOT EXISTS sessoes (
    session_id  TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def conectar() -> sqlite3.Connection:
    config.DIR_VAR.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: autocommit; as transações são abertas à mão.
    con = sqlite3.connect(config.BANCO_CONDOMINIO, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _carregar_json(nome: str) -> list[dict]:
    return json.loads((config.DIR_DADOS_INICIAIS / nome).read_text(encoding="utf-8"))


def _semear(con: sqlite3.Connection) -> None:
    """Volta o condomínio ao estado dos arquivos de dados/ (dentro de uma transação)."""
    for tabela in ("visitantes", "reservas", "areas", "apartamentos", "sessoes"):
        con.execute(f"DELETE FROM {tabela}")
    con.executemany(
        "INSERT INTO apartamentos (numero, morador) VALUES (:numero, :morador)",
        _carregar_json("apartamentos.json"),
    )
    con.executemany(
        "INSERT INTO areas (id, nome, taxa) VALUES (:id, :nome, :taxa)",
        _carregar_json("areas.json"),
    )
    con.executemany(
        "INSERT INTO reservas (codigo, apartamento, area, data) "
        "VALUES (:codigo, :apartamento, :area, :data)",
        _carregar_json("reservas.json"),
    )
    con.executemany(
        "INSERT INTO visitantes (apartamento, nome, data) VALUES (:apartamento, :nome, :data)",
        _carregar_json("visitantes.json"),
    )
    con.execute("INSERT OR REPLACE INTO meta (chave, valor) VALUES ('semeado', '1')")


def inicializar() -> None:
    """Cria o esquema e, só na primeira vez, carrega os dados iniciais."""
    with closing(conectar()) as con:
        con.executescript(ESQUEMA)
        con.execute("BEGIN IMMEDIATE")
        if not con.execute("SELECT 1 FROM meta WHERE chave = 'semeado'").fetchone():
            _semear(con)
        con.execute("COMMIT")


def restaurar() -> None:
    """Descarta as mudanças e recarrega os dados iniciais."""
    with closing(conectar()) as con:
        con.executescript(ESQUEMA)
        con.execute("BEGIN IMMEDIATE")
        _semear(con)
        con.execute("COMMIT")


# --- Sessões ---------------------------------------------------------------


def registrar_sessao(session_id: str, apartamento: str) -> None:
    with closing(conectar()) as con:
        con.execute(
            "INSERT INTO sessoes (session_id, apartamento) VALUES (?, ?)",
            (session_id, apartamento),
        )


def apartamento_da_sessao(session_id: str) -> str | None:
    with closing(conectar()) as con:
        linha = con.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return linha["apartamento"] if linha else None


# --- Validação de entradas -------------------------------------------------


def _slug(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "-".join(sem_acento.lower().replace("_", " ").replace("-", " ").split())


def listar_areas() -> list[dict]:
    with closing(conectar()) as con:
        return [dict(l) for l in con.execute("SELECT id, nome, taxa FROM areas ORDER BY rowid")]


def buscar_area(texto: str) -> dict | None:
    """Encontra a área pelo id ou pelo nome, ignorando acentos e maiúsculas."""
    alvo = _slug(texto or "")
    if not alvo:
        return None
    areas = listar_areas()
    for area in areas:
        if alvo in (area["id"], _slug(area["nome"])):
            return area
    candidatas = [
        a for a in areas if alvo in a["id"] or alvo in _slug(a["nome"]) or a["id"] in alvo
    ]
    return candidatas[0] if len(candidatas) == 1 else None


def normalizar_data(texto: str) -> str | None:
    """Devolve a data em AAAA-MM-DD ou None se o texto não for uma data válida."""
    try:
        return date.fromisoformat((texto or "").strip()).isoformat()
    except ValueError:
        return None


# --- Reservas --------------------------------------------------------------


def data_livre(area_id: str, data: str) -> bool:
    with closing(conectar()) as con:
        return (
            con.execute(
                "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
                (area_id, data),
            ).fetchone()
            is None
        )


def reservas_do_apartamento(apartamento: str) -> list[dict]:
    with closing(conectar()) as con:
        return [
            dict(l)
            for l in con.execute(
                "SELECT codigo, area, data FROM reservas "
                "WHERE apartamento = ? AND status = 'ativa' ORDER BY data, area",
                (apartamento,),
            )
        ]


def criar_reserva(apartamento: str, area_id: str, data: str) -> str | None:
    """Grava a reserva e devolve o código; devolve None se a data já está ocupada.

    Não há conferência prévia: a exclusividade vem do índice único parcial
    `uq_reserva_ativa_por_area_e_data`. Se duas gravações disputam a mesma área
    e data, o SQLite aceita uma e a outra recebe IntegrityError.
    """
    with closing(conectar()) as con:
        for _ in range(20):
            codigo = f"RSV-{secrets.token_hex(3).upper()}"
            try:
                con.execute(
                    "INSERT INTO reservas (codigo, apartamento, area, data) VALUES (?, ?, ?, ?)",
                    (codigo, apartamento, area_id, data),
                )
                return codigo
            except sqlite3.IntegrityError as erro:
                if "reservas.codigo" in str(erro):
                    continue  # código já usado (até por reserva cancelada): sorteia outro
                return None  # violou o índice único de área e data: perdeu a disputa
    raise RuntimeError("Não foi possível gerar um código de reserva único.")


def cancelar_reserva(
    apartamento: str,
    *,
    codigo: str | None = None,
    area_id: str | None = None,
    data: str | None = None,
) -> dict | None:
    """Cancela uma reserva ativa DO apartamento informado e a devolve.

    O filtro por apartamento está na própria instrução SQL: uma reserva de
    outro apartamento se comporta exatamente como uma reserva inexistente.
    """
    filtros = ["apartamento = ?", "status = 'ativa'"]
    valores: list[str] = [apartamento]
    if codigo:
        filtros.append("codigo = ?")
        valores.append(codigo.strip().upper())
    if area_id:
        filtros.append("area = ?")
        valores.append(area_id)
    if data:
        filtros.append("data = ?")
        valores.append(data)
    with closing(conectar()) as con:
        con.execute("BEGIN IMMEDIATE")
        linhas = con.execute(
            f"SELECT codigo, area, data FROM reservas WHERE {' AND '.join(filtros)}", valores
        ).fetchall()
        if len(linhas) != 1:
            con.execute("ROLLBACK")
            return None
        con.execute("UPDATE reservas SET status = 'cancelada' WHERE codigo = ?", (linhas[0]["codigo"],))
        con.execute("COMMIT")
        return dict(linhas[0])


# --- Visitantes ------------------------------------------------------------


def visitantes_do_apartamento(apartamento: str) -> list[dict]:
    with closing(conectar()) as con:
        return [
            dict(l)
            for l in con.execute(
                "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
                (apartamento,),
            )
        ]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> None:
    """Grava a autorização. Repetir a mesma autorização não duplica o registro."""
    with closing(conectar()) as con:
        con.execute(
            "INSERT OR IGNORE INTO visitantes (apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
