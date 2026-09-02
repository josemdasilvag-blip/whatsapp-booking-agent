#!/usr/bin/env python3
"""Demo para ver el agente funcionando, sin instalar nada.

Se ejecuta:  python demo_web.py     y se abre http://localhost:8765

A la izquierda, la conversacion tal y como la veria un cliente por WhatsApp.
A la derecha, la hoja de sala del restaurante, que se actualiza en el mismo
momento en que la reserva entra.

Es una ventana sobre el mismo codigo que usa el servidor MCP: mismo motor de
aforo, misma agenda, misma maquina de estados. No hay logica duplicada aqui.

Trabaja sobre `datos/demo.json` para no tocar la agenda de verdad.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RAIZ = Path(__file__).resolve().parent
os.environ.setdefault("RESERVAS_AGENDA", str(RAIZ / "datos" / "demo.json"))

from agenda import Agenda  # noqa: E402  (despues de fijar la ruta de datos)
from conversacion import DIAS_BONITOS, Conversacion  # noqa: E402

PUERTO = 8765
LOCAL = "casa-lola"

sesiones: dict[str, Conversacion] = {}


def nueva(sesion: str) -> Conversacion:
    charla = Conversacion(local_id=LOCAL, agenda=Agenda())
    sesiones[sesion] = charla
    return charla


def hoja_del_dia(dia: date) -> dict:
    agenda = Agenda()
    apuntes = agenda.del_dia(LOCAL, dia)
    return {
        "local": agenda.ficha(LOCAL)["nombre"],
        "dia": f"{DIAS_BONITOS[dia.weekday()]} {dia:%d/%m/%Y}",
        "reservas": [
            {
                "codigo": a.id,
                "hora": f"{a.momento:%H:%M}",
                "suelta": f"{a.fin:%H:%M}",
                "personas": a.personas,
                "nombre": a.nombre,
                "sala": a.sala,
                "telefono": a.telefono,
                "notas": a.notas,
            }
            for a in apuntes
        ],
        "total_reservas": len(apuntes),
        "total_comensales": sum(a.personas for a in apuntes),
        "salas": [
            {"nombre": s["nombre"], "aforo": s["aforo"]}
            for s in agenda.ficha(LOCAL)["salas"]
        ],
    }


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silencio: la consola es para errores
        pass

    def _responder(self, carga: dict, codigo: int = 200) -> None:
        cuerpo = json.dumps(carga, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self) -> None:
        ruta = urlparse(self.path)
        if ruta.path in ("/", "/index.html"):
            html = (RAIZ / "demo" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        if ruta.path == "/api/dia":
            parametros = parse_qs(ruta.query)
            crudo = parametros.get("dia", [date.today().isoformat()])[0]
            try:
                dia = date.fromisoformat(crudo)
            except ValueError:
                dia = date.today()
            self._responder(hoja_del_dia(dia))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        ruta = urlparse(self.path)
        largo = int(self.headers.get("Content-Length", 0))
        datos = json.loads(self.rfile.read(largo) or b"{}")
        sesion = datos.get("sesion", "demo")

        if ruta.path == "/api/reiniciar":
            charla = nueva(sesion)
            self._responder({"respuesta": charla.saludo(), "paso": charla.paso})
            return

        if ruta.path == "/api/mensaje":
            charla = sesiones.get(sesion) or nueva(sesion)
            charla.agenda = Agenda()  # relee el disco por si cambio algo fuera
            try:
                respuesta = charla.responder(datos.get("texto", ""))
            except Exception as exc:  # que la demo no se caiga por un dato raro
                respuesta = f"Se me ha atragantado eso: {exc}"
            self._responder(
                {
                    "respuesta": respuesta,
                    "paso": charla.paso,
                    "codigo": charla.codigo,
                    "dia": (charla.momento or datetime.now()).date().isoformat(),
                }
            )
            return

        self.send_error(404)


def main() -> None:
    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Manejador)
    print(f"Demo en http://localhost:{PUERTO}")
    print("Ctrl+C para pararlo.")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
