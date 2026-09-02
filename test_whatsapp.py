"""Pruebas de la capa de WhatsApp. Ninguna toca la red.

Lo que se comprueba es lo que rompe un webhook en produccion: que no se cuele
un POST falso, que un acuse de entrega no se confunda con un mensaje, y que el
alta del webhook devuelva exactamente lo que Meta espera.
"""

import hashlib
import hmac
import json

import pytest

from whatsapp import (
    ErrorWhatsapp,
    Whatsapp,
    firma_valida,
    mensajes_entrantes,
    responder_verificacion,
)

SECRETO = "un-app-secret-de-mentira"


def firmar(cuerpo: bytes, secreto: str = SECRETO) -> str:
    return "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()


def sobre(*mensajes, contactos=None, extra=None) -> dict:
    valor = {"messaging_product": "whatsapp", "messages": list(mensajes)}
    if contactos:
        valor["contacts"] = contactos
    if extra:
        valor.update(extra)
    return {"entry": [{"changes": [{"value": valor, "field": "messages"}]}]}


def texto(de="34600111222", cuerpo="hola", id_="wamid.1"):
    return {"from": de, "id": id_, "type": "text", "text": {"body": cuerpo}}


# -- alta del webhook -----------------------------------------------------


def test_el_alta_devuelve_el_challenge_tal_cual():
    codigo, cuerpo = responder_verificacion(
        {
            "hub.mode": ["subscribe"],
            "hub.verify_token": ["secreto"],
            "hub.challenge": ["1234567890"],
        },
        "secreto",
    )
    assert (codigo, cuerpo) == (200, "1234567890")


def test_con_el_token_cambiado_no_se_da_de_alta():
    codigo, _ = responder_verificacion(
        {
            "hub.mode": ["subscribe"],
            "hub.verify_token": ["otro"],
            "hub.challenge": ["1234567890"],
        },
        "secreto",
    )
    assert codigo == 403


def test_sin_token_configurado_no_se_acepta_nada():
    """Un verify_token vacio no puede valer como comodin."""
    codigo, _ = responder_verificacion(
        {"hub.mode": ["subscribe"], "hub.verify_token": [""], "hub.challenge": ["x"]},
        "",
    )
    assert codigo == 403


# -- firma ----------------------------------------------------------------


def test_la_firma_buena_pasa():
    cuerpo = json.dumps(sobre(texto())).encode()
    assert firma_valida(cuerpo, firmar(cuerpo), SECRETO)


def test_un_cuerpo_manipulado_no_pasa():
    cuerpo = json.dumps(sobre(texto())).encode()
    cabecera = firmar(cuerpo)
    manipulado = cuerpo.replace(b"hola", b"adios")
    assert not firma_valida(manipulado, cabecera, SECRETO)


def test_firmado_con_otro_secreto_no_pasa():
    cuerpo = b"{}"
    assert not firma_valida(cuerpo, firmar(cuerpo, "otro-secreto"), SECRETO)


def test_sin_cabecera_no_pasa():
    assert not firma_valida(b"{}", None, SECRETO)


def test_cabecera_con_otro_formato_no_pasa():
    assert not firma_valida(b"{}", "sha1=loquesea", SECRETO)


# -- lectura de mensajes --------------------------------------------------


def test_saca_el_texto_y_el_nombre_del_perfil():
    carga = sobre(
        texto(cuerpo="somos 4 el viernes"),
        contactos=[{"wa_id": "34600111222", "profile": {"name": "Ana"}}],
    )
    (m,) = mensajes_entrantes(carga)
    assert (m.de, m.texto, m.nombre) == ("34600111222", "somos 4 el viernes", "Ana")
    assert m.es_texto


def test_un_acuse_de_entrega_no_es_un_mensaje():
    """Si esto colara, el bot se contestaria a si mismo en bucle."""
    carga = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {"id": "wamid.1", "status": "delivered"}
                            ],
                        }
                    }
                ]
            }
        ]
    }
    assert mensajes_entrantes(carga) == []


def test_los_audios_y_las_fotos_se_ignoran():
    carga = sobre(
        {"from": "34600111222", "id": "wamid.2", "type": "audio", "audio": {"id": "x"}},
        texto(cuerpo="y esto si"),
    )
    assert [m.texto for m in mensajes_entrantes(carga)] == ["y esto si"]


def test_varios_mensajes_en_un_solo_sobre():
    carga = sobre(
        texto(cuerpo="uno", id_="wamid.1"),
        texto(cuerpo="dos", id_="wamid.2"),
    )
    assert len(mensajes_entrantes(carga)) == 2


def test_un_sobre_vacio_no_revienta():
    assert mensajes_entrantes({}) == []
    assert mensajes_entrantes({"entry": [{"changes": []}]}) == []


# -- cliente --------------------------------------------------------------


def test_sin_credenciales_avisa_claro(monkeypatch):
    monkeypatch.delenv("WHATSAPP_TOKEN", raising=False)
    monkeypatch.delenv("WHATSAPP_PHONE_NUMBER_ID", raising=False)
    with pytest.raises(ErrorWhatsapp) as e:
        Whatsapp()
    assert ".env" in str(e.value)


def test_el_destino_se_limpia_antes_de_mandarlo(monkeypatch):
    """Meta quiere solo digitos: '+34 600 11 12 22' se manda como 34600111222."""
    enviado = {}

    def falso(self, ruta, carga):
        enviado.update(carga)
        return {"messages": [{"id": "wamid.enviado"}]}

    monkeypatch.setattr(Whatsapp, "_llamar", falso)
    cliente = Whatsapp(token="t", phone_number_id="123")
    cliente.enviar_texto("+34 600 11 12 22", "hola")
    assert enviado["to"] == "34600111222"
    assert enviado["text"]["body"] == "hola"
    assert enviado["messaging_product"] == "whatsapp"


def test_un_mensaje_larguisimo_se_corta(monkeypatch):
    """WhatsApp corta en 4096; mandarlo mas largo es un error de Meta, no un texto truncado."""
    enviado = {}
    monkeypatch.setattr(
        Whatsapp, "_llamar", lambda self, ruta, carga: enviado.update(carga) or {}
    )
    cliente = Whatsapp(token="t", phone_number_id="123")
    cliente.enviar_texto("34600111222", "a" * 5000)
    assert len(enviado["text"]["body"]) == 4096
