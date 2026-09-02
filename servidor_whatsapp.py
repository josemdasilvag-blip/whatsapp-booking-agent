#!/usr/bin/env python3
"""El agente de reservas, escuchando por WhatsApp.

Se ejecuta:  python servidor_whatsapp.py

Une las tres piezas que ya existen: `whatsapp` (la Cloud API), `conversacion`
(la maquina de estados) y `agenda` (donde se guardan las reservas). Aqui no hay
logica de reservas; esto es solo el cable.

Dos rutas:

- `GET  /webhook` - el alta del webhook en Meta. Devuelve el `hub.challenge`.
- `POST /webhook` - los mensajes que entran.

**Contesta 200 a Meta antes de ponerse a trabajar.** Si se tarda en responder,
Meta da el envio por fallido y lo reintenta, y el cliente recibe la misma
respuesta tres veces.

Sin `WHATSAPP_TOKEN` en el `.env` arranca igual, en **modo seco**: procesa los
mensajes y escribe en consola lo que contestaria, sin llamar a Meta. Sirve para
probar el circuito entero antes de tener numero.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RAIZ = Path(__file__).resolve().parent


def cargar_env(ruta: Path | None = None) -> None:
    """Lee el .env sin depender de python-dotenv.

    `ENV_FILE` permite apuntar a otro fichero, o a uno que no existe para
    arrancar sin credenciales. Sin esa valvula, las pruebas creian estar en
    modo seco y acababan llamando a Meta con el token de verdad.
    """
    ruta = Path(ruta or os.environ.get("ENV_FILE") or RAIZ / ".env")
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


cargar_env()

from agenda import Agenda  # noqa: E402
from conversacion import HECHO, HUMANO, Conversacion  # noqa: E402
from whatsapp import (  # noqa: E402
    ErrorWhatsapp,
    Mensaje,
    Whatsapp,
    firma_valida,
    mensajes_entrantes,
    responder_verificacion,
)

PUERTO = int(os.environ.get("PUERTO_WEBHOOK", "8766"))
LOCAL = os.environ.get("LOCAL_ID", "casa-lola")
VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "")
APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")

# Una conversacion parada mas de esto se da por terminada: quien vuelve al dia
# siguiente empieza de cero, no a mitad de una reserva que ya no recuerda.
CADUCIDAD = timedelta(hours=6)


class Centralita:
    """Guarda una conversacion por telefono y las va caducando."""

    def __init__(self) -> None:
        self._charlas: dict[str, tuple[Conversacion, datetime]] = {}
        self._cerrojo = threading.Lock()

    def de(self, telefono: str) -> tuple[Conversacion, bool]:
        """Devuelve la conversacion y si acaba de empezar.

        Se abre una nueva cuando no habia ninguna, cuando la anterior lleva
        parada mas de `CADUCIDAD`, o cuando ya termino -- reserva cerrada o
        traspaso a un humano. Quien vuelve manana empieza de cero.
        """
        ahora = datetime.now()
        with self._cerrojo:
            guardada = self._charlas.get(telefono)
            viva = guardada is not None and ahora - guardada[1] < CADUCIDAD
            if viva and guardada[0].paso not in (HECHO, HUMANO):
                charla, nueva = guardada[0], False
            else:
                charla, nueva = self._nueva(), True
            self._charlas[telefono] = (charla, ahora)
            return charla, nueva

    def _nueva(self) -> Conversacion:
        return Conversacion(local_id=LOCAL, agenda=Agenda(), hoy=date.today())

    def olvidar(self, telefono: str) -> None:
        with self._cerrojo:
            self._charlas.pop(telefono, None)


centralita = Centralita()


def _cliente() -> Whatsapp | None:
    """None en modo seco: sin token no se llama a Meta, se escribe en consola."""
    try:
        return Whatsapp()
    except ErrorWhatsapp:
        return None


def atender(mensaje: Mensaje) -> None:
    """Un mensaje entrante, de principio a fin. Corre fuera del hilo del webhook."""
    quien = mensaje.nombre or mensaje.de
    print(f"<- {quien}: {mensaje.texto}")

    charla, nueva = centralita.de(mensaje.de)
    try:
        if nueva:
            # Primer mensaje: se saluda, pero ademas se atiende lo que ya haya
            # dicho. La gente escribe "hola, somos 4 el viernes" de una tacada,
            # y volver a preguntarle cuantos son queda a bot tonto.
            saludo = charla.saludo()
            seguimiento = charla.responder(mensaje.texto)
            # Si de ese primer mensaje no salio ningun dato -- un "hola" pelado --
            # se manda solo el saludo, que ya lleva la primera pregunta dentro.
            avanzo = charla.personas is not None or charla.momento is not None
            respuesta = f"{saludo}\n\n{seguimiento}" if avanzo else saludo
        else:
            respuesta = charla.responder(mensaje.texto)
    except Exception as exc:  # que un dato raro no tumbe el servidor
        print(f"!! fallo atendiendo a {quien}: {exc}")
        respuesta = (
            "Perdona, se me ha cruzado un cable. Me lo repites?"
        )

    print(f"-> {quien}: {respuesta}")

    cliente = _cliente()
    if cliente is None:
        print("   (modo seco: no se ha llamado a Meta)")
        return
    try:
        if mensaje.id:
            cliente.marcar_leido(mensaje.id)
        cliente.enviar_texto(mensaje.de, respuesta)
    except ErrorWhatsapp as exc:
        print(f"!! no se pudo contestar a {quien}: {exc}")


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass

    def _texto(self, codigo: int, cuerpo: str) -> None:
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self) -> None:
        ruta = urlparse(self.path)
        if ruta.path != "/webhook":
            self._texto(404, "no")
            return
        codigo, cuerpo = responder_verificacion(parse_qs(ruta.query), VERIFY_TOKEN)
        if codigo == 200:
            print("Webhook verificado por Meta.")
        else:
            print("Meta intento verificar el webhook y el token no cuadra.")
        self._texto(codigo, cuerpo)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/webhook":
            self._texto(404, "no")
            return
        largo = int(self.headers.get("Content-Length", 0))
        cuerpo = self.rfile.read(largo)

        if APP_SECRET and not firma_valida(
            cuerpo, self.headers.get("X-Hub-Signature-256"), APP_SECRET
        ):
            print("!! POST con firma invalida, descartado")
            self._texto(403, "firma invalida")
            return

        # Primero se le dice a Meta que todo bien, y despues se trabaja.
        self._texto(200, "ok")

        try:
            carga = json.loads(cuerpo or b"{}")
        except json.JSONDecodeError:
            print("!! POST con cuerpo que no es JSON")
            return
        for mensaje in mensajes_entrantes(carga):
            threading.Thread(target=atender, args=(mensaje,), daemon=True).start()


def main() -> None:
    seco = _cliente() is None
    print(f"Agente de reservas escuchando en http://localhost:{PUERTO}/webhook")
    print(f"Local: {LOCAL}")
    if not APP_SECRET:
        print("AVISO: sin WHATSAPP_APP_SECRET no se comprueba la firma de Meta.")
    if seco:
        print("MODO SECO: sin WHATSAPP_TOKEN. Se procesa todo pero no se contesta.")
    print("Ctrl+C para parar.")
    ThreadingHTTPServer(("0.0.0.0", PUERTO), Manejador).serve_forever()


if __name__ == "__main__":
    main()
