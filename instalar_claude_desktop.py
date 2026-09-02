#!/usr/bin/env python3
"""Registra este servidor en Claude Desktop, sin tocar nada mas.

Se ejecuta:  python instalar_claude_desktop.py

Hace una copia de seguridad con la fecha en el nombre, anade solo la entrada
`mcpServers.reservas` y deja el resto de la configuracion exactamente igual.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
CLAVE = "reservas"


def ruta_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) / "Claude"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Claude"
    else:
        base = Path.home() / ".config" / "Claude"
    return base / "claude_desktop_config.json"


def main() -> int:
    fichero = ruta_config()
    if not fichero.exists():
        print(f"No encuentro la configuracion de Claude Desktop en {fichero}.")
        print("Abre Claude Desktop una vez y vuelve a ejecutar esto.")
        return 1

    cfg = json.loads(fichero.read_text(encoding="utf-8"))

    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    copia = fichero.with_suffix(f".backup-{marca}.json")
    shutil.copy2(fichero, copia)

    servidores = cfg.setdefault("mcpServers", {})
    servidores[CLAVE] = {
        "command": sys.executable,
        "args": [str(RAIZ / "servidor_mcp.py")],
        "cwd": str(RAIZ),
    }

    fichero.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"copia de seguridad : {copia.name}")
    print(f"configuracion      : {fichero}")
    print(f"anadido            : mcpServers.{CLAVE}")
    print(f"  command {sys.executable}")
    print(f"  cwd     {RAIZ}")
    print()
    print("Cierra Claude Desktop del todo y vuelve a abrirlo. Luego pidele:")
    print('  "reserva mesa para 4 el viernes a las 21:30 en Casa Lola,')
    print('   a nombre de Ana, telefono 600111222"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
