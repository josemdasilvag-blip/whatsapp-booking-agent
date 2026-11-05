"""Capa de interpretacion: saca datos de un mensaje escrito en libre.

Por que existe. Los parsers de `conversacion.py` son deterministas y cubren lo
normal: "somos 4", "el viernes a las 21", "manana a las 2". Lo que no cubren es
como escribe la gente de verdad cuando no sigue el guion: "somos 4 o 5, mejor
tarde", "para el finde que viene si puede ser, mejor cenar tarde".

Donde encaja. **Solo se llama cuando lo determinista ya ha fallado.** El modelo
interpreta, no decide: devuelve campos, y a partir de ahi manda la misma logica
de aforo y turnos de siempre. Un modelo no puede decir que una mesa esta libre.

Como se cae. Si no hay clave, si la red falla, si tarda demasiado, si el JSON
viene roto o si un valor es absurdo, esto devuelve None y el agente se comporta
exactamente igual que antes de existir este fichero. Degradar bien es el
requisito, no un extra: es preferible repreguntar a inventarse una reserva.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

URL_API = "https://api.anthropic.com/v1/messages"
VERSION_API = "2023-06-01"
MODELO = os.environ.get("MODELO_INTERPRETE", "claude-haiku-4-5-20251001")
SEGUNDOS_MAXIMO = float(os.environ.get("TIMEOUT_INTERPRETE", "8"))

# Limites de cordura. Lo que caiga fuera se descarta entero: si el modelo se
# inventa 400 personas, lo que hay que hacer es repreguntar, no reservar.
MAX_PERSONAS = 50
MAX_DIAS_VISTA = 365

INSTRUCCIONES = """Extraes datos de reserva de restaurante de mensajes en español.

Devuelves SOLO un objeto JSON, sin explicar nada y sin ```:
{"personas": entero o null, "fecha": "AAAA-MM-DD" o null, "hora": "HH:MM" o null, "franja": "comida" o "cena" o null}

Reglas:
- Lo que no diga el mensaje va a null. No inventes. No rellenes por parecer util.
- Si da un rango de personas ("4 o 5", "entre 6 y 8"), coge el numero mayor:
  para el aforo lo prudente es reservar por arriba.
- "mediodia", "comer", "almorzar" -> franja "comida". "cenar", "por la noche" -> "cena".
- "tarde" a secas NO es una hora. Si dice "mejor tarde" hablando de cenar, es
  franja "cena" y hora null.
- Fechas relativas se resuelven contra la fecha de hoy que te doy.
- "el finde" sin mas -> el sabado siguiente.
- Si el mensaje no habla de una reserva, todo va a null."""


@dataclass
class Interpretacion:
    """Lo que el modelo ha entendido. Todo opcional, todo ya validado."""

    personas: int | None = None
    fecha: date | None = None
    hora: tuple[int, int] | None = None
    franja: str | None = None

    @property
    def vacia(self) -> bool:
        return (
            self.personas is None
            and self.fecha is None
            and self.hora is None
            and self.franja is None
        )


def _pedir_al_modelo(texto: str, hoy: date, clave: str) -> str:
    cuerpo = json.dumps(
        {
            "model": MODELO,
            "max_tokens": 200,
            "system": INSTRUCCIONES,
            "messages": [
                {"role": "user", "content": f"Hoy es {hoy:%Y-%m-%d}.\nMensaje: {texto}"}
            ],
        }
    ).encode("utf-8")
    peticion = urllib.request.Request(
        URL_API,
        data=cuerpo,
        headers={
            "x-api-key": clave,
            "anthropic-version": VERSION_API,
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=SEGUNDOS_MAXIMO) as respuesta:
        datos = json.loads(respuesta.read().decode("utf-8"))
    return datos["content"][0]["text"]


def _json_del_texto(bruto: str) -> dict | None:
    """Saca el objeto JSON aunque venga con ``` o con texto alrededor."""
    if not bruto:
        return None
    trozo = re.search(r"\{.*\}", bruto, re.DOTALL)
    if trozo is None:
        return None
    try:
        datos = json.loads(trozo.group(0))
    except json.JSONDecodeError:
        return None
    return datos if isinstance(datos, dict) else None


def _personas_validas(valor) -> int | None:
    if isinstance(valor, bool) or not isinstance(valor, int):
        return None
    return valor if 1 <= valor <= MAX_PERSONAS else None


def _fecha_valida(valor, hoy: date) -> date | None:
    if not isinstance(valor, str):
        return None
    try:
        dia = date.fromisoformat(valor)
    except ValueError:
        return None
    # Ni el pasado ni un ano vista. Las dos cosas son sintoma de que el modelo
    # ha resuelto mal una fecha relativa, y una reserva a un ano no existe.
    if dia < hoy or dia > hoy + timedelta(days=MAX_DIAS_VISTA):
        return None
    return dia


def _hora_valida(valor) -> tuple[int, int] | None:
    if not isinstance(valor, str):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", valor.strip())
    if m is None:
        return None
    hora, minuto = int(m.group(1)), int(m.group(2))
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    return (hora, minuto)


def _franja_valida(valor) -> str | None:
    if not isinstance(valor, str):
        return None
    limpio = valor.strip().lower()
    return limpio if limpio in {"comida", "cena"} else None


def interpretar(texto: str, hoy: date, pedir=None) -> Interpretacion | None:
    """Interpreta un mensaje libre. Devuelve None si no se puede, sin levantar.

    `pedir` permite inyectar el cliente en los tests, para que la bateria no
    dependa de la red ni de tener clave.
    """
    if not texto or not texto.strip():
        return None

    if pedir is None:
        clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not clave:
            return None

        def pedir(t, h):
            return _pedir_al_modelo(t, h, clave)

    try:
        bruto = pedir(texto, hoy)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    except (KeyError, IndexError, ValueError):
        # Respuesta con una forma que no esperabamos. Mismo tratamiento.
        return None

    datos = _json_del_texto(bruto if isinstance(bruto, str) else "")
    if datos is None:
        return None

    entendido = Interpretacion(
        personas=_personas_validas(datos.get("personas")),
        fecha=_fecha_valida(datos.get("fecha"), hoy),
        hora=_hora_valida(datos.get("hora")),
        franja=_franja_valida(datos.get("franja")),
    )
    return None if entendido.vacia else entendido
