"""Pruebas de la maquina de estados de la conversacion.

Lo que se comprueba aqui no es que las cuentas salgan -- eso ya esta en las
otras dos tandas -- sino que el bot no le cierre al cliente una reserva
distinta de la que ha pedido.
"""

import json
from datetime import date

import pytest

from agenda import Agenda
from conversacion import CONFIRMAR, CUANDO, HECHO, HUMANO, Conversacion

MIERCOLES = date(2026, 8, 19)  # el "hoy" de todas las pruebas

CONFIG = {
    "locales": [
        {
            "id": "prueba",
            "nombre": "Casa Prueba",
            "empujar_grupos_grandes": True,
            "salas": [
                {"nombre": "Comedor", "aforo": 40},
                {"nombre": "Terraza", "aforo": 12},
            ],
            "turnos": {
                str(d): [
                    {"nombre": "Comida", "apertura": "13:00", "ultimo_pase": "15:30"},
                    {"nombre": "Cena", "apertura": "20:00", "ultimo_pase": "23:00"},
                ]
                for d in range(1, 7)
            },
            "turnos_fuertes": ["4-Cena"],
            "deposito": {"activo": False},
        },
        {
            "id": "sencillo",
            "nombre": "Sitio de una sala",
            "empujar_grupos_grandes": False,
            "salas": [{"nombre": "Salon", "aforo": 60}],
            "turnos": {
                str(d): [{"nombre": "Cena", "apertura": "20:00", "ultimo_pase": "23:00"}]
                for d in range(7)
            },
            "turnos_fuertes": [],
            "deposito": {"activo": False},
        },
    ]
}


@pytest.fixture
def charla(tmp_path):
    locales = tmp_path / "locales.json"
    locales.write_text(json.dumps(CONFIG), encoding="utf-8")
    agenda = Agenda(ruta_locales=locales, ruta_reservas=tmp_path / "agenda.json")
    return Conversacion(local_id="prueba", agenda=agenda, hoy=MIERCOLES)


def guion(c, *mensajes):
    return [c.responder(m) for m in mensajes]


# -- el camino normal -----------------------------------------------------


def test_reserva_completa_de_principio_a_fin(charla):
    guion(
        charla,
        "somos 4",
        "Ana Perez",
        "el viernes a las 21:30",
        "terraza",
        "600111222",
        "no",
        "celiaca",
    )
    assert charla.paso == CONFIRMAR
    final = charla.responder("si")
    assert charla.paso == HECHO
    assert charla.codigo in final
    guardada = charla.agenda.apunte(charla.codigo)
    assert guardada.personas == 4
    assert guardada.sala == "Terraza"
    assert guardada.notas == "celiaca"
    assert f"{guardada.momento:%H:%M}" == "21:30"


def test_lo_dice_todo_de_una_vez(charla):
    charla.responder("somos 4 el viernes a las 21:30")
    assert charla.personas == 4
    assert f"{charla.momento:%d/%m %H:%M}" == "21/08 21:30"


def test_con_una_sola_sala_no_se_pregunta(charla):
    charla.local_id = "sencillo"
    guion(charla, "somos 2", "Luis Gomez", "manana a las 21:00", "600111222", "no", "no")
    assert charla.paso == CONFIRMAR
    assert charla.sala == "Salon"


# -- lo que no debe pasar -------------------------------------------------


def test_si_acepta_la_hora_que_le_propongo_se_la_apunto(charla):
    """El fallo del 19/8/2026: le proponia 20:00 o 23:00, el cliente decia
    '20:00', y la reserva se cerraba igual a las 21:00."""
    charla.responder("somos 9 el viernes para cenar")
    assert charla.momento.hour == 21  # el bot habia asumido las 21
    charla.responder("Grupo Torres")
    charla.responder("20:00")
    assert charla.momento.hour == 20, "ignoro la hora que el mismo habia propuesto"


def test_al_grupo_grande_le_propone_su_turno_no_la_hora_de_comer(charla):
    respuesta = charla.responder("somos 9 el viernes para cenar")
    assert "13:00" not in respuesta, "le ofrece comer a quien ha pedido cenar"
    assert "20:00" in respuesta and "23:00" in respuesta


def test_no_inventa_una_fecha_que_no_entiende(charla):
    guion(charla, "somos 2", "Ana Perez")
    respuesta = charla.responder("cuando podais")
    assert charla.momento is None
    assert "21/08" in respuesta or "viernes" in respuesta.lower() or "fecha" in respuesta


def test_una_hora_a_la_que_no_hay_servicio_no_se_acepta(charla):
    guion(charla, "somos 2", "Ana Perez")
    respuesta = charla.responder("el viernes a las 18:00")
    assert "no damos servicio" in respuesta


def test_si_dice_que_no_al_resumen_no_se_guarda_nada(charla):
    guion(
        charla, "somos 2", "Ana Perez", "el viernes a las 21:00", "comedor", "600111222", "no", "no"
    )
    assert charla.paso == CONFIRMAR
    charla.responder("no")
    assert charla.agenda.apuntes == []
    assert charla.momento is None


# -- lleno y alternativas -------------------------------------------------


def test_cuando_no_cabe_ofrece_alternativas_numeradas_y_las_respeta(charla):
    charla.agenda.crear(
        local_id="prueba",
        momento="2026-08-21T21:00",
        personas=12,
        nombre="Otros",
        telefono="600000000",
        sala="Terraza",
    )
    guion(charla, "somos 4", "Ana Perez")
    respuesta = charla.responder("el viernes a las 21:00")
    charla.responder("terraza")
    assert "1." in charla.responder("terraza") or charla.alternativas
    if charla.alternativas:
        elegida = charla.alternativas[0]
        charla.responder("1")
        assert charla.momento == elegida


# -- traspaso a humano ----------------------------------------------------


def test_solo_pasa_a_una_persona_si_el_cliente_lo_pide(charla):
    respuesta = charla.responder("quiero hablar con una persona")
    assert charla.paso == HUMANO
    assert "persona" in respuesta


def test_una_alergia_no_pasa_a_una_persona(charla):
    """La regla de jose_, al reves de lo que hace todo el mercado."""
    guion(
        charla, "somos 2", "Ana Perez", "el viernes a las 21:00", "comedor", "600111222", "no"
    )
    charla.responder("mi hijo es alergico a los frutos secos, es grave")
    assert charla.paso == CONFIRMAR
    assert "frutos secos" in charla.notas


# -- lo que jose_ encontro probandolo el 20/8/2026 ------------------------


def test_un_nombre_a_secas_pide_apellido(charla):
    """En una hoja de sala, 'Jose' no identifica a nadie."""
    charla.responder("somos 4")
    respuesta = charla.responder("Jose")
    assert "apellido" in respuesta.lower()
    assert charla.nombre == ""
    charla.responder("Da Silva")
    assert charla.nombre == "Jose Da Silva"


def test_con_nombre_y_apellido_no_pregunta_dos_veces(charla):
    charla.responder("somos 4")
    respuesta = charla.responder("Ana Perez")
    assert "apellido" not in respuesta.lower()
    assert charla.nombre == "Ana Perez"


def test_a_las_nueve_es_la_cena(charla):
    """El fallo del 20/8: 'viernes a las nueve' se leia como las 9 de la manana
    y contestaba que a esa hora no hay servicio."""
    charla.responder("somos 4")
    charla.responder("Ana Perez")
    respuesta = charla.responder("el viernes a las nueve")
    assert "no damos servicio" not in respuesta
    assert charla.momento.hour == 21


def test_a_las_dos_es_la_comida(charla):
    charla.responder("somos 2")
    charla.responder("Ana Perez")
    charla.responder("el viernes a las dos")
    assert charla.momento.hour == 14


def test_la_una_y_media(charla):
    charla.responder("somos 2")
    charla.responder("Ana Perez")
    charla.responder("el viernes a la una y media")
    assert (charla.momento.hour, charla.momento.minute) == (13, 30)


def test_si_dice_comer_no_se_va_a_la_noche(charla):
    charla.responder("somos 2")
    charla.responder("Ana Perez")
    charla.responder("el viernes para comer a las dos")
    assert charla.momento.hour == 14


def test_una_hora_de_24h_se_respeta(charla):
    charla.responder("somos 2")
    charla.responder("Ana Perez")
    charla.responder("el viernes a las 21:00")
    assert charla.momento.hour == 21


def test_pidiendo_otra_hora_en_vez_de_elegir_de_la_lista(charla):
    """El otro fallo del 20/8: con la lista de alternativas delante, el cliente
    dijo '21' y el bot le contesto 'dime el numero de la opcion'."""
    charla.responder("somos 4")
    charla.responder("Ana Perez")
    charla.responder("el viernes a las 8 de la manana")  # fuera de servicio
    assert charla.alternativas or charla.paso == CUANDO
    respuesta = charla.responder("a las 21")
    assert "numero de la opcion" not in respuesta.lower()
    assert charla.momento is not None and charla.momento.hour == 21


def test_elegir_por_numero_sigue_funcionando(charla):
    charla.agenda.crear(
        local_id="prueba",
        momento="2026-08-21T21:00",
        personas=12,
        nombre="Otros",
        telefono="600000000",
        sala="Terraza",
    )
    guion(charla, "somos 4", "Ana Perez")
    charla.responder("el viernes a las 21:00")
    charla.responder("terraza")
    if charla.alternativas:
        primera = charla.alternativas[0]
        charla.responder("1")
        assert charla.momento == primera
