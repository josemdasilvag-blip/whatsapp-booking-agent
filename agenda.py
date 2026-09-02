"""Agenda: quien reserva, no solo cuanta gente cabe.

`motor_disponibilidad` sabe de aforo y de tiempo de mesa; no sabe quien viene,
como se llama ni que es celiaco. Eso lo guarda esta capa, y es lo que convierte
un calculo en algo que un restaurante puede usar.

La fuente de verdad es `datos/agenda.json`. El objeto `Local` del motor se
reconstruye desde ella en cada operacion, asi que no hay dos copias del estado
que se puedan desincronizar.

Los campos que se piden, y en que orden, estan en `como-funciona-una-reserva.md`.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from motor_disponibilidad import (
    Local,
    Reserva,
    Sala,
    SinHueco,
    Turno,
    ocupacion_minutos,
)

RAIZ = Path(__file__).resolve().parent

# Se pueden apuntar a otro sitio con variables de entorno: asi un cliente lleva
# sus propios datos sin tocar el codigo, y las pruebas no pisan los de verdad.
LOCALES = Path(os.environ.get("RESERVAS_LOCALES", RAIZ / "datos" / "locales.json"))
RESERVAS = Path(os.environ.get("RESERVAS_AGENDA", RAIZ / "datos" / "agenda.json"))

ACTIVA = "activa"
CANCELADA = "cancelada"


class LocalDesconocido(KeyError):
    pass


class ApunteDesconocido(KeyError):
    pass


class DatosInvalidos(ValueError):
    pass


def _momento(valor: str | datetime) -> datetime:
    """Acepta '2026-08-21T21:00' o '2026-08-21 21:00'. Sin zona horaria.

    El local y el cliente estan en el mismo sitio; meter husos aqui solo anade
    formas de equivocarse.
    """
    if isinstance(valor, datetime):
        return valor.replace(second=0, microsecond=0, tzinfo=None)
    try:
        limpio = str(valor).strip().replace(" ", "T")
        return datetime.fromisoformat(limpio).replace(
            second=0, microsecond=0, tzinfo=None
        )
    except ValueError as exc:
        raise DatosInvalidos(
            f"{valor!r} no es una fecha y hora valida. "
            "Usa AAAA-MM-DDTHH:MM, por ejemplo 2026-08-21T21:00."
        ) from exc


def _dia(valor: str | date) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError as exc:
        raise DatosInvalidos(f"{valor!r} no es una fecha valida. Usa AAAA-MM-DD.") from exc


def _telefono(valor: str) -> str:
    """Para comparar telefonos: solo digitos, y el + de delante si lo lleva."""
    limpio = "".join(c for c in str(valor) if c.isdigit() or c == "+")
    if len(limpio.lstrip("+")) < 6:
        raise DatosInvalidos(f"{valor!r} no parece un telefono")
    return limpio


@dataclass
class Apunte:
    """Una reserva con nombre y apellidos, tal y como se guarda en disco."""

    id: str
    local: str
    sala: str
    inicio: str  # ISO, sin zona horaria
    personas: int
    nombre: str
    telefono: str
    correo: str = ""
    notas: str = ""  # campo libre: alergias, intolerancias, lo que sea
    estado: str = ACTIVA
    creado: str = ""

    @property
    def momento(self) -> datetime:
        return datetime.fromisoformat(self.inicio)

    @property
    def fin(self) -> datetime:
        return self.momento + timedelta(minutes=ocupacion_minutos(self.personas))

    @property
    def activa(self) -> bool:
        return self.estado == ACTIVA

    def como_reserva(self) -> Reserva:
        return Reserva(self.momento, self.personas, self.sala)

    def resumen(self) -> str:
        linea = (
            f"{self.id} - {self.nombre}, {self.personas} pax, "
            f"{self.momento:%d/%m %H:%M}-{self.fin:%H:%M} en {self.sala}"
        )
        if self.notas:
            linea += f" [{self.notas}]"
        if not self.activa:
            linea += "  (CANCELADA)"
        return linea


class Agenda:
    def __init__(
        self, ruta_locales: Path = LOCALES, ruta_reservas: Path = RESERVAS
    ) -> None:
        self.ruta_locales = Path(ruta_locales)
        self.ruta_reservas = Path(ruta_reservas)
        self.config = json.loads(self.ruta_locales.read_text(encoding="utf-8"))
        self.apuntes: list[Apunte] = self._cargar()

    # -- persistencia ------------------------------------------------------

    def _cargar(self) -> list[Apunte]:
        if not self.ruta_reservas.exists():
            return []
        crudo = json.loads(self.ruta_reservas.read_text(encoding="utf-8"))
        return [Apunte(**a) for a in crudo.get("reservas", [])]

    def _guardar(self) -> None:
        self.ruta_reservas.parent.mkdir(parents=True, exist_ok=True)
        self.ruta_reservas.write_text(
            json.dumps(
                {"reservas": [asdict(a) for a in self.apuntes]},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    # -- locales -----------------------------------------------------------

    def fichas(self) -> list[dict]:
        return self.config["locales"]

    def ficha(self, local_id: str) -> dict:
        for ficha in self.fichas():
            if ficha["id"] == local_id:
                return ficha
        conocidos = ", ".join(f["id"] for f in self.fichas())
        raise LocalDesconocido(
            f"no hay ningun local con id {local_id!r}. Los que hay: {conocidos}"
        )

    def local(self, local_id: str, excluir: str | None = None) -> Local:
        """El `Local` del motor, cargado con las reservas activas.

        `excluir` deja fuera un apunte concreto: al modificar una reserva no
        puede competir consigo misma por el aforo.
        """
        ficha = self.ficha(local_id)
        turnos: dict[int, list[Turno]] = {}
        for dia_semana, lista in ficha["turnos"].items():
            turnos[int(dia_semana)] = [
                Turno(
                    t["nombre"],
                    time.fromisoformat(t["apertura"]),
                    time.fromisoformat(t["ultimo_pase"]),
                )
                for t in lista
            ]
        local = Local(
            nombre=ficha["nombre"],
            salas=[Sala(s["nombre"], s["aforo"]) for s in ficha["salas"]],
            turnos=turnos,
            empujar_grupos_grandes=ficha.get("empujar_grupos_grandes", True),
        )
        local.reservas = [
            a.como_reserva()
            for a in self.apuntes
            if a.local == local_id and a.activa and a.id != excluir
        ]
        return local

    def sala_por_defecto(self, local_id: str) -> str:
        """Si el local solo tiene una sala, al cliente no se le pregunta."""
        salas = self.ficha(local_id)["salas"]
        if len(salas) == 1:
            return salas[0]["nombre"]
        nombres = ", ".join(s["nombre"] for s in salas)
        raise DatosInvalidos(
            f"{self.ficha(local_id)['nombre']} tiene varias salas y hay que "
            f"elegir una: {nombres}"
        )

    def es_turno_fuerte(self, local_id: str, momento: datetime) -> bool:
        turno = self.local(local_id).turno_de(momento)
        if turno is None:
            return False
        clave = f"{momento.weekday()}-{turno.nombre}"
        return clave in self.ficha(local_id).get("turnos_fuertes", [])

    def deposito_aplicable(self, local_id: str, momento: datetime) -> dict | None:
        """Por defecto no hay deposito: el 99% de los locales no puede pedirlo."""
        cfg = self.ficha(local_id).get("deposito", {})
        if not cfg.get("activo"):
            return None
        if cfg.get("solo_en_turnos_fuertes", True) and not self.es_turno_fuerte(
            local_id, momento
        ):
            return None
        return cfg

    # -- consultar ---------------------------------------------------------

    def cabe(self, local_id: str, momento: datetime, personas: int, sala: str) -> bool:
        return self.local(local_id).cabe(momento, personas, sala)

    def alternativas(
        self,
        local_id: str,
        momento: datetime,
        personas: int,
        sala: str,
        maximo: int = 3,
    ) -> list[datetime]:
        return self.local(local_id).alternativas(momento, personas, sala, maximo=maximo)

    def horas_para_grupo(
        self,
        local_id: str,
        dia: date,
        personas: int,
        sala: str,
        momento: datetime | None = None,
    ) -> list[datetime]:
        """Con `momento`, los extremos se calculan sobre el turno que pidio.

        Sin el, sobre el dia entero: util para la vista del restaurante, malo
        para contestarle a un cliente que ha dicho a que hora quiere venir.
        """
        local = self.local(local_id)
        turno = local.turno_de(momento) if momento is not None else None
        return local.horas_recomendadas_grupo(dia, personas, sala, turno=turno)

    def del_dia(self, local_id: str, dia: date) -> list[Apunte]:
        return sorted(
            (
                a
                for a in self.apuntes
                if a.local == local_id and a.activa and a.momento.date() == dia
            ),
            key=lambda a: (a.momento, a.sala),
        )

    def buscar(
        self,
        telefono: str | None = None,
        nombre: str | None = None,
        local_id: str | None = None,
        incluir_canceladas: bool = False,
    ) -> list[Apunte]:
        if not telefono and not nombre:
            raise DatosInvalidos("hay que buscar por telefono o por nombre")
        cola = _telefono(telefono).lstrip("+")[-9:] if telefono else None
        aguja = nombre.strip().lower() if nombre else None
        encontrados = []
        for a in self.apuntes:
            if local_id and a.local != local_id:
                continue
            if not a.activa and not incluir_canceladas:
                continue
            if cola and _telefono(a.telefono).lstrip("+").endswith(cola):
                encontrados.append(a)
            elif aguja and aguja in a.nombre.lower():
                encontrados.append(a)
        return sorted(encontrados, key=lambda a: a.momento)

    def apunte(self, apunte_id: str) -> Apunte:
        buscado = str(apunte_id).strip().upper()
        for a in self.apuntes:
            if a.id.upper() == buscado:
                return a
        raise ApunteDesconocido(f"no hay ninguna reserva con el codigo {apunte_id!r}")

    # -- escribir ----------------------------------------------------------

    def _nuevo_id(self) -> str:
        existentes = {a.id for a in self.apuntes}
        while True:
            candidato = "R-" + secrets.token_hex(3).upper()
            if candidato not in existentes:
                return candidato

    def crear(
        self,
        local_id: str,
        momento: str | datetime,
        personas: int,
        nombre: str,
        telefono: str,
        sala: str | None = None,
        correo: str = "",
        notas: str = "",
    ) -> Apunte:
        inicio = _momento(momento)
        personas = int(personas)
        if personas < 1:
            raise DatosInvalidos("una reserva es de una persona como minimo")
        if not str(nombre).strip():
            raise DatosInvalidos("la reserva necesita un nombre")
        tel = _telefono(telefono)
        self.ficha(local_id)  # valida el local antes de tocar nada
        sala = sala or self.sala_por_defecto(local_id)
        local = self.local(local_id)
        local.sala(sala)  # valida la sala
        if local.turno_de(inicio) is None or not local.cabe(inicio, personas, sala):
            raise SinHueco(inicio, personas, sala)
        apunte = Apunte(
            id=self._nuevo_id(),
            local=local_id,
            sala=sala,
            inicio=inicio.isoformat(timespec="minutes"),
            personas=personas,
            nombre=str(nombre).strip(),
            telefono=tel,
            correo=str(correo).strip(),
            notas=str(notas).strip(),
            creado=datetime.now().isoformat(timespec="seconds"),
        )
        self.apuntes.append(apunte)
        self._guardar()
        return apunte

    def modificar(
        self,
        apunte_id: str,
        personas: int | None = None,
        momento: str | datetime | None = None,
        sala: str | None = None,
        notas: str | None = None,
    ) -> Apunte:
        """Cambiar una reserva ya hecha.

        Subir de 6 a 7 personas alarga la mesa de 1h30 a 2h, asi que un cambio
        que parece minimo puede no caber. Se comprueba con la reserva vieja
        fuera del aforo, que si no compite consigo misma.
        """
        apunte = self.apunte(apunte_id)
        if not apunte.activa:
            raise DatosInvalidos(
                f"la reserva {apunte.id} esta cancelada; hay que crear una nueva"
            )
        nuevas_personas = int(personas) if personas is not None else apunte.personas
        nuevo_inicio = _momento(momento) if momento is not None else apunte.momento
        nueva_sala = sala or apunte.sala
        if nuevas_personas < 1:
            raise DatosInvalidos("una reserva es de una persona como minimo")

        local = self.local(apunte.local, excluir=apunte.id)
        local.sala(nueva_sala)
        if local.turno_de(nuevo_inicio) is None or not local.cabe(
            nuevo_inicio, nuevas_personas, nueva_sala
        ):
            raise SinHueco(nuevo_inicio, nuevas_personas, nueva_sala)

        apunte.personas = nuevas_personas
        apunte.inicio = nuevo_inicio.isoformat(timespec="minutes")
        apunte.sala = nueva_sala
        if notas is not None:
            apunte.notas = str(notas).strip()
        self._guardar()
        return apunte

    def cancelar(self, apunte_id: str) -> Apunte:
        """No se borra: se marca. Una reserva borrada no se puede auditar."""
        apunte = self.apunte(apunte_id)
        if not apunte.activa:
            raise DatosInvalidos(f"la reserva {apunte.id} ya estaba cancelada")
        apunte.estado = CANCELADA
        self._guardar()
        return apunte
