"""Pruebas de la capa que guarda las reservas.

El motor ya esta probado en `test_motor_disponibilidad.py`. Aqui se comprueba
lo que anade la agenda: que persiste, que no se pierde el aforo entre llamadas,
que modificar recalcula el tiempo de mesa y que cancelar no borra nada.
"""

import json
from datetime import date, datetime

import pytest

from agenda import (
    Agenda,
    ApunteDesconocido,
    DatosInvalidos,
    LocalDesconocido,
)
from motor_disponibilidad import SinHueco

# Viernes 21 de agosto de 2026. weekday(): 4 = viernes, 5 = sabado, 6 = domingo.
VIERNES_CENA = "2026-08-21T21:00"
LUNES = "2026-08-24T21:00"  # Casa Lola cierra los lunes

CONFIG = {
    "locales": [
        {
            "id": "prueba",
            "nombre": "Local de prueba",
            "empujar_grupos_grandes": True,
            "salas": [{"nombre": "Comedor", "aforo": 20}, {"nombre": "Terraza", "aforo": 8}],
            "turnos": {
                str(d): [
                    {"nombre": "Comida", "apertura": "13:00", "ultimo_pase": "15:30"},
                    {"nombre": "Cena", "apertura": "20:00", "ultimo_pase": "23:00"},
                ]
                for d in range(1, 7)
            },
            "turnos_fuertes": ["4-Cena", "5-Comida", "5-Cena", "6-Comida"],
            "deposito": {
                "activo": False,
                "importe_por_persona": 15,
                "solo_en_turnos_fuertes": True,
            },
        },
        {
            "id": "unica-sala",
            "nombre": "Local de una sola sala",
            "empujar_grupos_grandes": False,
            "salas": [{"nombre": "Salon", "aforo": 100}],
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
def agenda(tmp_path):
    locales = tmp_path / "locales.json"
    locales.write_text(json.dumps(CONFIG), encoding="utf-8")
    return Agenda(ruta_locales=locales, ruta_reservas=tmp_path / "agenda.json")


def reservar(a, **cambios):
    datos = dict(
        local_id="prueba",
        momento=VIERNES_CENA,
        personas=2,
        nombre="Ana",
        telefono="+34600111222",
        sala="Comedor",
    )
    datos.update(cambios)
    return a.crear(**datos)


# -- lo basico ------------------------------------------------------------


def test_una_reserva_guarda_los_siete_campos(agenda):
    r = reservar(
        agenda,
        personas=4,
        nombre="Ana Perez",
        correo="ana@ejemplo.com",
        notas="celiaca",
    )
    assert (r.personas, r.nombre, r.sala) == (4, "Ana Perez", "Comedor")
    assert r.correo == "ana@ejemplo.com"
    assert r.notas == "celiaca"
    assert r.id.startswith("R-")


def test_la_reserva_sobrevive_a_recargar_el_fichero(agenda):
    r = reservar(agenda, nombre="Marta")
    otra = Agenda(agenda.ruta_locales, agenda.ruta_reservas)
    assert otra.apunte(r.id).nombre == "Marta"


def test_el_aforo_cuenta_lo_ya_guardado(agenda):
    reservar(agenda, personas=18)
    with pytest.raises(SinHueco):
        reservar(agenda, personas=4)


def test_dos_ids_no_se_repiten(agenda):
    ids = {reservar(agenda).id for _ in range(5)}
    assert len(ids) == 5


# -- validacion -----------------------------------------------------------


def test_local_que_no_existe(agenda):
    with pytest.raises(LocalDesconocido):
        reservar(agenda, local_id="no-existe")


def test_sala_que_no_existe(agenda):
    with pytest.raises(KeyError):
        reservar(agenda, sala="Sotano")


def test_fuera_de_turno_no_se_reserva(agenda):
    with pytest.raises(SinHueco):
        reservar(agenda, momento="2026-08-21T18:00")


def test_dia_de_cierre_no_se_reserva(agenda):
    with pytest.raises(SinHueco):
        reservar(agenda, momento=LUNES)


def test_cero_personas_no_es_una_reserva(agenda):
    with pytest.raises(DatosInvalidos):
        reservar(agenda, personas=0)


def test_sin_nombre_no_hay_reserva(agenda):
    with pytest.raises(DatosInvalidos):
        reservar(agenda, nombre="   ")


def test_telefono_que_no_lo_es(agenda):
    with pytest.raises(DatosInvalidos):
        reservar(agenda, telefono="hola")


def test_fecha_mal_escrita_lo_dice_con_el_formato_bueno(agenda):
    with pytest.raises(DatosInvalidos) as e:
        reservar(agenda, momento="21 de agosto")
    assert "AAAA-MM-DD" in str(e.value)


# -- salas ----------------------------------------------------------------


def test_con_una_sola_sala_no_se_pregunta(agenda):
    r = agenda.crear(
        local_id="unica-sala",
        momento=VIERNES_CENA,
        personas=2,
        nombre="Luis",
        telefono="600111222",
    )
    assert r.sala == "Salon"


def test_con_varias_salas_hay_que_elegir(agenda):
    with pytest.raises(DatosInvalidos) as e:
        agenda.crear(
            local_id="prueba",
            momento=VIERNES_CENA,
            personas=2,
            nombre="Luis",
            telefono="600111222",
        )
    assert "Comedor" in str(e.value) and "Terraza" in str(e.value)


def test_cada_sala_tiene_su_propio_aforo(agenda):
    reservar(agenda, personas=8, sala="Terraza")
    with pytest.raises(SinHueco):
        reservar(agenda, personas=1, sala="Terraza")
    assert reservar(agenda, personas=8, sala="Comedor")


# -- buscar ---------------------------------------------------------------


def test_buscar_por_telefono_aunque_este_escrito_de_otra_forma(agenda):
    reservar(agenda, telefono="+34 600 111 222")
    assert len(agenda.buscar(telefono="600111222")) == 1


def test_buscar_por_nombre_parcial_y_sin_mayusculas(agenda):
    reservar(agenda, nombre="Ana Maria Perez")
    assert len(agenda.buscar(nombre="maria")) == 1


def test_buscar_sin_nada_que_buscar(agenda):
    with pytest.raises(DatosInvalidos):
        agenda.buscar()


def test_las_canceladas_no_salen_salvo_que_se_pidan(agenda):
    r = reservar(agenda)
    agenda.cancelar(r.id)
    assert agenda.buscar(telefono="600111222") == []
    assert len(agenda.buscar(telefono="600111222", incluir_canceladas=True)) == 1


# -- modificar ------------------------------------------------------------


def test_pasar_de_seis_a_siete_alarga_la_mesa(agenda):
    r = reservar(agenda, personas=6)
    assert r.fin.hour == 22 and r.fin.minute == 30  # 1h30
    agenda.modificar(r.id, personas=7)
    assert agenda.apunte(r.id).fin.hour == 23  # 2h


def test_anadir_una_persona_puede_no_caber(agenda):
    r = reservar(agenda, personas=6)
    reservar(agenda, personas=14, momento="2026-08-21T20:00")
    with pytest.raises(SinHueco):
        agenda.modificar(r.id, personas=7)
    assert agenda.apunte(r.id).personas == 6  # y no se ha tocado


def test_una_reserva_no_compite_consigo_misma(agenda):
    r = reservar(agenda, personas=20)  # el comedor entero
    assert agenda.modificar(r.id, personas=20).personas == 20


def test_modificar_una_cancelada_no_se_puede(agenda):
    r = reservar(agenda)
    agenda.cancelar(r.id)
    with pytest.raises(DatosInvalidos):
        agenda.modificar(r.id, personas=3)


def test_cambiar_solo_las_notas_no_toca_lo_demas(agenda):
    r = reservar(agenda, personas=4)
    agenda.modificar(r.id, notas="alergia al marisco")
    de_nuevo = agenda.apunte(r.id)
    assert de_nuevo.notas == "alergia al marisco"
    assert de_nuevo.personas == 4 and de_nuevo.inicio == r.inicio


# -- cancelar -------------------------------------------------------------


def test_cancelar_libera_el_aforo(agenda):
    r = reservar(agenda, personas=20)
    agenda.cancelar(r.id)
    assert reservar(agenda, personas=20)


def test_cancelar_no_borra_la_fila(agenda):
    r = reservar(agenda)
    agenda.cancelar(r.id)
    guardado = json.loads(agenda.ruta_reservas.read_text(encoding="utf-8"))
    assert len(guardado["reservas"]) == 1
    assert guardado["reservas"][0]["estado"] == "cancelada"


def test_cancelar_dos_veces_avisa(agenda):
    r = reservar(agenda)
    agenda.cancelar(r.id)
    with pytest.raises(DatosInvalidos):
        agenda.cancelar(r.id)


def test_codigo_inventado(agenda):
    with pytest.raises(ApunteDesconocido):
        agenda.apunte("R-NOEXISTE")


# -- deposito y turnos fuertes -------------------------------------------


def test_de_fabrica_no_hay_deposito(agenda):
    momento = datetime.fromisoformat(VIERNES_CENA)
    assert agenda.deposito_aplicable("prueba", momento) is None


def test_activado_solo_pesa_en_los_turnos_fuertes(agenda):
    agenda.ficha("prueba")["deposito"]["activo"] = True
    viernes_noche = datetime.fromisoformat(VIERNES_CENA)
    miercoles_noche = datetime.fromisoformat("2026-08-19T21:00")
    assert agenda.deposito_aplicable("prueba", viernes_noche)["importe_por_persona"] == 15
    assert agenda.deposito_aplicable("prueba", miercoles_noche) is None


def test_los_cuatro_turnos_fuertes_de_jose(agenda):
    fuertes = {
        "2026-08-21T21:00": True,   # viernes noche
        "2026-08-22T14:00": True,   # sabado mediodia
        "2026-08-22T21:00": True,   # sabado noche
        "2026-08-23T14:00": True,   # domingo mediodia
        "2026-08-19T21:00": False,  # miercoles noche
        "2026-08-21T14:00": False,  # viernes mediodia
    }
    for momento, esperado in fuertes.items():
        assert agenda.es_turno_fuerte("prueba", datetime.fromisoformat(momento)) is esperado


# -- agenda del dia -------------------------------------------------------


def test_el_dia_sale_ordenado_por_hora(agenda):
    reservar(agenda, momento="2026-08-21T22:00", nombre="Tarde")
    reservar(agenda, momento="2026-08-21T20:00", nombre="Pronto")
    dia = agenda.del_dia("prueba", date(2026, 8, 21))
    assert [a.nombre for a in dia] == ["Pronto", "Tarde"]


def test_el_dia_no_arrastra_canceladas_ni_otros_dias(agenda):
    r = reservar(agenda, nombre="Fuera")
    agenda.cancelar(r.id)
    reservar(agenda, momento="2026-08-22T21:00", nombre="Manana")
    assert agenda.del_dia("prueba", date(2026, 8, 21)) == []


# -- alternativas ---------------------------------------------------------


def test_si_no_cabe_se_ofrecen_alternativas(agenda):
    reservar(agenda, personas=20, momento="2026-08-21T21:00")
    huecos = agenda.alternativas(
        "prueba", datetime.fromisoformat(VIERNES_CENA), 2, "Comedor"
    )
    assert huecos
    assert all(h.date() == date(2026, 8, 21) for h in huecos)


def test_al_grupo_grande_se_le_dan_los_extremos(agenda):
    horas = agenda.horas_para_grupo("prueba", date(2026, 8, 21), 8, "Comedor")
    assert len(horas) == 2
    assert horas[0].hour == 13 and horas[1].hour == 23


def test_en_local_con_sitio_de_sobra_no_se_empuja(agenda):
    horas = agenda.horas_para_grupo("unica-sala", date(2026, 8, 21), 8, "Salon")
    assert len(horas) > 2


def test_al_grupo_grande_se_le_dan_los_extremos_de_SU_turno(agenda):
    """Sin acotar por turno, a quien pide cenar se le ofrecia la hora de comer.

    El extremo del dia (13:00) no es el extremo de la cena. Detectado el
    19/8/2026 probando la conversacion con un grupo de 9 que pedia cena.
    """
    cena = datetime.fromisoformat("2026-08-21T21:00")
    horas = agenda.horas_para_grupo(
        "prueba", date(2026, 8, 21), 8, "Comedor", momento=cena
    )
    assert [h.hour for h in horas] == [20, 23]

    comida = datetime.fromisoformat("2026-08-21T14:00")
    horas = agenda.horas_para_grupo(
        "prueba", date(2026, 8, 21), 8, "Comedor", momento=comida
    )
    assert [h.hour for h in horas] == [13, 15]


def test_sin_momento_sigue_mirando_el_dia_entero(agenda):
    """La vista del restaurante si quiere el dia completo."""
    horas = agenda.horas_para_grupo("prueba", date(2026, 8, 21), 8, "Comedor")
    assert horas[0].hour == 13 and horas[-1].hour == 23
