"""Cliente de la WhatsApp Cloud API: mandar, recibir y comprobar que es Meta.

Sin dependencias: `urllib` de la libreria estandar. Un servidor que hay que
mantener un ano no deberia arrastrar paquetes que no necesita.

Cuatro cosas y nada mas:

- `Whatsapp.enviar_texto(...)` manda un mensaje.
- `Whatsapp.marcar_leido(...)` pone el doble check azul.
- `responder_verificacion(...)` contesta al GET con el que Meta da de alta el
  webhook.
- `firma_valida(...)` comprueba que el POST viene de Meta y no de cualquiera
  que haya encontrado la URL.

Esa ultima es la que casi nadie implementa y es la que separa un webhook de un
agujero abierto a internet.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

# Si Meta contesta "Unsupported version", se sube este numero y ya.
VERSION_API = os.environ.get("WHATSAPP_API_VERSION", "v21.0")
BASE = "https://graph.facebook.com"


class ErrorWhatsapp(RuntimeError):
    pass


@dataclass
class Mensaje:
    """Un mensaje entrante, ya limpio."""

    de: str  # telefono del cliente, formato internacional sin +
    texto: str
    nombre: str = ""  # como se llama en su perfil de WhatsApp
    id: str = ""

    @property
    def es_texto(self) -> bool:
        return bool(self.texto.strip())


class Whatsapp:
    def __init__(
        self,
        token: str | None = None,
        phone_number_id: str | None = None,
        version: str = VERSION_API,
    ) -> None:
        self.token = token or os.environ.get("WHATSAPP_TOKEN", "")
        self.phone_number_id = phone_number_id or os.environ.get(
            "WHATSAPP_PHONE_NUMBER_ID", ""
        )
        self.version = version
        if not self.token or not self.phone_number_id:
            raise ErrorWhatsapp(
                "faltan WHATSAPP_TOKEN o WHATSAPP_PHONE_NUMBER_ID en el .env"
            )

    # -- salida ------------------------------------------------------------

    def _llamar(self, ruta: str, carga: dict) -> dict:
        url = f"{BASE}/{self.version}/{ruta}"
        peticion = urllib.request.Request(
            url,
            data=json.dumps(carga).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(peticion, timeout=20) as respuesta:
                return json.loads(respuesta.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detalle = exc.read().decode("utf-8", "replace")[:500]
            raise ErrorWhatsapp(f"Meta devolvio {exc.code}: {detalle}") from exc
        except urllib.error.URLError as exc:
            raise ErrorWhatsapp(f"no se pudo hablar con Meta: {exc.reason}") from exc

    def enviar_texto(self, destino: str, texto: str) -> dict:
        """Manda un mensaje de texto. `destino` en formato internacional.

        Ojo con la ventana de 24 horas: fuera de ella Meta solo deja mandar
        plantillas aprobadas, no texto libre. Como aqui siempre contestamos a
        alguien que acaba de escribir, no nos afecta.
        """
        return self._llamar(
            f"{self.phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": _solo_digitos(destino),
                "type": "text",
                "text": {"preview_url": False, "body": texto[:4096]},
            },
        )

    def marcar_leido(self, mensaje_id: str) -> dict:
        return self._llamar(
            f"{self.phone_number_id}/messages",
            {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": mensaje_id,
            },
        )


def _solo_digitos(numero: str) -> str:
    return "".join(c for c in str(numero) if c.isdigit())


# -- entrada ---------------------------------------------------------------


def responder_verificacion(parametros: dict, verify_token: str) -> tuple[int, str]:
    """El GET con el que Meta da de alta el webhook.

    Manda `hub.mode`, `hub.verify_token` y `hub.challenge`. Si el token cuadra,
    hay que devolver el challenge tal cual y con codigo 200. Devolver otra cosa
    -- aunque sea un 200 con texto distinto -- y Meta no da el webhook por bueno.
    """
    modo = parametros.get("hub.mode", [""])
    reto = parametros.get("hub.challenge", [""])
    token = parametros.get("hub.verify_token", [""])
    modo = modo[0] if isinstance(modo, list) else modo
    reto = reto[0] if isinstance(reto, list) else reto
    token = token[0] if isinstance(token, list) else token
    if modo == "subscribe" and token and token == verify_token:
        return 200, reto
    return 403, "token de verificacion incorrecto"


def firma_valida(cuerpo: bytes, cabecera: str | None, app_secret: str) -> bool:
    """Comprueba la cabecera `X-Hub-Signature-256` de Meta.

    Sin esto, cualquiera que descubra la URL del webhook puede inventarse
    conversaciones y reservas. Se compara con `compare_digest` para no filtrar
    informacion por el tiempo que tarda la comparacion.
    """
    if not cabecera or not app_secret:
        return False
    if not cabecera.startswith("sha256="):
        return False
    esperada = hmac.new(
        app_secret.encode("utf-8"), cuerpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperada, cabecera[len("sha256=") :])


def mensajes_entrantes(carga: dict) -> list[Mensaje]:
    """Saca los mensajes de texto del sobre que manda Meta.

    El formato viene con tres capas (`entry` -> `changes` -> `value`) y ademas
    por ahi llegan los acuses de entrega, que no son mensajes. Se ignoran: si
    esto devolviera un aviso de "entregado" como si fuera texto, el bot se
    contestaria a si mismo.
    """
    encontrados: list[Mensaje] = []
    for entrada in carga.get("entry", []):
        for cambio in entrada.get("changes", []):
            valor = cambio.get("value", {})
            perfiles = {
                c.get("wa_id", ""): c.get("profile", {}).get("name", "")
                for c in valor.get("contacts", [])
            }
            for m in valor.get("messages", []):
                if m.get("type") != "text":
                    continue
                de = m.get("from", "")
                encontrados.append(
                    Mensaje(
                        de=de,
                        texto=m.get("text", {}).get("body", ""),
                        nombre=perfiles.get(de, ""),
                        id=m.get("id", ""),
                    )
                )
    return encontrados
