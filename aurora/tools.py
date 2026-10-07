"""Tools de reservas e visitantes.

Nenhuma tool recebe apartamento como parâmetro: o modelo não tem como escolher
em nome de quem a tool age. O apartamento sai sempre de `_apartamento()`, que lê
o dono da sessão no contexto da execução.
"""

from typing import Optional

from google.adk.tools import FunctionTool, ToolContext

from . import banco

_FORMATO_DATA = "Informe a data no formato AAAA-MM-DD."


def _apartamento(tool_context: ToolContext) -> str:
    """Apartamento do morador autenticado.

    É o `user_id` da sessão do ADK, definido em `POST /sessoes` e imutável
    depois disso. Nada que o morador escreva ou que o modelo gere chega aqui.
    """
    return tool_context.user_id


def _aprovada(tool_context: ToolContext) -> bool:
    """True só quando a rota de confirmações aprovou ESTA chamada de tool."""
    confirmacao = tool_context.tool_confirmation
    return bool(confirmacao and confirmacao.confirmed)


def _areas_validas() -> str:
    return "Áreas disponíveis: " + ", ".join(a["id"] for a in banco.listar_areas()) + "."


# --- Reservas --------------------------------------------------------------


def listar_areas() -> dict:
    """Lista as áreas comuns que podem ser reservadas, com o id e a taxa de cada uma."""
    return {"areas": banco.listar_areas()}


def consultar_disponibilidade(area: str, data: str) -> dict:
    """Informa se uma área comum está livre em uma data.

    Args:
        area: id ou nome da área (ex.: salao-de-festas, churrasqueira, quadra).
        data: data desejada no formato AAAA-MM-DD.
    """
    area_encontrada = banco.buscar_area(area)
    if not area_encontrada:
        return {"erro": f"Área desconhecida. {_areas_validas()}"}
    data_iso = banco.normalizar_data(data)
    if not data_iso:
        return {"erro": _FORMATO_DATA}
    # Só o fato de estar livre ou ocupada sai daqui, nunca de quem é a reserva.
    livre = banco.data_livre(area_encontrada["id"], data_iso)
    return {"area": area_encontrada["id"], "data": data_iso, "situacao": "livre" if livre else "ocupada"}


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    return {"reservas": banco.reservas_do_apartamento(_apartamento(tool_context))}


def reserva_exige_confirmacao(area: str, data: str, tool_context: ToolContext) -> bool:
    """Regra de negócio 2: a reserva gera cobrança quando a área tem taxa.

    Pedido inválido ou data já ocupada não gera cobrança nenhuma, então não há o
    que confirmar: a própria tool devolve a recusa.
    """
    area_encontrada = banco.buscar_area(area)
    data_iso = banco.normalizar_data(data)
    if not area_encontrada or not data_iso:
        return False
    return area_encontrada["taxa"] > 0 and banco.data_livre(area_encontrada["id"], data_iso)


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva uma área comum para o apartamento do morador desta conversa.

    Áreas com taxa geram cobrança e só são reservadas depois que o morador
    aprova a confirmação exibida pelo aplicativo.

    Args:
        area: id ou nome da área (ex.: salao-de-festas, churrasqueira, quadra).
        data: data da reserva no formato AAAA-MM-DD.
    """
    area_encontrada = banco.buscar_area(area)
    if not area_encontrada:
        return {"erro": f"Área desconhecida. {_areas_validas()}"}
    data_iso = banco.normalizar_data(data)
    if not data_iso:
        return {"erro": _FORMATO_DATA}

    ocupada = {
        "situacao": "recusada",
        "motivo": "A área já está reservada nessa data. Sugira outra data ou outra área.",
    }
    # Garantia 1, reforçada dentro da tool: mesmo que o ADK chegue até aqui sem
    # passar pelo pedido de confirmação, área com taxa não é gravada sem a
    # aprovação vinda da rota de confirmações.
    if area_encontrada["taxa"] > 0 and not _aprovada(tool_context):
        if not banco.data_livre(area_encontrada["id"], data_iso):
            return ocupada
        return {"situacao": "nao_executada", "motivo": "A cobrança não foi aprovada pelo morador."}

    codigo = banco.criar_reserva(_apartamento(tool_context), area_encontrada["id"], data_iso)
    if codigo is None:
        return ocupada
    return {
        "situacao": "reservada",
        "codigo": codigo,
        "area": area_encontrada["id"],
        "data": data_iso,
        "taxa_cobrada": area_encontrada["taxa"],
    }


def cancelar_reserva(
    tool_context: ToolContext,
    area: Optional[str] = None,
    data: Optional[str] = None,
    codigo: Optional[str] = None,
) -> dict:
    """Cancela uma reserva do apartamento do morador desta conversa.

    Informe a área e a data da reserva, ou o código dela.

    Args:
        area: id ou nome da área reservada.
        data: data da reserva no formato AAAA-MM-DD.
        codigo: código da reserva (ex.: RSV-1234).
    """
    area_id = data_iso = None
    if area:
        area_encontrada = banco.buscar_area(area)
        if not area_encontrada:
            return {"erro": f"Área desconhecida. {_areas_validas()}"}
        area_id = area_encontrada["id"]
    if data:
        data_iso = banco.normalizar_data(data)
        if not data_iso:
            return {"erro": _FORMATO_DATA}
    if not codigo and not (area_id and data_iso):
        return {"erro": "Informe a área e a data da reserva, ou o código dela."}

    cancelada = banco.cancelar_reserva(
        _apartamento(tool_context), codigo=codigo, area_id=area_id, data=data_iso
    )
    if not cancelada:
        # Mesma resposta para reserva inexistente e para reserva de outro apartamento.
        return {
            "situacao": "nao_encontrada",
            "motivo": "O apartamento desta conversa não tem reserva ativa com esses dados.",
        }
    return {"situacao": "cancelada", **cancelada}


# --- Visitantes ------------------------------------------------------------


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados pelo apartamento do morador desta conversa."""
    return {"visitantes": banco.visitantes_do_apartamento(_apartamento(tool_context))}


def visitante_exige_confirmacao(nome: str, data: str, tool_context: ToolContext) -> bool:
    """Regra de negócio 3: toda autorização válida libera acesso e pede confirmação."""
    return bool((nome or "").strip()) and banco.normalizar_data(data) is not None


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante para o apartamento do morador desta conversa.

    A entrada só é liberada depois que o morador aprova a confirmação exibida
    pelo aplicativo.

    Args:
        nome: nome completo do visitante.
        data: data da visita no formato AAAA-MM-DD.
    """
    nome_limpo = " ".join((nome or "").split())
    if not nome_limpo:
        return {"erro": "Informe o nome do visitante."}
    data_iso = banco.normalizar_data(data)
    if not data_iso:
        return {"erro": _FORMATO_DATA}
    # Garantia 1, reforçada dentro da tool: sem aprovação, nada é gravado.
    if not _aprovada(tool_context):
        return {"situacao": "nao_executada", "motivo": "A liberação não foi aprovada pelo morador."}
    banco.autorizar_visitante(_apartamento(tool_context), nome_limpo, data_iso)
    return {"situacao": "autorizado", "nome": nome_limpo, "data": data_iso}


# --- Registro --------------------------------------------------------------

ACAO_RESERVAR = reservar_area.__name__
ACAO_AUTORIZAR = autorizar_visitante.__name__

TOOLS_RESERVAS = [
    listar_areas,
    consultar_disponibilidade,
    listar_minhas_reservas,
    FunctionTool(reservar_area, require_confirmation=reserva_exige_confirmacao),
    cancelar_reserva,
]

TOOLS_VISITANTES = [
    listar_meus_visitantes,
    FunctionTool(autorizar_visitante, require_confirmation=visitante_exige_confirmacao),
]


def detalhes_da_confirmacao(acao: str, argumentos: dict) -> dict:
    """Detalhes exibidos ao morador em `confirmacoes_pendentes`, a partir da chamada original."""
    data = banco.normalizar_data(str(argumentos.get("data", ""))) or argumentos.get("data")
    if acao == ACAO_RESERVAR:
        area = banco.buscar_area(str(argumentos.get("area", "")))
        if area:
            return {"area": area["id"], "nome_da_area": area["nome"], "data": data, "taxa": area["taxa"]}
        return {"area": argumentos.get("area"), "data": data}
    if acao == ACAO_AUTORIZAR:
        return {"nome": " ".join(str(argumentos.get("nome", "")).split()), "data": data}
    return dict(argumentos)
