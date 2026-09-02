#!/usr/bin/env python3
"""Habla JSON-RPC con el servidor por stdio, como haria un cliente de verdad.

Se ejecuta:  python comprobar_mcp.py

Las pruebas de pytest demuestran que las cuentas salen. Esto demuestra otra cosa
distinta: que el servidor arranca, hace el handshake MCP, anuncia sus siete
herramientas y aguanta una conversacion completa de reserva -- crear, ver que ya
no cabe, buscar por telefono, sacar la hoja del dia, modificar y cancelar.

Trabaja sobre una agenda temporal, nunca sobre `datos/agenda.json`.

Nota de por que esta escrito asi: manda las peticiones **de una en una** y espera
cada respuesta antes de la siguiente. Volcarlas todas de golpe y cerrar la
entrada (`communicate`) hace que el servidor conteste solo las que le dio tiempo
a leer antes del EOF, y las ultimas se pierden sin error. Un cliente real
mantiene la conexion abierta; esto tambien.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
ESPERA = 30  # segundos por respuesta

HERRAMIENTAS = {
    "reservas_listar_locales",
    "reservas_consultar_disponibilidad",
    "reservas_crear",
    "reservas_buscar",
    "reservas_modificar",
    "reservas_cancelar",
    "reservas_agenda_del_dia",
}

# Viernes 21 de agosto de 2026, cena.
VIERNES = "2026-08-21T21:00"


class Cliente:
    """Un cliente MCP minimo por stdio, con la conexion abierta de principio a fin."""

    def __init__(self, entorno: dict) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "servidor_mcp.py"],
            cwd=RAIZ,
            env=entorno,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.buzon: queue.Queue = queue.Queue()
        self.errores: list[str] = []
        threading.Thread(target=self._leer_salida, daemon=True).start()
        threading.Thread(target=self._leer_errores, daemon=True).start()
        self.siguiente_id = 0

    def _leer_salida(self) -> None:
        for linea in self.proc.stdout:
            linea = linea.strip()
            if linea.startswith("{"):
                try:
                    self.buzon.put(json.loads(linea))
                except json.JSONDecodeError:
                    pass

    def _leer_errores(self) -> None:
        for linea in self.proc.stderr:
            if linea.strip():
                self.errores.append(linea.rstrip())

    def _enviar(self, mensaje: dict) -> None:
        self.proc.stdin.write(json.dumps(mensaje) + "\n")
        self.proc.stdin.flush()

    def _esperar(self, id_: int) -> dict:
        while True:
            try:
                msg = self.buzon.get(timeout=ESPERA)
            except queue.Empty:
                raise TimeoutError(f"el servidor no contesto a la peticion {id_}")
            if msg.get("id") == id_:
                return msg

    def peticion(self, metodo: str, params: dict | None = None) -> dict:
        self.siguiente_id += 1
        id_ = self.siguiente_id
        self._enviar(
            {"jsonrpc": "2.0", "id": id_, "method": metodo, "params": params or {}}
        )
        return self._esperar(id_)

    def notificar(self, metodo: str) -> None:
        self._enviar({"jsonrpc": "2.0", "method": metodo})

    def handshake(self) -> str:
        respuesta = self.peticion(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "comprobar_mcp", "version": "0"},
            },
        )
        self.notificar("notifications/initialized")
        return respuesta.get("result", {}).get("serverInfo", {}).get("name", "")

    def herramientas(self) -> list[str]:
        r = self.peticion("tools/list")
        return [t["name"] for t in r.get("result", {}).get("tools", [])]

    def usar(self, herramienta: str, **argumentos) -> tuple[str, bool]:
        """Los argumentos van anidados bajo `params`, que es como los declara el
        esquema que anuncia el servidor: cada herramienta recibe un unico objeto
        validado por pydantic."""
        r = self.peticion(
            "tools/call",
            {"name": herramienta, "arguments": {"params": argumentos}},
        )
        resultado = r.get("result", {})
        texto = "\n".join(
            p.get("text", "")
            for p in resultado.get("content", [])
            if p.get("type") == "text"
        )
        return texto, bool(resultado.get("isError"))

    def json(self, herramienta: str, **argumentos) -> dict:
        texto, es_error = self.usar(herramienta, response_format="json", **argumentos)
        if es_error:
            return {"_error": texto}
        try:
            return json.loads(texto)
        except json.JSONDecodeError:
            return {"_error": texto}

    def cerrar(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="reservas-mcp-"))
    shutil.copy(RAIZ / "datos" / "locales.json", tmp / "locales.json")
    entorno = os.environ | {
        "RESERVAS_LOCALES": str(tmp / "locales.json"),
        "RESERVAS_AGENDA": str(tmp / "agenda.json"),
        "PYTHONIOENCODING": "utf-8",
    }

    fallos: list[str] = []
    cliente = Cliente(entorno)
    try:
        servidor = cliente.handshake()
        herramientas = cliente.herramientas()
        print(f"servidor     : {servidor or '(ninguno)'}")
        print(f"herramientas : {len(herramientas)}")
        for h in sorted(herramientas):
            print(f"  - {h}")
        print()
        if servidor != "reservas_mcp":
            fallos.append(f"el servidor se llama {servidor!r}, no 'reservas_mcp'")
        faltan = HERRAMIENTAS - set(herramientas)
        if faltan:
            fallos.append(f"no anuncia: {sorted(faltan)}")

        # 1. El comedor entero para un solo grupo de 80.
        grande = cliente.json(
            "reservas_crear",
            local_id="casa-lola",
            momento=VIERNES,
            personas=80,
            nombre="Grupo grande",
            telefono="+34600111222",
            sala="Comedor",
            notas="dos celiacos",
        )
        print(f"crear 80 pax : {grande.get('codigo', 'FALLO')}  fin {grande.get('fin', '?')}")
        if not grande.get("codigo"):
            fallos.append(f"no se pudo crear la reserva de 80: {grande.get('_error', '')}")
        if grande.get("notas") != "dos celiacos":
            fallos.append("la nota para cocina no llego")

        # 2. Con el comedor lleno no cabe nadie mas: escalera de alternativas.
        lleno = cliente.json(
            "reservas_consultar_disponibilidad",
            local_id="casa-lola",
            momento=VIERNES,
            personas=4,
            sala="Comedor",
        )
        alternativas = lleno.get("alternativas", [])
        print(f"lleno        : cabe={lleno.get('cabe')}  alternativas={len(alternativas)}")
        if lleno.get("cabe") is not False:
            fallos.append("dice que cabe con el comedor lleno")
        if not alternativas:
            fallos.append("no ofrece ninguna alternativa")
        elif not all(a.startswith("2026-08-21") for a in alternativas):
            fallos.append(f"las alternativas se van de dia: {alternativas}")

        # 3. Una de 6 en la terraza, que si cabe.
        pequena = cliente.json(
            "reservas_crear",
            local_id="casa-lola",
            momento=VIERNES,
            personas=6,
            nombre="Ana Perez",
            telefono="600333444",
            sala="Terraza",
        )
        print(f"crear 6 pax  : {pequena.get('codigo', 'FALLO')}  fin {pequena.get('fin', '?')}")
        if not pequena.get("codigo"):
            fallos.append(f"no se pudo crear la de 6: {pequena.get('_error', '')}")
        if not str(pequena.get("fin", "")).endswith("22:30"):
            fallos.append(f"6 pax ocupan 1h30 y acaban a {pequena.get('fin')}")

        # 4. El telefono, escrito de otra forma, encuentra la reserva.
        busqueda = cliente.json("reservas_buscar", telefono="+34 600 33 34 44")
        print(f"buscar tel   : {busqueda.get('encontradas')} encontrada(s)")
        if busqueda.get("encontradas") != 1:
            fallos.append("el telefono con espacios no encuentra la reserva")

        # 5. La hoja del dia suma los dos grupos.
        dia = cliente.json(
            "reservas_agenda_del_dia", local_id="casa-lola", dia="2026-08-21"
        )
        print(
            f"hoja del dia : {dia.get('total_reservas')} reservas, "
            f"{dia.get('total_comensales')} pax"
        )
        if dia.get("total_comensales") != 86:
            fallos.append(f"la hoja del dia suma {dia.get('total_comensales')}, no 86")

        # 6. De 6 a 7 personas: la mesa pasa de 1h30 a 2h.
        if pequena.get("codigo"):
            cambiada = cliente.json(
                "reservas_modificar", codigo=pequena["codigo"], personas=7
            )
            print(
                f"6 -> 7 pax   : {cambiada.get('personas', '?')} pax, "
                f"fin {cambiada.get('fin', '?')}"
            )
            if cambiada.get("personas") != 7:
                fallos.append(f"no aplico el cambio a 7: {cambiada.get('_error', '')}")
            if not str(cambiada.get("fin", "")).endswith("23:00"):
                fallos.append(
                    f"7 pax ocupan 2h y acaban a {cambiada.get('fin')}: "
                    "no recalculo el tiempo de mesa"
                )

            # 7. Cancelar marca, no borra.
            anulada = cliente.json("reservas_cancelar", codigo=pequena["codigo"])
            print(f"cancelar     : estado {anulada.get('estado', '?')}")
            if anulada.get("estado") != "cancelada":
                fallos.append("no dejo la reserva como cancelada")

            guardado = json.loads((tmp / "agenda.json").read_text(encoding="utf-8"))
            if len(guardado["reservas"]) != 2:
                fallos.append("cancelar borro la fila en vez de marcarla")

        # 8. Un codigo inventado da error, no una reserva fantasma.
        fantasma = cliente.json("reservas_cancelar", codigo="R-NOEXISTE")
        if "_error" not in fantasma:
            fallos.append("acepto un codigo de reserva que no existe")
    except TimeoutError as exc:
        fallos.append(str(exc))
    finally:
        if cliente.errores:
            print("\nstderr:")
            print("\n".join(cliente.errores)[:800])
        cliente.cerrar()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if fallos:
        print("FALLA:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("Handshake, siete herramientas y ciclo completo de reserva: todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
