#!/usr/bin/env python3
"""Servidor MCP de reservas de restaurante.

Le da a un asistente las siete cosas que hace falta saber hacer con una reserva:
listar locales, mirar si cabe, crear, buscar, modificar, cancelar y sacar la
hoja del dia. Debajo estan `motor_disponibilidad` (aforo y tiempo de mesa) y
`agenda` (quien reserva y donde se guarda).

El criterio operativo que implementa esta en `como-funciona-una-reserva.md`, y
no sale de ninguna documentacion: es de un director de operaciones de
restauracion. Lo importante de ahi, y que casi ningun agente de reservas hace:

- El aforo se dobla y se triplica. Manda el tiempo de mesa, no un mapa de mesas.
- Cuando no hay hueco hay una escalera de alternativas, y tiene un orden.
- El deposito viene desactivado. El 99% de los locales no puede pedirlo.
- No hay reglas de traspaso a humano por sentimiento ni por palabras clave.
  Se pasa a una persona solo si el cliente lo pide.

Transporte: stdio. Se arranca con `python servidor_mcp.py`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from enum import Enum
from typing import Optional

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from agenda import (
    Agenda,
    Apunte,
    ApunteDesconocido,
    DatosInvalidos,
    LocalDesconocido,
    _dia,
    _momento,
)
from motor_disponibilidad import UMBRAL_GRUPO_GRANDE, SinHueco, ocupacion_minutos

mcp = MCPServer("reservas_mcp")

ERRORES = (LocalDesconocido, ApunteDesconocido, DatosInvalidos, SinHueco, KeyError, ValueError)


def _agenda() -> Agenda:
    """Una agenda nueva por llamada: el estado vive en disco, no en memoria.

    Asi dos clientes MCP a la vez no se pisan una copia vieja de las reservas.
    """
    return Agenda()


def _solo_lee(titulo: str) -> ToolAnnotations:
    return ToolAnnotations(
        title=titulo,
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )


def _escribe(titulo: str, destructiva: bool = False) -> ToolAnnotations:
    return ToolAnnotations(
        title=titulo,
        read_only_hint=False,
        destructive_hint=destructiva,
        idempotent_hint=False,
        open_world_hint=False,
    )


class Formato(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


def _fallo(mensaje: str) -> str:
    return f"Error: {mensaje}"


def _ficha_apunte(a: Apunte) -> dict:
    return {
        "codigo": a.id,
        "local": a.local,
        "sala": a.sala,
        "inicio": a.inicio,
        "fin": a.fin.isoformat(timespec="minutes"),
        "personas": a.personas,
        "nombre": a.nombre,
        "telefono": a.telefono,
        "correo": a.correo,
        "notas": a.notas,
        "estado": a.estado,
    }


def _salida(carga: dict | list, lineas: list[str], formato: Formato) -> str:
    if formato is Formato.JSON:
        return json.dumps(carga, ensure_ascii=False, indent=2)
    return "\n".join(lineas)


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_format: Formato = Field(
        default=Formato.MARKDOWN, description="'markdown' para leer, 'json' para encadenar"
    )


# -- 1. locales -----------------------------------------------------------


class ListarLocalesInput(Base):
    pass


@mcp.tool(name="reservas_listar_locales", annotations=_solo_lee("Listar locales"))
async def reservas_listar_locales(params: ListarLocalesInput) -> str:
    """Lista los restaurantes, sus salas con aforo y sus turnos.

    Llamala la primera si no sabes que `local_id` existen, o si necesitas saber
    si un local tiene varias salas: cuando solo tiene una, al cliente NO se le
    pregunta por la sala.

    Args:
        params (ListarLocalesInput):
            - response_format (Formato): 'markdown' o 'json'

    Returns:
        str: Locales, salas, aforos, turnos por dia y si piden deposito.

        Esquema JSON:
        {
          "locales": [{
            "id": str, "nombre": str, "ciudad": str,
            "varias_salas": bool,
            "salas": [{"nombre": str, "aforo": int}],
            "turnos": {"0".."6": [{"nombre": str, "apertura": str, "ultimo_pase": str}]},
            "deposito_activo": bool
          }]
        }

    Ejemplos:
        - Usala cuando: "en que restaurantes puedo reservar?"
        - No la uses cuando: ya tienes el local_id y quieres horas libres
          (usa reservas_consultar_disponibilidad).
    """
    try:
        agenda = _agenda()
        dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        carga, lineas = {"locales": []}, []
        for f in agenda.fichas():
            carga["locales"].append(
                {
                    "id": f["id"],
                    "nombre": f["nombre"],
                    "ciudad": f.get("ciudad", ""),
                    "varias_salas": len(f["salas"]) > 1,
                    "salas": f["salas"],
                    "turnos": f["turnos"],
                    "deposito_activo": bool(f.get("deposito", {}).get("activo")),
                }
            )
            lineas.append(f"## {f['nombre']}  (`{f['id']}`)")
            salas = ", ".join(f"{s['nombre']} ({s['aforo']} pax)" for s in f["salas"])
            lineas.append(f"- Salas: {salas}")
            if len(f["salas"]) == 1:
                lineas.append("  (una sola sala: no hay que preguntarle al cliente)")
            for d in range(7):
                turnos = f["turnos"].get(str(d))
                if not turnos:
                    lineas.append(f"- {dias[d]}: cerrado")
                else:
                    detalle = "; ".join(
                        f"{t['nombre']} {t['apertura']}-{t['ultimo_pase']}" for t in turnos
                    )
                    lineas.append(f"- {dias[d]}: {detalle}")
            dep = f.get("deposito", {})
            lineas.append(
                f"- Deposito: {'si, ' + str(dep.get('importe_por_persona')) + ' EUR/pax' if dep.get('activo') else 'no'}"
            )
            lineas.append("")
        return _salida(carga, lineas, params.response_format)
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 2. disponibilidad ----------------------------------------------------


class DisponibilidadInput(Base):
    local_id: str = Field(description="Id del local, de reservas_listar_locales")
    momento: str = Field(description="Dia y hora pedidos, AAAA-MM-DDTHH:MM")
    personas: int = Field(ge=1, description="Cuantos comensales")
    sala: Optional[str] = Field(
        default=None, description="Solo si el local tiene varias salas"
    )


@mcp.tool(
    name="reservas_consultar_disponibilidad",
    annotations=_solo_lee("Consultar disponibilidad"),
)
async def reservas_consultar_disponibilidad(params: DisponibilidadInput) -> str:
    """Mira si cabe un grupo a esa hora, y si no, que ofrecerle.

    No basta con un si o un no. Cuando no cabe, devuelve la escalera en el orden
    que manda en hostelaria: primero otra hora de SU MISMO turno (ahi no hay
    corte: si lo unico libre de esa cena son tres horas mas tarde, se ofrece
    igual), luego el otro turno del mismo dia, y por ultimo otro dia a una hora
    parecida a la que pidio.

    Para grupos de 7 o mas avisa de que van a los extremos del turno, porque un
    grupo grande en mitad del servicio parte el comedor. En locales con espacio
    de sobra ese empuje esta desactivado y no avisa.

    Args:
        params (DisponibilidadInput):
            - local_id (str), momento (str), personas (int), sala (str|None)

    Returns:
        str: Si cabe, cuanto ocupa la mesa, alternativas y aviso de deposito.

        Esquema JSON:
        {
          "cabe": bool, "momento": str, "personas": int, "sala": str,
          "minutos_de_mesa": int, "libera_a_las": str,
          "turno_fuerte": bool,
          "alternativas": [str],
          "horas_para_grupo_grande": [str],
          "deposito": {"importe_por_persona": int} | null
        }

    Ejemplos:
        - Usala cuando: "hay sitio para 6 el sabado a las 21:30?"
        - No la uses cuando: ya sabes que cabe y quieres cerrarla
          (usa reservas_crear, que vuelve a comprobar el hueco).
    """
    try:
        agenda = _agenda()
        momento = _momento(params.momento)
        sala = params.sala or agenda.sala_por_defecto(params.local_id)
        agenda.local(params.local_id).sala(sala)

        cabe = agenda.cabe(params.local_id, momento, params.personas, sala)
        minutos = ocupacion_minutos(params.personas)
        libera = momento + timedelta(minutes=minutos)
        fuerte = agenda.es_turno_fuerte(params.local_id, momento)
        deposito = agenda.deposito_aplicable(params.local_id, momento)

        alternativas = (
            []
            if cabe
            else agenda.alternativas(params.local_id, momento, params.personas, sala)
        )
        extremos = []
        if params.personas >= UMBRAL_GRUPO_GRANDE and agenda.ficha(params.local_id).get(
            "empujar_grupos_grandes", True
        ):
            extremos = agenda.horas_para_grupo(
                params.local_id, momento.date(), params.personas, sala, momento=momento
            )

        carga = {
            "cabe": cabe,
            "momento": momento.isoformat(timespec="minutes"),
            "personas": params.personas,
            "sala": sala,
            "minutos_de_mesa": minutos,
            "libera_a_las": libera.isoformat(timespec="minutes"),
            "turno_fuerte": fuerte,
            "alternativas": [h.isoformat(timespec="minutes") for h in alternativas],
            "horas_para_grupo_grande": [h.isoformat(timespec="minutes") for h in extremos],
            "deposito": deposito,
        }

        lineas = [
            f"**{'Hay sitio' if cabe else 'No hay sitio'}** para {params.personas} "
            f"el {momento:%d/%m a las %H:%M} en {sala}.",
            f"- Ocupa la mesa {minutos} min: la suelta a las {libera:%H:%M}.",
        ]
        if fuerte:
            lineas.append("- Es turno fuerte.")
        if not cabe and alternativas:
            lineas.append("- Alternativas, por orden:")
            lineas += [f"  {i}. {h:%d/%m %H:%M}" for i, h in enumerate(alternativas, 1)]
        elif not cabe:
            lineas.append("- Sin alternativas en los proximos 7 dias.")
        if extremos:
            horas = " o ".join(f"{h:%H:%M}" for h in extremos)
            lineas.append(
                f"- Grupo de {params.personas}: ofrecerle los extremos del turno ({horas}), "
                "no el centro."
            )
        if deposito:
            lineas.append(
                f"- Este turno pide deposito: {deposito['importe_por_persona']} EUR por persona."
            )
        return _salida(carga, lineas, params.response_format)
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 3. crear -------------------------------------------------------------


class CrearInput(Base):
    local_id: str = Field(description="Id del local")
    momento: str = Field(description="Dia y hora, AAAA-MM-DDTHH:MM")
    personas: int = Field(ge=1, description="Cuantos comensales")
    nombre: str = Field(description="A nombre de quien")
    telefono: str = Field(description="Telefono de contacto")
    sala: Optional[str] = Field(default=None, description="Solo si hay varias salas")
    correo: str = Field(default="", description="Correo, si lo da")
    notas: str = Field(
        default="",
        description="Campo libre: alergias, intolerancias, silla de bebe, lo que sea",
    )


@mcp.tool(name="reservas_crear", annotations=_escribe("Crear reserva"))
async def reservas_crear(params: CrearInput) -> str:
    """Cierra la reserva y devuelve el codigo con el que se busca despues.

    Vuelve a comprobar el hueco antes de escribir, asi que es seguro llamarla
    aunque haya pasado un rato desde la consulta de disponibilidad. Si ya no
    cabe, falla y no deja nada a medias.

    Lo que se pregunta para reservar, y nada mas: personas, nombre, dia y hora,
    sala (solo si hay varias), telefono, correo y campo libre. Una alergia NO es
    motivo para pasar a un humano: se recoge en `notas` y llega a cocina igual.

    Args:
        params (CrearInput): local_id, momento, personas, nombre, telefono y,
            opcionales, sala, correo y notas.

    Returns:
        str: La reserva creada con su codigo.

        Esquema JSON:
        {"codigo": str, "local": str, "sala": str, "inicio": str, "fin": str,
         "personas": int, "nombre": str, "telefono": str, "correo": str,
         "notas": str, "estado": str, "deposito": {...}|null}

    Ejemplos:
        - Usala cuando: "cierra mesa para 4 a nombre de Ana el viernes a las 21".
        - No la uses cuando: el cliente aun no ha dado nombre y telefono.
    """
    try:
        agenda = _agenda()
        apunte = agenda.crear(
            local_id=params.local_id,
            momento=params.momento,
            personas=params.personas,
            nombre=params.nombre,
            telefono=params.telefono,
            sala=params.sala,
            correo=params.correo,
            notas=params.notas,
        )
        deposito = agenda.deposito_aplicable(params.local_id, apunte.momento)
        carga = _ficha_apunte(apunte) | {"deposito": deposito}
        lineas = [
            f"Reserva **{apunte.id}** confirmada.",
            f"- {apunte.nombre}, {apunte.personas} pax",
            f"- {apunte.momento:%A %d/%m a las %H:%M} en {apunte.sala} "
            f"(mesa hasta las {apunte.fin:%H:%M})",
            f"- Telefono {apunte.telefono}"
            + (f" - {apunte.correo}" if apunte.correo else ""),
        ]
        if apunte.notas:
            lineas.append(f"- Nota para cocina: {apunte.notas}")
        if deposito:
            lineas.append(
                f"- Deposito de este turno: {deposito['importe_por_persona']} EUR por persona."
            )
        return _salida(carga, lineas, params.response_format)
    except SinHueco as exc:
        return _fallo(
            f"{exc}. Consulta alternativas con reservas_consultar_disponibilidad."
        )
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 4. buscar ------------------------------------------------------------


class BuscarInput(Base):
    telefono: Optional[str] = Field(default=None, description="Telefono del cliente")
    nombre: Optional[str] = Field(default=None, description="Nombre o parte del nombre")
    local_id: Optional[str] = Field(default=None, description="Limitar a un local")
    incluir_canceladas: bool = Field(default=False, description="Ver tambien las anuladas")


@mcp.tool(name="reservas_buscar", annotations=_solo_lee("Buscar reservas"))
async def reservas_buscar(params: BuscarInput) -> str:
    """Encuentra las reservas de un cliente por telefono o por nombre.

    Es el paso previo obligatorio a modificar o cancelar: de aqui sale el codigo.
    El telefono se compara por los ultimos 9 digitos, asi que da igual como este
    escrito (+34 600 11 22 33 y 600112233 son el mismo).

    Args:
        params (BuscarInput): telefono y/o nombre; al menos uno.

    Returns:
        str: Reservas encontradas, de la mas proxima a la mas lejana.

        Esquema JSON:
        {"encontradas": int, "reservas": [{"codigo": str, ...}]}

    Ejemplos:
        - Usala cuando: "quiero cambiar mi reserva, soy Ana, 600112233".
        - No la uses cuando: quieres ver el servicio entero de un dia
          (usa reservas_agenda_del_dia).
    """
    try:
        agenda = _agenda()
        encontradas = agenda.buscar(
            telefono=params.telefono,
            nombre=params.nombre,
            local_id=params.local_id,
            incluir_canceladas=params.incluir_canceladas,
        )
        carga = {
            "encontradas": len(encontradas),
            "reservas": [_ficha_apunte(a) for a in encontradas],
        }
        if not encontradas:
            lineas = ["Ninguna reserva con esos datos."]
        else:
            lineas = [f"{len(encontradas)} reserva(s):"] + [
                f"- {a.resumen()}" for a in encontradas
            ]
        return _salida(carga, lineas, params.response_format)
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 5. modificar ---------------------------------------------------------


class ModificarInput(Base):
    codigo: str = Field(description="Codigo de la reserva, de reservas_buscar")
    personas: Optional[int] = Field(default=None, ge=1, description="Nuevo numero de pax")
    momento: Optional[str] = Field(default=None, description="Nueva fecha y hora")
    sala: Optional[str] = Field(default=None, description="Nueva sala")
    notas: Optional[str] = Field(default=None, description="Reemplaza el campo libre")


@mcp.tool(name="reservas_modificar", annotations=_escribe("Modificar reserva"))
async def reservas_modificar(params: ModificarInput) -> str:
    """Cambia personas, hora, sala o notas de una reserva que ya existe.

    Ojo con el numero de comensales: pasar de 6 a 7 sube el tiempo de mesa de
    1h30 a 2h, asi que una persona mas puede no caber aunque parezca un cambio
    minimo. Se comprueba con la reserva vieja fuera del aforo, para que no
    compita consigo misma, y si no cabe no se toca nada.

    Args:
        params (ModificarInput): codigo, y lo que se quiera cambiar.

    Returns:
        str: La reserva como queda.

        Esquema JSON: igual que reservas_crear, sin el bloque de deposito.

    Ejemplos:
        - Usala cuando: "somos dos mas" o "podemos pasarlo a las 22?".
        - No la uses cuando: la reserva esta cancelada; ahi se crea una nueva.
    """
    try:
        agenda = _agenda()
        apunte = agenda.modificar(
            apunte_id=params.codigo,
            personas=params.personas,
            momento=params.momento,
            sala=params.sala,
            notas=params.notas,
        )
        lineas = [
            f"Reserva **{apunte.id}** actualizada.",
            f"- {apunte.nombre}, {apunte.personas} pax",
            f"- {apunte.momento:%A %d/%m a las %H:%M} en {apunte.sala} "
            f"(mesa hasta las {apunte.fin:%H:%M})",
        ]
        if apunte.notas:
            lineas.append(f"- Nota para cocina: {apunte.notas}")
        return _salida(_ficha_apunte(apunte), lineas, params.response_format)
    except SinHueco as exc:
        return _fallo(f"{exc}. La reserva se queda como estaba.")
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 6. cancelar ----------------------------------------------------------


class CancelarInput(Base):
    codigo: str = Field(description="Codigo de la reserva, de reservas_buscar")


@mcp.tool(name="reservas_cancelar", annotations=_escribe("Cancelar reserva", destructiva=True))
async def reservas_cancelar(params: CancelarInput) -> str:
    """Anula una reserva y libera su aforo.

    No borra la fila: la marca como cancelada. Una reserva borrada no se puede
    auditar despues, y el historico de anulaciones es justo lo que hace falta
    para medir no-shows.

    Args:
        params (CancelarInput): codigo de la reserva.

    Returns:
        str: Confirmacion de la anulacion.

        Esquema JSON: {"codigo": str, "estado": "cancelada", ...}

    Ejemplos:
        - Usala cuando: "al final no vamos, cancela".
        - No la uses cuando: solo cambia la hora (usa reservas_modificar).
    """
    try:
        apunte = _agenda().cancelar(params.codigo)
        lineas = [
            f"Reserva **{apunte.id}** cancelada.",
            f"- Eran {apunte.personas} pax el {apunte.momento:%d/%m a las %H:%M}.",
            "- El aforo queda libre.",
        ]
        return _salida(_ficha_apunte(apunte), lineas, params.response_format)
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


# -- 7. hoja del dia ------------------------------------------------------


class DiaInput(Base):
    local_id: str = Field(description="Id del local")
    dia: str = Field(description="Dia a mirar, AAAA-MM-DD")


@mcp.tool(name="reservas_agenda_del_dia", annotations=_solo_lee("Hoja del dia"))
async def reservas_agenda_del_dia(params: DiaInput) -> str:
    """La hoja de sala de un dia: todo lo reservado, por hora, con las alergias.

    Es la vista que usa el restaurante, no el cliente. Suma comensales por turno
    para ver de un vistazo como viene el servicio.

    Args:
        params (DiaInput): local_id y dia.

    Returns:
        str: Reservas del dia ordenadas por hora, con totales.

        Esquema JSON:
        {"local": str, "dia": str, "total_reservas": int, "total_comensales": int,
         "reservas": [{"codigo": str, ...}]}

    Ejemplos:
        - Usala cuando: "que tenemos el sabado?" o "cuanta gente entra hoy?".
        - No la uses cuando: buscas la reserva de un cliente concreto
          (usa reservas_buscar).
    """
    try:
        agenda = _agenda()
        dia = _dia(params.dia)
        ficha = agenda.ficha(params.local_id)
        apuntes = agenda.del_dia(params.local_id, dia)
        comensales = sum(a.personas for a in apuntes)
        carga = {
            "local": ficha["nombre"],
            "dia": dia.isoformat(),
            "total_reservas": len(apuntes),
            "total_comensales": comensales,
            "reservas": [_ficha_apunte(a) for a in apuntes],
        }
        lineas = [f"## {ficha['nombre']} - {dia:%A %d/%m/%Y}"]
        if not apuntes:
            lineas.append("Sin reservas.")
        else:
            lineas.append(f"{len(apuntes)} reservas, {comensales} comensales.")
            lineas.append("")
            for a in apuntes:
                lineas.append(f"- {a.resumen()}  tel {a.telefono}")
            con_notas = [a for a in apuntes if a.notas]
            if con_notas:
                lineas.append("")
                lineas.append("**Para cocina:**")
                lineas += [f"- {a.nombre} ({a.momento:%H:%M}): {a.notas}" for a in con_notas]
        return _salida(carga, lineas, params.response_format)
    except ERRORES as exc:
        return _fallo(str(exc).strip("'"))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
