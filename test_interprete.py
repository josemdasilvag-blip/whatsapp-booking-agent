"""Pruebas de la capa de interpretacion.

Aqui no se comprueba que el modelo acierte -- eso no es comprobable en una
bateria y no depende de nosotros. Se comprueba lo que si es nuestro: que **nada
de lo que devuelva el modelo pueda colarse sin validar**, y que cuando falla,
falle hacia el lado seguro. Por eso el cliente va inyectado y no se toca la red.
"""

import json
from datetime import date, timedelta

import pytest

from agenda import Agenda
from conversacion import CUANDO, Conversacion
from interprete import Interpretacion, interpretar

HOY = date(2026, 8, 19)  # miercoles

CONFIG = {
    "locales": [
        {
            "id": "prueba",
            "nombre": "Casa Prueba",
            "empujar_grupos_grandes": False,
            "salas": [{"nombre": "Comedor", "aforo": 40}],
            "turnos": {
                str(d): [
                    {"nombre": "Comida", "apertura": "13:00", "ultimo_pase": "15:30"},
                    {"nombre": "Cena", "apertura": "20:00", "ultimo_pase": "23:00"},
                ]
                for d in range(1, 7)
            },
            "turnos_fuertes": [],
            "deposito": {"activo": False},
        }
    ]
}


def responde(payload):
    """Cliente falso: devuelve siempre lo mismo, sea texto o dict."""

    def _pedir(texto, hoy):
        return payload if isinstance(payload, str) else json.dumps(payload)

    return _pedir


def revienta(excepcion):
    def _pedir(texto, hoy):
        raise excepcion

    return _pedir


# -- lo que el modelo devuelve bien ---------------------------------------


def test_saca_los_cuatro_campos():
    r = interpretar(
        "somos 4 o 5, mejor cenar el viernes tarde",
        HOY,
        pedir=responde(
            {"personas": 5, "fecha": "2026-08-21", "hora": "22:00", "franja": "cena"}
        ),
    )
    assert r.personas == 5
    assert r.fecha == date(2026, 8, 21)
    assert r.hora == (22, 0)
    assert r.franja == "cena"


def test_acepta_json_envuelto_en_backticks():
    r = interpretar(
        "somos ocho",
        HOY,
        pedir=responde('```json\n{"personas": 8, "fecha": null, "hora": null, "franja": null}\n```'),
    )
    assert r.personas == 8


def test_solo_franja_sin_hora_es_valido():
    r = interpretar(
        "mejor para cenar",
        HOY,
        pedir=responde({"personas": None, "fecha": None, "hora": None, "franja": "cena"}),
    )
    assert r.franja == "cena"
    assert r.hora is None


# -- lo que el modelo devuelve mal, que es lo que importa ------------------


def test_todo_null_no_es_una_interpretacion():
    assert (
        interpretar(
            "hola buenas",
            HOY,
            pedir=responde({"personas": None, "fecha": None, "hora": None, "franja": None}),
        )
        is None
    )


def test_json_roto_no_revienta():
    assert interpretar("somos 4", HOY, pedir=responde("{personas: 4,,,")) is None


def test_respuesta_sin_json_no_revienta():
    assert interpretar("somos 4", HOY, pedir=responde("Claro, con gusto!")) is None


@pytest.mark.parametrize("personas", [0, -3, 51, 9999, "cuatro", 4.5, True, None])
def test_personas_absurdas_se_descartan(personas):
    r = interpretar(
        "somos muchos",
        HOY,
        pedir=responde({"personas": personas, "fecha": None, "hora": None, "franja": None}),
    )
    assert r is None


def test_fecha_en_el_pasado_se_descarta():
    r = interpretar(
        "el lunes pasado",
        HOY,
        pedir=responde({"personas": 2, "fecha": "2026-08-17", "hora": None, "franja": None}),
    )
    assert r.fecha is None
    assert r.personas == 2  # lo bueno se conserva, lo malo se cae


def test_fecha_a_mas_de_un_ano_se_descarta():
    lejos = (HOY + timedelta(days=400)).isoformat()
    r = interpretar(
        "dentro de mucho",
        HOY,
        pedir=responde({"personas": 2, "fecha": lejos, "hora": None, "franja": None}),
    )
    assert r.fecha is None


@pytest.mark.parametrize("hora", ["25:00", "12:99", "tarde", "12", "", None, 21])
def test_horas_invalidas_se_descartan(hora):
    r = interpretar(
        "a esa hora",
        HOY,
        pedir=responde({"personas": 2, "fecha": None, "hora": hora, "franja": None}),
    )
    assert r.hora is None


def test_franja_inventada_se_descarta():
    r = interpretar(
        "para el brunch",
        HOY,
        pedir=responde({"personas": 2, "fecha": None, "hora": None, "franja": "brunch"}),
    )
    assert r.franja is None


# -- fallos de infraestructura --------------------------------------------


@pytest.mark.parametrize(
    "fallo", [TimeoutError("tarda"), OSError("sin red"), ValueError("forma rara")]
)
def test_si_la_llamada_falla_no_hay_interpretacion(fallo):
    assert interpretar("somos 4", HOY, pedir=revienta(fallo)) is None


def test_sin_clave_no_se_llama_a_nadie(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert interpretar("somos 4 o 5", HOY) is None


def test_texto_vacio_no_gasta_llamada():
    def _explota(texto, hoy):
        raise AssertionError("no se debe llamar al modelo con texto vacio")

    assert interpretar("   ", HOY, pedir=_explota) is None


# -- integrado en la conversacion -----------------------------------------


@pytest.fixture
def charla(tmp_path):
    locales = tmp_path / "locales.json"
    locales.write_text(json.dumps(CONFIG), encoding="utf-8")
    agenda = Agenda(ruta_locales=locales, ruta_reservas=tmp_path / "agenda.json")
    return Conversacion(local_id="prueba", agenda=agenda, hoy=HOY)


def test_el_modelo_rescata_lo_que_la_regex_no_saca(charla):
    charla.pedir_al_modelo = responde(
        {"personas": 5, "fecha": "2026-08-21", "hora": "22:00", "franja": "cena"}
    )
    charla.responder("para el finde que viene, los que seamos, mejor tarde")
    assert charla.personas == 5
    assert charla.momento.date() == date(2026, 8, 21)
    assert charla.momento.hour == 22


def test_la_franja_del_modelo_fija_el_turno(charla):
    charla.pedir_al_modelo = responde(
        {"personas": 2, "fecha": "2026-08-21", "hora": None, "franja": "cena"}
    )
    charla.responder("para el finde, mejor tarde")
    assert charla.momento.hour == 21  # hora por defecto de cena


def test_no_se_llama_al_modelo_si_la_regex_ya_lo_saca(charla):
    def _explota(texto, hoy):
        raise AssertionError("lo determinista bastaba; esta llamada sobra")

    charla.pedir_al_modelo = _explota
    charla.responder("somos 4")
    assert charla.personas == 4


def test_una_sola_llamada_por_mensaje(charla):
    llamadas = []

    def _cuenta(texto, hoy):
        llamadas.append(texto)
        return json.dumps(
            {"personas": 5, "fecha": "2026-08-21", "hora": "22:00", "franja": "cena"}
        )

    charla.pedir_al_modelo = _cuenta
    charla.responder("para el finde que viene, los que seamos, mejor tarde")
    assert len(llamadas) == 1


def test_si_el_modelo_cae_la_conversacion_sigue(charla):
    charla.pedir_al_modelo = revienta(TimeoutError("timeout"))
    respuesta = charla.responder("pues no sé, los que seamos")
    assert "Cuántas personas" in respuesta
    assert charla.personas is None


# -- la frontera: que resuelve la regla y que se deja al modelo -------------
#
# Un rango es un problema cerrado y lo cierra una regla. Se comprueba aqui
# porque es justo la linea que separa las dos capas: si esto se rompiese, la
# capa de LLM estaria tapando un fallo del parser en vez de anadir algo.


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("somos 4 o 5", 5),
        ("entre 6 y 8 personas", 8),
        ("somos 4-5", 5),
        ("cuatro o cinco", 5),
        ("seis a ocho", 8),
        ("somos 20 o 25", 25),
    ],
)
def test_un_rango_se_reserva_por_arriba(texto, esperado):
    from conversacion import _numero_de_personas

    assert _numero_de_personas(texto) == esperado


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("una mesa para cuatro", 4),   # "una" es articulo, no el numero
        ("una reserva para ocho", 8),
        ("para dos el dia veinte", 2),  # "veinte" es el dia, no la gente
        ("uno de nosotros", 1),
        ("somos dos", 2),
        ("reserva para 12", 12),
    ],
)
def test_no_confunde_articulos_ni_fechas_con_el_numero(texto, esperado):
    from conversacion import _numero_de_personas

    assert _numero_de_personas(texto) == esperado


@pytest.mark.parametrize("texto", ["a las 21", "a las 8 y media", "a la una"])
def test_una_hora_no_es_un_numero_de_personas(texto):
    from conversacion import _numero_de_personas

    assert _numero_de_personas(texto) is None
