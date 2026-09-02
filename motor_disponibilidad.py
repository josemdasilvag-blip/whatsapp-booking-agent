"""Motor de disponibilidad por aforo con mesas dobladas.

El criterio operativo que implementa esto esta en `como-funciona-una-reserva.md`.
Lo resumido:

- No hay mapa de mesas. Hay **aforo** por sala y las mesas se doblan y triplican.
- Lo que decide cuantos pases caben es el **tiempo de ocupacion**, y ese tiempo
  depende del tamano del grupo.
- Cuando no hay hueco se ofrece una escalera: otra hora del mismo turno, luego
  otra hora del mismo dia, y por ultimo otro dia a la hora que pidio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

# Tiempo de mesa segun el tamano del grupo, en minutos.
# Tramos de jose_: 2-3 -> 1h15, 4-6 -> 1h30, 7-10 -> 2h.
TRAMOS_OCUPACION: list[tuple[int, int]] = [(3, 75), (6, 90), (10, 120)]
OCUPACION_GRUPO_GRANDE = 120

# A partir de aqui es "grupo grande" y se empuja a los extremos del turno.
UMBRAL_GRUPO_GRANDE = 7

# Cuanto nos alejamos de la hora pedida al sugerir alternativas.
MARGEN_ALTERNATIVAS = timedelta(hours=1)

PASO = timedelta(minutes=15)


def ocupacion_minutos(personas: int) -> int:
    """Cuanto tiempo ocupa la mesa un grupo de este tamano."""
    if personas < 1:
        raise ValueError("una reserva es de una persona como minimo")
    for tope, minutos in TRAMOS_OCUPACION:
        if personas <= tope:
            return minutos
    return OCUPACION_GRUPO_GRANDE


@dataclass(frozen=True)
class Turno:
    """Una franja de servicio: la comida o la cena de un dia concreto."""

    nombre: str
    apertura: time
    ultimo_pase: time  # ultima hora a la que se acepta que entre gente

    def contiene(self, momento: time) -> bool:
        return self.apertura <= momento <= self.ultimo_pase

    def horas(self, dia: date) -> list[datetime]:
        """Todas las horas a las que se puede empezar en este turno."""
        inicio = datetime.combine(dia, self.apertura)
        fin = datetime.combine(dia, self.ultimo_pase)
        horas, actual = [], inicio
        while actual <= fin:
            horas.append(actual)
            actual += PASO
        return horas


@dataclass(frozen=True)
class Sala:
    nombre: str
    aforo: int


@dataclass(frozen=True)
class Reserva:
    inicio: datetime
    personas: int
    sala: str

    @property
    def fin(self) -> datetime:
        return self.inicio + timedelta(minutes=ocupacion_minutos(self.personas))

    def solapa(self, desde: datetime, hasta: datetime) -> bool:
        return self.inicio < hasta and desde < self.fin


@dataclass
class Local:
    """Un restaurante.

    `empujar_grupos_grandes` es False en los locales con espacio de sobra: ahi no
    hace falta mover a nadie a los extremos del turno.
    """

    nombre: str
    salas: list[Sala]
    turnos: dict[int, list[Turno]]  # weekday() -> turnos de ese dia
    empujar_grupos_grandes: bool = True
    reservas: list[Reserva] = field(default_factory=list)

    def sala(self, nombre: str) -> Sala:
        for s in self.salas:
            if s.nombre == nombre:
                return s
        raise KeyError(f"el local no tiene una sala llamada {nombre!r}")

    @property
    def tiene_varias_salas(self) -> bool:
        """Si solo hay una, al cliente no se le pregunta por la sala."""
        return len(self.salas) > 1

    def turnos_de(self, dia: date) -> list[Turno]:
        return self.turnos.get(dia.weekday(), [])

    def turno_de(self, momento: datetime) -> Turno | None:
        for turno in self.turnos_de(momento.date()):
            if turno.contiene(momento.time()):
                return turno
        return None

    # -- ocupacion ---------------------------------------------------------

    def ocupadas(self, sala: str, desde: datetime, hasta: datetime) -> int:
        """Maximo de comensales sentados a la vez en esa ventana.

        Se mira solo en los instantes en que algo cambia: el maximo de una
        funcion escalonada siempre cae en un punto de inicio.
        """
        solapadas = [
            r for r in self.reservas if r.sala == sala and r.solapa(desde, hasta)
        ]
        if not solapadas:
            return 0
        instantes = {desde} | {r.inicio for r in solapadas if r.inicio >= desde}
        return max(
            sum(r.personas for r in solapadas if r.inicio <= t < r.fin)
            for t in instantes
        )

    def cabe(self, inicio: datetime, personas: int, sala: str) -> bool:
        if self.turno_de(inicio) is None:
            return False
        fin = inicio + timedelta(minutes=ocupacion_minutos(personas))
        return self.ocupadas(sala, inicio, fin) + personas <= self.sala(sala).aforo

    # -- alternativas ------------------------------------------------------

    def _libres_en_turno(
        self, dia: date, turno: Turno, personas: int, sala: str
    ) -> list[datetime]:
        return [h for h in turno.horas(dia) if self.cabe(h, personas, sala)]

    def alternativas(
        self,
        pedido: datetime,
        personas: int,
        sala: str,
        dias_a_mirar: int = 7,
        maximo: int = 3,
    ) -> list[datetime]:
        """La escalera de jose_, y en este orden.

        1. Otra hora del mismo turno, a una hora arriba o abajo como mucho.
        2. Otra hora del mismo dia, o sea el otro turno.
        3. Otro dia, a la hora que pidio originalmente.
        """
        propuestas: list[datetime] = []

        turno_pedido = self.turno_de(pedido)
        if turno_pedido is not None:
            # Dentro de su propio turno no hay corte: si lo unico libre de la
            # cena son tres horas mas tarde, eso se le ofrece igual. Manda la
            # cercania a la hora que pidio, nada mas.
            libres = self._libres_en_turno(
                pedido.date(), turno_pedido, personas, sala
            )
            propuestas += sorted(libres, key=lambda h: (abs(h - pedido), h))

        if len(propuestas) < maximo:
            for turno in self.turnos_de(pedido.date()):
                if turno == turno_pedido:
                    continue
                propuestas += self._libres_en_turno(pedido.date(), turno, personas, sala)

        # Otro dia, cerca de la hora que pidio. Aqui el margen si corta: no tiene
        # sentido mandar a quien queria cenar a comer el jueves.
        if len(propuestas) < maximo:
            for adelanto in range(1, dias_a_mirar + 1):
                otro_dia = (pedido + timedelta(days=adelanto)).date()
                candidatos = [
                    h
                    for turno in self.turnos_de(otro_dia)
                    for h in self._libres_en_turno(otro_dia, turno, personas, sala)
                    if abs(h - datetime.combine(otro_dia, pedido.time()))
                    <= MARGEN_ALTERNATIVAS
                ]
                if candidatos:
                    referencia = datetime.combine(otro_dia, pedido.time())
                    propuestas.append(
                        min(candidatos, key=lambda h: (abs(h - referencia), h))
                    )

        vistas, unicas = set(), []
        for p in propuestas:
            if p not in vistas:
                vistas.add(p)
                unicas.append(p)
        return unicas[:maximo]

    def horas_recomendadas_grupo(
        self, dia: date, personas: int, sala: str, turno: Turno | None = None
    ) -> list[datetime]:
        """Para grupos grandes: los extremos del turno, no el centro.

        Un grupo grande en mitad del turno fuerte parte el comedor. Se le empuja
        a la primera hora o a la ultima. En un local con espacio de sobra no hace
        falta y se le ofrece lo que haya.

        `turno` acota los extremos a ese servicio, y no es un detalle: sin acotar,
        a quien pide cenar se le acaba ofreciendo la primera hora de la comida,
        que es el extremo del dia pero no el de su turno.
        """
        libres: list[datetime] = []
        for t in [turno] if turno is not None else self.turnos_de(dia):
            libres += self._libres_en_turno(dia, t, personas, sala)
        if not libres:
            return []
        if personas < UMBRAL_GRUPO_GRANDE or not self.empujar_grupos_grandes:
            return libres
        return [libres[0], libres[-1]]

    # -- escribir ----------------------------------------------------------

    def reservar(self, inicio: datetime, personas: int, sala: str) -> Reserva:
        if not self.cabe(inicio, personas, sala):
            raise SinHueco(inicio, personas, sala)
        reserva = Reserva(inicio, personas, sala)
        self.reservas.append(reserva)
        return reserva

    def modificar_personas(self, reserva: Reserva, personas: int) -> Reserva:
        """Cambiar el numero de comensales.

        Ojo: subir de 6 a 7 alarga la mesa de 1h30 a 2h, asi que puede no caber
        aunque solo se anada una persona. Se comprueba sin la reserva vieja
        delante, que si no compite consigo misma.
        """
        self.reservas.remove(reserva)
        try:
            return self.reservar(reserva.inicio, personas, reserva.sala)
        except SinHueco:
            self.reservas.append(reserva)
            raise

    def cancelar(self, reserva: Reserva) -> None:
        self.reservas.remove(reserva)


class SinHueco(Exception):
    def __init__(self, inicio: datetime, personas: int, sala: str):
        super().__init__(
            f"no caben {personas} el {inicio:%d/%m a las %H:%M} en {sala}"
        )
        self.inicio, self.personas, self.sala = inicio, personas, sala
