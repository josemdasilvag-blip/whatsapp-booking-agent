"""La conversacion de reserva, como maquina de estados.

Determinista donde se puede, que es casi todo. Una reserva tiene siete datos y
un orden; eso no necesita un modelo de lenguaje, necesita no equivocarse.

El orden lo manda `como-funciona-una-reserva.md`:
personas -> nombre -> dia y hora -> sala (solo si hay varias) -> telefono ->
correo -> campo libre.

Esta capa no sabe nada de WhatsApp ni de HTTP. Recibe texto y devuelve texto, y
por eso sirve igual para la demo web de hoy que para la Cloud API despues.

Traspaso a humano: **solo si el cliente lo pide**. Ni alergias, ni enfado, ni
palabras clave. Esa es la decision de jose_ y esta escrita a proposito.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from agenda import Agenda, DatosInvalidos
from interprete import interpretar
from motor_disponibilidad import SinHueco, UMBRAL_GRUPO_GRANDE

DIAS = [
    "lunes",
    "martes",
    "miercoles",
    "jueves",
    "viernes",
    "sabado",
    "domingo",
]

# Los de arriba son para comparar contra texto ya sin tildes (`_limpio`).
# Estos son los que ve el cliente, y ahi las tildes no son opcionales.
DIAS_BONITOS = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]

# Pasos, en el orden en que se preguntan.
PERSONAS = "personas"
NOMBRE = "nombre"
CUANDO = "cuando"
SALA = "sala"
TELEFONO = "telefono"
CORREO = "correo"
NOTAS = "notas"
CONFIRMAR = "confirmar"
ALTERNATIVAS = "alternativas"
HECHO = "hecho"
HUMANO = "humano"

SALTAR = {"no", "ninguna", "ninguno", "nada", "no gracias", "paso", "-", "no tengo"}
SI = {"si", "vale", "ok", "perfecto", "confirmo", "adelante", "correcto", "eso es"}
NO = {"no", "mejor no", "cambia", "cambiar", "espera"}


def _limpio(texto: str) -> str:
    """Sin acentos, sin mayusculas y sin signos: para comparar, no para mostrar."""
    plano = unicodedata.normalize("NFD", texto.lower())
    plano = "".join(c for c in plano if unicodedata.category(c) != "Mn")
    return plano.strip()


def _pide_persona(texto: str) -> bool:
    """La unica razon para pasar a un humano: que lo pida el cliente."""
    t = _limpio(texto)
    patrones = [
        "hablar con",
        "con una persona",
        "con alguien",
        "con un humano",
        "atencion humana",
        "pasame con",
        "quiero hablar",
        "un encargado",
        "el responsable",
    ]
    return any(p in t for p in patrones)


def _precedido_de_articulo_de_hora(t: str, posicion: int) -> bool:
    """True si el numero viene detras de "la"/"las", o sea que es una hora.

    "a la una" son las 13:00, no una persona. El camino de los digitos ya
    hacia esta comprobacion; el de las palabras se habia quedado sin ella.
    """
    antes = t[max(0, posicion - 12) : posicion]
    return "las " in antes or "la " in antes


def _numero_de_personas(texto: str) -> int | None:
    t = _limpio(texto)
    palabras = {
        "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
        "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
        "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "veinte": 20,
    }
    # Un rango escrito con palabras ("cuatro o cinco") tambien va por arriba.
    # Se exige el conector en medio a proposito: sin el, "para dos el dia
    # veinte" se leeria como un rango de 2 a 20, que es justo lo que no quiero.
    nombres = "|".join(palabras)
    par = re.search(rf"\b({nombres})\s+(?:o|a|y)\s+({nombres})\b", t)
    if par:
        return max(palabras[par.group(1)], palabras[par.group(2)])

    # Se recorre en el orden del TEXTO, no en el del diccionario: si no, "una
    # mesa para cuatro" devuelve 1, porque "una" esta antes en el dict. Y si el
    # primero es el articulo "una/uno" y detras viene otro numero, manda el otro.
    encontrados = [
        (m.start(), palabras[m.group(1)], m.group(1))
        for m in re.finditer(rf"\b({'|'.join(palabras)})\b", t)
        if not _precedido_de_articulo_de_hora(t, m.start())
    ]
    if encontrados:
        encontrados.sort()
        if encontrados[0][2] in {"una", "uno"} and len(encontrados) > 1:
            return encontrados[1][1]
        return encontrados[0][1]

    # Un rango ("4 o 5", "entre 6 y 8", "4-5") se resuelve por arriba: si
    # aparecen cinco y reservamos para cuatro, el quinto se queda de pie. Es
    # un problema cerrado, asi que lo resuelve una regla y no un modelo.
    rango = re.search(r"\b(\d{1,3})\s*(?:o|a|-|y)\s*(\d{1,3})\b", t)
    if rango:
        bajo, alto = int(rango.group(1)), int(rango.group(2))
        if 1 <= bajo <= 100 and 1 <= alto <= 100 and alto > bajo:
            antes = t[max(0, rango.start() - 12) : rango.start()]
            if "las " not in antes and "la " not in antes:
                return alto

    # Un numero suelto, pero no si es una hora ("a las 21").
    for m in re.finditer(r"\b(\d{1,3})\b", t):
        n = int(m.group(1))
        antes = t[max(0, m.start() - 12) : m.start()]
        if "las " in antes or "la " in antes:
            continue
        if 1 <= n <= 100:
            return n
    return None


HORAS_ESCRITAS = {
    "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiuna": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
}


def _hora(texto: str) -> tuple[int, int] | None:
    """Saca una hora del mensaje, escrita como la escribe la gente.

    Devuelve la hora tal cual se dijo. Convertir "las nueve" en las 21:00 no se
    hace aqui: depende de los turnos del local y eso lo sabe la conversacion.
    """
    t = _limpio(texto)

    minutos = 0
    if "y media" in t:
        minutos = 30
    elif "y cuarto" in t:
        minutos = 15

    m = re.search(r"\b(\d{1,2})[:.h](\d{2})\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    # El filtro de detras evita leer "las 4 personas" como una hora. Lo que no
    # puede hacer es descartar "las 8 de la manana", que es como habla la gente.
    m = re.search(
        r"\b(?:a las|las|sobre las|a la|a eso de las)\s+(\d{1,2})\b"
        r"(?!\s*(?:personas|pax|comensales|adultos|ninos))",
        t,
    )
    if m:
        return int(m.group(1)), minutos
    m = re.search(r"\b(\d{1,2})\s*h\b", t)
    if m:
        return int(m.group(1)), minutos
    # "a las nueve", "a las nueve y media", "sobre las diez"
    for palabra, valor in HORAS_ESCRITAS.items():
        if re.search(rf"\b(?:a las|las|sobre las|a la)\s+{palabra}\b", t):
            return valor, minutos
    return None


def _es_de_manana(texto: str) -> bool:
    """"De la manana" es una franja del dia, no el dia de despues."""
    return bool(re.search(r"\b(?:de|por) la manana\b", _limpio(texto)))


def _fecha(texto: str, hoy: date) -> date | None:
    t = _limpio(texto)
    # "a las 8 de la manana" no es "manana": si no se quita, "el viernes a las
    # 8 de la manana" acaba reservando para el dia siguiente.
    t = re.sub(r"\b(?:de|por) la manana\b", " ", t)
    if "pasado manana" in t:
        return hoy + timedelta(days=2)
    if "manana" in t:
        return hoy + timedelta(days=1)
    if "hoy" in t or "esta noche" in t:
        return hoy
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", t)
    if m:
        dia, mes = int(m.group(1)), int(m.group(2))
        anio = int(m.group(3) or hoy.year)
        if anio < 100:
            anio += 2000
        try:
            return date(anio, mes, dia)
        except ValueError:
            return None
    for i, nombre in enumerate(DIAS):
        if re.search(rf"\b{nombre}\b", t):
            adelanto = (i - hoy.weekday()) % 7
            if adelanto == 0 and "que viene" not in t:
                adelanto = 0 if "hoy" in t else 7 if "proximo" in t else 0
            if "que viene" in t or "proximo" in t:
                adelanto = adelanto or 7
            return hoy + timedelta(days=adelanto)
    return None


def _es_de_noche(texto: str) -> bool | None:
    t = _limpio(texto)
    if any(p in t for p in ("cena", "cenar", "noche")):
        return True
    if any(p in t for p in ("comida", "comer", "mediodia", "almuerzo", "almorzar")):
        return False
    return None


def _telefono_valido(texto: str) -> str | None:
    digitos = re.sub(r"[^\d+]", "", texto)
    return digitos if len(digitos.lstrip("+")) >= 9 else None


def _correo_valido(texto: str) -> str | None:
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", texto)
    return m.group(0) if m else None


@dataclass
class Conversacion:
    """Una conversacion con un cliente. Se guarda entre mensajes."""

    local_id: str
    agenda: Agenda = field(default_factory=Agenda)
    hoy: date = field(default_factory=date.today)
    paso: str = PERSONAS
    personas: int | None = None
    nombre: str = ""
    nombre_parcial: str = ""  # el nombre a secas, mientras se pide el apellido
    momento: datetime | None = None
    sala: str = ""
    telefono: str = ""
    correo: str = ""
    notas: str = ""
    alternativas: list[datetime] = field(default_factory=list)
    codigo: str = ""
    # Inyectable en tests, para que la bateria no toque la red.
    pedir_al_modelo: object = None
    _texto_interpretado: str = ""
    _interpretacion: object = None

    # -- utilidades internas ----------------------------------------------

    @property
    def ficha(self) -> dict:
        return self.agenda.ficha(self.local_id)

    @property
    def nombre_local(self) -> str:
        return self.ficha["nombre"]

    def _salas(self) -> list[str]:
        return [s["nombre"] for s in self.ficha["salas"]]

    def _interpretar(self, texto: str):
        """Puente a la capa de LLM. Solo se usa cuando lo determinista falla.

        Se cachea por mensaje: un mismo texto pasa por el paso de personas y por
        el de cuando, y no tiene sentido pagar dos llamadas para lo mismo.
        """
        if texto == self._texto_interpretado:
            return self._interpretacion
        try:
            entendido = interpretar(texto, self.hoy, pedir=self.pedir_al_modelo)
        except Exception:
            # La interpretacion es un extra. Si se rompe, se sigue sin ella.
            entendido = None
        self._texto_interpretado = texto
        self._interpretacion = entendido
        return entendido

    def _pregunta_del_paso(self) -> str:
        if self.paso == PERSONAS:
            return "Cuántas personas seríais?"
        if self.paso == NOMBRE:
            return "A nombre de quién la pongo?"
        if self.paso == CUANDO:
            return "Qué día y a qué hora?"
        if self.paso == SALA:
            salas = " o ".join(self._salas())
            return f"Prefieres {salas}?"
        if self.paso == TELEFONO:
            return "Un teléfono de contacto?"
        if self.paso == CORREO:
            return "Me dejas un correo? Si no quieres, dime que no."
        if self.paso == NOTAS:
            return "Alguna alergia o algo que deba saber la cocina? Si no, dime que no."
        return ""

    def _avanzar(self) -> str:
        """Va al siguiente dato que falte, saltandose lo que no aplica."""
        if self.personas is None:
            self.paso = PERSONAS
        elif not self.nombre:
            self.paso = NOMBRE
        elif self.momento is None:
            self.paso = CUANDO
        elif not self.sala:
            if len(self._salas()) == 1:
                self.sala = self._salas()[0]
                return self._avanzar()
            self.paso = SALA
        elif not self.telefono:
            self.paso = TELEFONO
        elif self.correo == "":
            self.paso = CORREO
        elif self.notas == "":
            self.paso = NOTAS
        else:
            self.paso = CONFIRMAR
            return self._resumen()
        return self._pregunta_del_paso()

    def _resumen(self) -> str:
        cuando = (
            f"{DIAS_BONITOS[self.momento.weekday()]} {self.momento:%d/%m} "
            f"a las {self.momento:%H:%M}"
        )
        lineas = [
            "Te leo la reserva:",
            f"- {self.personas} personas a nombre de {self.nombre}",
            f"- {cuando}, en {self.sala}",
            f"- Teléfono {self.telefono}",
        ]
        if self.correo and self.correo != "-":
            lineas.append(f"- Correo {self.correo}")
        if self.notas and self.notas != "-":
            lineas.append(f"- Nota para cocina: {self.notas}")
        deposito = self.agenda.deposito_aplicable(self.local_id, self.momento)
        if deposito:
            lineas.append(
                f"- Este turno lleva depósito: {deposito['importe_por_persona']} EUR por persona."
            )
        lineas.append("")
        lineas.append("La confirmo?")
        return "\n".join(lineas)

    def _sin_hueco(self) -> str:
        """La escalera de alternativas, numerada para que el cliente elija."""
        self.alternativas = self.agenda.alternativas(
            self.local_id, self.momento, self.personas, self.sala or self._salas()[0]
        )
        if not self.alternativas:
            self.paso = CUANDO
            self.momento = None
            return (
                "Ahí no me queda sitio, y tampoco los días siguientes a esa hora. "
                "Quieres probar otra fecha?"
            )
        self.paso = ALTERNATIVAS
        opciones = "\n".join(
            f"{i}. {DIAS_BONITOS[h.weekday()]} {h:%d/%m} a las {h:%H:%M}"
            for i, h in enumerate(self.alternativas, 1)
        )
        return (
            f"A esa hora no me queda sitio para {self.personas}. Lo más cerca que tengo:\n\n"
            f"{opciones}\n\nCuál te viene mejor? Dime el número."
        )

    def _aviso_grupo_grande(self) -> str:
        if self.personas is None or self.personas < UMBRAL_GRUPO_GRANDE:
            return ""
        if not self.ficha.get("empujar_grupos_grandes", True):
            return ""
        horas = self.agenda.horas_para_grupo(
            self.local_id,
            self.momento.date(),
            self.personas,
            self.sala or self._salas()[0],
            momento=self.momento,
        )
        if len(horas) != 2:
            return ""
        return (
            f"Para {self.personas} personas me viene mucho mejor al principio o al final "
            f"del turno ({horas[0]:%H:%M} o {horas[1]:%H:%M}): en mitad del servicio se nos "
            "parte el comedor."
        )

    # -- entrada principal -------------------------------------------------

    def saludo(self) -> str:
        return (
            f"Hola! Soy la reserva de {self.nombre_local}. "
            f"{self._pregunta_del_paso()}"
        )

    def responder(self, texto: str) -> str:
        texto = texto.strip()
        if not texto:
            return self._pregunta_del_paso()

        if _pide_persona(texto):
            self.paso = HUMANO
            return (
                "Claro, te paso con una persona del restaurante. "
                "Te escriben en cuanto puedan."
            )
        if self.paso == HUMANO:
            return "Ya le he avisado, te contestan enseguida."
        if self.paso == HECHO:
            return (
                f"Tu reserva {self.codigo} sigue en pie. Si necesitas cambiarla o "
                "anularla, dímelo."
            )

        if self.paso == ALTERNATIVAS:
            return self._elegir_alternativa(texto)
        if self.paso == CONFIRMAR:
            return self._confirmar(texto)

        return self._recoger_dato(texto)

    def _recoger_dato(self, texto: str) -> str:
        plano = _limpio(texto)

        if self.paso == PERSONAS:
            n = _numero_de_personas(texto)
            if n is None:
                # "somos 4 o 5", "un grupito de unos ocho": esto no lo saca una
                # expresion regular sin volverse loca. Que lo lea el modelo.
                entendido = self._interpretar(texto)
                n = entendido.personas if entendido else None
            if n is None:
                return "Cuántas personas seríais? Dime un número."
            self.personas = n
            # A veces lo dicen todo de una: "somos 4 el viernes a las 21".
            # Si de ahi sale algo que decir -- que no cabe, o que un grupo grande
            # va mejor a los extremos del turno -- se dice, no se descarta.
            respuesta = self._intentar_cuando(texto)
            return respuesta or self._avanzar()

        if self.paso == NOMBRE:
            dado = texto.strip()[:60]
            if not dado:
                return self._pregunta_del_paso()
            if self.nombre_parcial:
                # Ya le pedimos el apellido; esto es la segunda mitad.
                self.nombre = f"{self.nombre_parcial} {dado}".strip()[:80]
                self.nombre_parcial = ""
                return self._avanzar()
            if len(dado.split()) < 2:
                # Un nombre a secas no vale en una hoja de sala: en un turno
                # puede haber tres Joses y el camarero no sabe cual es cual.
                self.nombre_parcial = dado
                return "Y el apellido? Para no confundirte con otra reserva."
            self.nombre = dado
            return self._avanzar()

        if self.paso == CUANDO:
            respuesta = self._intentar_cuando(texto)
            if respuesta:
                return respuesta
            if self.momento is None:
                return (
                    "No te he cogido bien la fecha. Dímela así: 'viernes a las 21:30' "
                    "o '21/08 21:30'."
                )
            return self._avanzar()

        if self.paso == SALA:
            for sala in self._salas():
                if _limpio(sala) in plano:
                    self.sala = sala
                    if not self.agenda.cabe(
                        self.local_id, self.momento, self.personas, self.sala
                    ):
                        return self._sin_hueco()
                    return self._avanzar()
            # Si acabo de proponerle los extremos del turno, lo normal es que
            # conteste con una hora, no con una sala. Ignorarlo seria cerrarle
            # la reserva a una hora que no ha pedido.
            hora = _hora(texto)
            if hora and self.momento is not None:
                nuevo = self.momento.replace(hour=hora[0], minute=hora[1])
                if self.agenda.local(self.local_id).turno_de(nuevo) is not None:
                    self.momento = nuevo
                    return f"Hecho, a las {nuevo:%H:%M}. {self._pregunta_del_paso()}"
            salas = " o ".join(self._salas())
            return f"Te apunto en {salas}?"

        if self.paso == TELEFONO:
            tel = _telefono_valido(texto)
            if not tel:
                return "Ese teléfono no me cuadra. Me lo repites con el prefijo?"
            self.telefono = tel
            return self._avanzar()

        if self.paso == CORREO:
            if plano in SALTAR:
                self.correo = "-"
                return self._avanzar()
            correo = _correo_valido(texto)
            if not correo:
                return "Ese correo no me cuadra. Me lo repites, o dime que no."
            self.correo = correo
            return self._avanzar()

        if self.paso == NOTAS:
            self.notas = "-" if plano in SALTAR else texto.strip()[:200]
            return self._avanzar()

        return self._pregunta_del_paso()

    def _en_24h(self, dia: date, hora: tuple[int, int], texto: str) -> tuple[int, int]:
        """"A las nueve" en un restaurante espanol es la cena, no la manana.

        Nadie dice "a las veintiuna" al pedir mesa. Se prueban las dos lecturas
        de una hora de 1 a 11 y se queda la que cae dentro de un turno; si caen
        las dos, manda lo que haya dicho el cliente (cena o comida) y, a falta
        de pista, la de la noche.
        """
        h, m = hora
        if not 1 <= h <= 11 or _es_de_manana(texto):
            return hora
        local = self.agenda.local(self.local_id)
        candidatas = [
            (hh, m)
            for hh in (h, h + 12)
            if local.turno_de(datetime.combine(dia, datetime.min.time()).replace(hour=hh, minute=m))
            is not None
        ]
        if not candidatas:
            return hora
        if len(candidatas) == 1:
            return candidatas[0]
        noche = _es_de_noche(texto)
        if noche is False:
            return min(candidatas)
        return max(candidatas)

    def _intentar_cuando(self, texto: str) -> str:
        """Saca fecha y hora del mensaje. Devuelve texto solo si hay que repreguntar."""
        dia = _fecha(texto, self.hoy)
        hora = _hora(texto)
        franja = None
        if dia is None and hora is None:
            # Ultima parada antes de repreguntar. El modelo interpreta el texto
            # libre; la disponibilidad la sigue decidiendo el aforo, no el.
            entendido = self._interpretar(texto)
            if entendido is not None:
                dia, hora, franja = entendido.fecha, entendido.hora, entendido.franja
        if dia is None and hora is None:
            return ""
        if dia is None:
            # Solo ha dicho la hora. Si ya sabemos de que dia hablamos -- porque
            # lo dijo antes y ahora esta cambiando la hora -- se mantiene el dia
            # en vez de volver a preguntarselo.
            if self.momento is None:
                return ""
            dia = self.momento.date()
        if hora is None:
            turno_noche = _es_de_noche(texto)
            if turno_noche is None and franja is not None:
                turno_noche = franja == "cena"
            if turno_noche is None:
                self.momento = None
                return f"Para el {dia:%d/%m}, comida o cena? Y a qué hora?"
            hora = (21, 0) if turno_noche else (14, 0)
        hora = self._en_24h(dia, hora, texto)
        momento = datetime.combine(dia, datetime.min.time()).replace(
            hour=hora[0], minute=hora[1]
        )
        local = self.agenda.local(self.local_id)
        if local.turno_de(momento) is None:
            self.momento = momento
            respuesta = self._sin_hueco()
            return f"A esa hora no damos servicio. {respuesta}"
        self.momento = momento
        sala = self.sala or (self._salas()[0] if len(self._salas()) == 1 else None)
        if sala and not self.agenda.cabe(self.local_id, momento, self.personas or 1, sala):
            return self._sin_hueco()
        aviso = self._aviso_grupo_grande()
        if aviso:
            return f"{aviso}\n\n{self._avanzar()}"
        return ""

    def _elegir_alternativa(self, texto: str) -> str:
        plano = _limpio(texto)
        elegido = None
        m = re.search(r"\b([1-9])\b", plano)
        if m and 1 <= int(m.group(1)) <= len(self.alternativas):
            elegido = self.alternativas[int(m.group(1)) - 1]
        else:
            hora = _hora(texto)
            if hora:
                for h in self.alternativas:
                    if (h.hour, h.minute) == hora:
                        elegido = h
                        break
        if elegido is None:
            if plano in NO or "ninguna" in plano or "ninguno" in plano:
                self.paso = CUANDO
                self.momento = None
                self.alternativas = []
                return "Sin problema. Qué otro día u hora te viene bien?"
            # Puede que no este eligiendo de la lista, sino pidiendo otra hora:
            # "a las 21", "mejor el sabado". Tratarlo como opcion invalida es
            # lo que hace que un bot parezca sordo.
            if _fecha(texto, self.hoy) or _hora(texto):
                self.paso = CUANDO
                self.alternativas = []
                respuesta = self._intentar_cuando(texto)
                if self.momento is not None:
                    return respuesta or self._avanzar()
                return respuesta or self._pregunta_del_paso()
            return "Dime el número de la opción que prefieras, o dime otra fecha."
        self.momento = elegido
        self.alternativas = []
        return self._avanzar()

    def _confirmar(self, texto: str) -> str:
        plano = _limpio(texto)
        if plano in NO or plano.startswith("no"):
            self.paso = CUANDO
            self.momento = None
            return "Vale, lo dejamos sin cerrar. Qué día y hora prefieres?"
        if plano not in SI and not plano.startswith(("si", "vale", "ok", "confirm")):
            return "La confirmo? Dime sí o no."
        try:
            apunte = self.agenda.crear(
                local_id=self.local_id,
                momento=self.momento,
                personas=self.personas,
                nombre=self.nombre,
                telefono=self.telefono,
                sala=self.sala,
                correo="" if self.correo == "-" else self.correo,
                notas="" if self.notas == "-" else self.notas,
            )
        except SinHueco:
            return self._sin_hueco()
        except DatosInvalidos as exc:
            return f"Se me ha quedado un dato mal: {exc}"
        self.codigo = apunte.id
        self.paso = HECHO
        return (
            f"Confirmada. Tu código es {apunte.id}.\n"
            f"Os esperamos el {DIAS_BONITOS[apunte.momento.weekday()]} {apunte.momento:%d/%m} "
            f"a las {apunte.momento:%H:%M} en {apunte.sala}. Hasta luego!"
        )
