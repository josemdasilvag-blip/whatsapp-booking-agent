"""Las reglas de jose_, convertidas en pruebas.

Cada test comprueba una regla concreta de `como-funciona-una-reserva.md`.
"""

from datetime import date, datetime, time, timedelta

import pytest

from motor_disponibilidad import (
    Local,
    Reserva,
    Sala,
    SinHueco,
    Turno,
    ocupacion_minutos,
)

COMIDA = Turno("comida", time(13, 0), time(15, 30))
CENA = Turno("cena", time(20, 0), time(23, 0))

# Viernes 21 de agosto de 2026. weekday(): 4 = viernes, 5 = sabado, 6 = domingo.
VIERNES = date(2026, 8, 21)
SABADO = date(2026, 8, 22)


def local(aforo: int = 120, empujar: bool = True) -> Local:
    return Local(
        nombre="Prueba",
        salas=[Sala("comedor", aforo), Sala("terraza", 40)],
        turnos={d: [COMIDA, CENA] for d in range(7)},
        empujar_grupos_grandes=empujar,
    )


def llenar(l: Local, dia: date, turno: Turno, sala: str = "comedor") -> None:
    """Deja un turno sin un hueco libre.

    Se escriben las reservas a mano en vez de con `reservar` porque el objetivo
    es dejarlo lleno, no comprobar que quepan: con `reservar` la segunda ya
    chocaria con la primera.
    """
    aforo = l.sala(sala).aforo
    hora = datetime.combine(dia, turno.apertura)
    fin = datetime.combine(dia, turno.ultimo_pase)
    while hora <= fin:
        l.reservas.append(Reserva(hora, aforo, sala))
        hora += timedelta(minutes=30)


# -- tiempo de mesa --------------------------------------------------------


@pytest.mark.parametrize(
    "personas, minutos",
    [(2, 75), (3, 75), (4, 90), (6, 90), (7, 120), (10, 120)],
)
def test_los_tramos_de_ocupacion_son_los_suyos(personas, minutos):
    assert ocupacion_minutos(personas) == minutos


def test_pasar_de_seis_a_siete_alarga_la_mesa_media_hora():
    assert ocupacion_minutos(7) - ocupacion_minutos(6) == 30


# -- aforo y mesas dobladas ------------------------------------------------


def test_las_mesas_se_doblan():
    """Dos de 21:00 sueltan a las 22:15, asi que el aforo se revende."""
    l = local(aforo=2)
    l.reservar(datetime.combine(VIERNES, time(21, 0)), 2, "comedor")

    assert not l.cabe(datetime.combine(VIERNES, time(22, 0)), 2, "comedor")
    assert l.cabe(datetime.combine(VIERNES, time(22, 15)), 2, "comedor")


def test_un_grupo_de_nueve_ocupa_su_aforo_dos_horas():
    l = local(aforo=10)
    l.reservar(datetime.combine(VIERNES, time(21, 0)), 9, "comedor")

    assert not l.cabe(datetime.combine(VIERNES, time(22, 45)), 9, "comedor")
    assert l.cabe(datetime.combine(VIERNES, time(23, 0)), 9, "comedor")


def test_el_aforo_es_por_sala():
    l = local(aforo=4)
    l.reservar(datetime.combine(VIERNES, time(21, 0)), 4, "comedor")

    assert l.cabe(datetime.combine(VIERNES, time(21, 0)), 4, "terraza")


def test_no_se_reserva_fuera_de_turno():
    l = local()
    assert not l.cabe(datetime.combine(VIERNES, time(18, 0)), 2, "comedor")


def test_no_se_acepta_entrar_despues_del_ultimo_pase():
    l = local()
    assert not l.cabe(datetime.combine(VIERNES, time(23, 30)), 2, "comedor")


# -- la escalera de alternativas -------------------------------------------


def test_primero_ofrece_el_mismo_turno_y_lo_mas_cerca_posible():
    """La pareja de las 20:15 suelta a las 21:30, media hora de lo pedido."""
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(21, 0))
    l.reservar(datetime.combine(VIERNES, time(20, 15)), 2, "comedor")

    propuestas = l.alternativas(pedido, 2, "comedor")

    assert propuestas[0] == datetime.combine(VIERNES, time(21, 30))
    assert all(p.date() == VIERNES and p.hour >= 20 for p in propuestas)


def test_dentro_del_turno_se_ofrece_aunque_quede_lejos():
    """En su propio turno no hay corte de margen.

    Si lo que queda libre de la cena son dos horas mas tarde, se le ofrece: sigue
    siendo la cena que pidio. Lo unico que manda es el orden por cercania.
    """
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(20, 0))
    l.reservar(pedido, 2, "comedor")

    propuestas = l.alternativas(pedido, 2, "comedor", maximo=10)

    assert datetime.combine(VIERNES, time(22, 30)) in propuestas
    de_la_cena = [p for p in propuestas if p.date() == VIERNES and p.hour >= 20]
    assert de_la_cena == sorted(de_la_cena, key=lambda h: abs(h - pedido))


def test_en_otro_dia_si_corta_el_margen_de_una_hora():
    """Saltar de dia si respeta la hora: quien queria cenar no acaba comiendo."""
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(21, 0))
    llenar(l, VIERNES, COMIDA)
    llenar(l, VIERNES, CENA)

    propuestas = l.alternativas(pedido, 2, "comedor", maximo=5)

    otros_dias = [p for p in propuestas if p.date() > VIERNES]
    assert otros_dias
    for p in otros_dias:
        referencia = datetime.combine(p.date(), time(21, 0))
        assert abs(p - referencia) <= timedelta(hours=1)


def test_solo_se_sale_del_turno_cuando_no_queda_ni_un_hueco():
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(21, 0))
    llenar(l, VIERNES, CENA)

    propuestas = l.alternativas(pedido, 2, "comedor")

    del_la_cena = [p for p in propuestas if p.date() == VIERNES and p.hour >= 20]
    assert del_la_cena == []


def test_si_no_hay_en_el_turno_ofrece_el_otro_turno_del_mismo_dia():
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(21, 0))
    llenar(l, VIERNES, CENA)

    propuestas = l.alternativas(pedido, 2, "comedor")

    assert any(p.date() == VIERNES and p.hour < 16 for p in propuestas)


def test_por_ultimo_ofrece_otro_dia_a_la_hora_que_pidio():
    """Con el dia entero lleno, lo que queda es otro dia a su hora."""
    l = local(aforo=2)
    pedido = datetime.combine(VIERNES, time(21, 0))
    llenar(l, VIERNES, COMIDA)
    llenar(l, VIERNES, CENA)

    propuestas = l.alternativas(pedido, 2, "comedor")

    assert propuestas
    assert all(p.date() > VIERNES for p in propuestas)
    assert all(p.time() == time(21, 0) for p in propuestas)


# -- grupos grandes --------------------------------------------------------


def test_al_grupo_grande_se_le_empuja_a_los_extremos_del_turno():
    l = local()
    horas = l.horas_recomendadas_grupo(SABADO, 9, "comedor")

    assert len(horas) == 2
    assert horas[0].time() == COMIDA.apertura
    assert horas[1].time() == CENA.ultimo_pase


def test_a_una_pareja_no_se_le_empuja_a_ningun_lado():
    l = local()
    horas = l.horas_recomendadas_grupo(SABADO, 2, "comedor")

    assert len(horas) > 2


def test_en_un_local_con_espacio_de_sobra_no_se_empuja_a_nadie():
    l = local(empujar=False)
    horas = l.horas_recomendadas_grupo(SABADO, 9, "comedor")

    assert len(horas) > 2


# -- modificar y cancelar --------------------------------------------------


def test_anadir_una_persona_puede_no_caber_porque_alarga_la_mesa():
    """De 6 a 7 la mesa pasa de 1h30 a 2h y pisa a la reserva de despues."""
    l = local(aforo=6)
    reserva = l.reservar(datetime.combine(VIERNES, time(20, 0)), 6, "comedor")
    l.reservar(datetime.combine(VIERNES, time(21, 30)), 6, "comedor")

    with pytest.raises(SinHueco):
        l.modificar_personas(reserva, 7)


def test_si_la_modificacion_no_cabe_la_reserva_original_sigue_viva():
    l = local(aforo=6)
    reserva = l.reservar(datetime.combine(VIERNES, time(20, 0)), 6, "comedor")
    l.reservar(datetime.combine(VIERNES, time(21, 30)), 6, "comedor")

    with pytest.raises(SinHueco):
        l.modificar_personas(reserva, 7)

    assert reserva in l.reservas


def test_quitar_personas_libera_aforo():
    l = local(aforo=6)
    reserva = l.reservar(datetime.combine(VIERNES, time(21, 0)), 6, "comedor")
    assert not l.cabe(datetime.combine(VIERNES, time(21, 0)), 4, "comedor")

    l.modificar_personas(reserva, 2)

    assert l.cabe(datetime.combine(VIERNES, time(21, 0)), 4, "comedor")


def test_cancelar_libera_la_mesa():
    l = local(aforo=4)
    reserva = l.reservar(datetime.combine(VIERNES, time(21, 0)), 4, "comedor")

    l.cancelar(reserva)

    assert l.cabe(datetime.combine(VIERNES, time(21, 0)), 4, "comedor")


# -- salas -----------------------------------------------------------------


def test_si_solo_hay_una_sala_no_se_pregunta_por_ella():
    uno = Local("Prueba", [Sala("comedor", 60)], {d: [CENA] for d in range(7)})
    assert not uno.tiene_varias_salas
    assert local().tiene_varias_salas
