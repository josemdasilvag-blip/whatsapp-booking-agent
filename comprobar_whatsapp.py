#!/usr/bin/env python3
"""Prueba el webhook entero sin tener numero de WhatsApp todavia.

Se ejecuta:  python comprobar_whatsapp.py

Levanta `servidor_whatsapp.py` en modo seco, hace el alta del webhook igual que
la hace Meta, y despues le manda una conversacion completa de reserva con los
mismos sobres JSON y la misma firma `X-Hub-Signature-256` que manda Meta de
verdad. Al final comprueba que la reserva quedo escrita en la agenda.

Es decir: todo el camino menos el ultimo cable. El dia que haya numero, lo unico
que cambia es de donde vienen los mensajes.

Trabaja sobre una agenda temporal.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
PUERTO = 8767
VERIFY = "token-de-prueba"
SECRETO = "secreto-de-prueba"
CLIENTE = "34600111222"

CONVERSACION = [
    "hola",
    "somos 4",
    "Ana Perez",
    "el viernes a las 21:30",
    "terraza",
    "600111222",
    "no",
    "celiaca",
    "si",
]


def sobre(texto: str) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "000000000000000",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "contacts": [
                                    {"wa_id": CLIENTE, "profile": {"name": "Ana"}}
                                ],
                                "messages": [
                                    {
                                        "from": CLIENTE,
                                        "id": f"wamid.{time.time_ns()}",
                                        "type": "text",
                                        "text": {"body": texto},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


def firmar(cuerpo: bytes) -> str:
    return "sha256=" + hmac.new(SECRETO.encode(), cuerpo, hashlib.sha256).hexdigest()


def post(cuerpo: bytes) -> int:
    peticion = urllib.request.Request(
        f"http://127.0.0.1:{PUERTO}/webhook",
        data=cuerpo,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": firmar(cuerpo),
        },
        method="POST",
    )
    with urllib.request.urlopen(peticion, timeout=10) as r:
        return r.status


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="webhook-"))
    shutil.copy(RAIZ / "datos" / "locales.json", tmp / "locales.json")
    entorno = os.environ | {
        "RESERVAS_LOCALES": str(tmp / "locales.json"),
        "RESERVAS_AGENDA": str(tmp / "agenda.json"),
        "WEBHOOK_VERIFY_TOKEN": VERIFY,
        "WHATSAPP_APP_SECRET": SECRETO,
        "PUERTO_WEBHOOK": str(PUERTO),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        # Sin esto el servidor cargaria el .env de verdad y saldria del modo
        # seco por la puerta de atras, llamando a Meta con el token bueno.
        "ENV_FILE": str(tmp / "no-hay-env"),
    }
    # Modo seco a proposito: sin token no se llama a Meta.
    entorno.pop("WHATSAPP_TOKEN", None)
    entorno.pop("WHATSAPP_PHONE_NUMBER_ID", None)

    proc = subprocess.Popen(
        [sys.executable, "servidor_whatsapp.py"],
        cwd=RAIZ,
        env=entorno,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    salida: queue.Queue = queue.Queue()

    def leer():
        for linea in proc.stdout:
            salida.put(linea.rstrip())

    threading.Thread(target=leer, daemon=True).start()

    fallos: list[str] = []
    lineas: list[str] = []
    try:
        # Esperar a que abra el puerto
        for _ in range(50):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{PUERTO}/webhook?hub.mode=ping", timeout=1
                )
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.2)

        # 1. El alta del webhook, como la hace Meta
        url = (
            f"http://127.0.0.1:{PUERTO}/webhook?hub.mode=subscribe"
            f"&hub.verify_token={VERIFY}&hub.challenge=987654321"
        )
        with urllib.request.urlopen(url, timeout=10) as r:
            reto = r.read().decode()
        print(f"alta del webhook : {r.status} -> {reto}")
        if reto != "987654321":
            fallos.append(f"el alta devolvio {reto!r} en vez del challenge")

        # 2. Un token equivocado tiene que rebotar
        malo = (
            f"http://127.0.0.1:{PUERTO}/webhook?hub.mode=subscribe"
            f"&hub.verify_token=inventado&hub.challenge=987654321"
        )
        try:
            urllib.request.urlopen(malo, timeout=10)
            fallos.append("acepto un verify_token inventado")
            print("token falso     : ACEPTADO (mal)")
        except urllib.error.HTTPError as exc:
            print(f"token falso      : rechazado con {exc.code}")
            if exc.code != 403:
                fallos.append(f"el token falso devolvio {exc.code}, no 403")

        # 3. Un POST sin firma tiene que rebotar
        try:
            peticion = urllib.request.Request(
                f"http://127.0.0.1:{PUERTO}/webhook",
                data=sobre("hola"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(peticion, timeout=10)
            fallos.append("acepto un POST sin firma")
            print("POST sin firma   : ACEPTADO (mal)")
        except urllib.error.HTTPError as exc:
            print(f"POST sin firma   : rechazado con {exc.code}")

        # 4. La conversacion entera
        print("\nconversacion:")
        for mensaje in CONVERSACION:
            post(sobre(mensaje))
            time.sleep(0.35)  # dar tiempo al hilo que atiende

        time.sleep(1.0)
        while not salida.empty():
            lineas.append(salida.get())
        for linea in lineas:
            if linea.startswith(("<-", "->", "!!")):
                print("  " + linea.replace("\n", " "))

        # El POST sin firma del paso 3 tiene que dejar rastro: ese `!!` es la
        # prueba de que el rechazo funciono, no un fallo.
        esperado = "firma invalida"
        inesperados = [
            l for l in lineas if l.startswith("!!") and esperado not in l
        ]
        if not any(esperado in l for l in lineas):
            fallos.append("el POST sin firma no dejo rastro en el registro")
        for l in inesperados:
            fallos.append(f"el servidor registro un error: {l[3:]}")

        # 5. La reserva tiene que estar escrita
        fichero = tmp / "agenda.json"
        if not fichero.exists():
            fallos.append("no se guardo ninguna reserva")
        else:
            guardado = json.loads(fichero.read_text(encoding="utf-8"))
            reservas = guardado.get("reservas", [])
            print(f"\nagenda           : {len(reservas)} reserva(s)")
            if len(reservas) != 1:
                fallos.append(f"esperaba 1 reserva y hay {len(reservas)}")
            else:
                r = reservas[0]
                print(
                    f"  {r['id']} {r['nombre']}, {r['personas']} pax, "
                    f"{r['inicio']} en {r['sala']} [{r['notas']}]"
                )
                if r["personas"] != 4:
                    fallos.append(f"guardo {r['personas']} personas en vez de 4")
                if not r["inicio"].endswith("21:30"):
                    fallos.append(f"guardo la hora {r['inicio']}")
                if r["sala"] != "Terraza":
                    fallos.append(f"guardo la sala {r['sala']}")
                if r["notas"] != "celiaca":
                    fallos.append("perdio la nota para cocina")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("FALLA:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("Webhook, firma y conversacion completa por WhatsApp: todo correcto.")
    print("Falta solo el numero. El resto del camino ya esta probado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
