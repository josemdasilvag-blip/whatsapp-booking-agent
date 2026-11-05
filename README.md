# WhatsApp restaurant booking agent · MCP server

> **In English.** A booking agent for restaurant groups: an [MCP](https://modelcontextprotocol.io)
> server with seven tools (list venues, check availability, create, find, modify and cancel
> bookings, and print the day sheet) plus a WhatsApp Cloud API webhook so guests book by chat.
> The availability engine is deterministic: capacity per room and table-turn times that depend
> on party size, with an ordered ladder of alternatives when a slot is full. An optional
> Claude (Haiku) layer interprets free text; **the model proposes, the code validates and
> executes**, so the agent can never confirm a booking it did not save.
> Python · MCP SDK · WhatsApp Cloud API · Claude API · **149 automated tests** (`python -m pytest`).
> Venue data in `datos/` is sample data. The rest of this README is in Spanish.

## Servidor MCP de reservas de restaurante

Un servidor [MCP](https://modelcontextprotocol.io) que le da a un asistente las siete
cosas que hay que saber hacer con una reserva: listar locales, mirar si cabe, crear,
buscar, modificar, cancelar y sacar la hoja del día.

Lo que lo diferencia de un CRUD con nombres bonitos no es el código: es el criterio de
operaciones que hay debajo, escrito en
[`como-funciona-una-reserva.md`](como-funciona-una-reserva.md) por alguien que ha dirigido
operaciones en grupos de restauración. Cuatro decisiones que casi ningún agente de
reservas del mercado toma bien:

**El aforo se dobla y se triplica.** No hay mapa de mesas. Hay aforo por sala y un tiempo
de ocupación que depende del tamaño del grupo (2-3 pax → 1 h 15; 4-6 → 1 h 30; 7-10 → 2 h).
El mismo sitio se vende varias veces en un turno, y lo que decide cuántas es el reloj.
Consecuencia práctica: pasar una reserva de 6 a 7 personas alarga la mesa media hora y
puede no caber, aunque solo se añada una persona. El servidor lo comprueba y lo dice.

**Cuando no hay hueco hay una escalera, y tiene orden.** Primero otra hora de su mismo
turno — y ahí no hay corte: si lo único libre de esa cena son tres horas más tarde, se
ofrece igual, porque sigue siendo la cena que pidió. Luego el otro turno del mismo día.
Y solo al final otro día, esta vez sí con un margen de una hora, que quien quería cenar
no acaba comiendo el jueves.

**El depósito viene desactivado.** El 99% de los restaurantes no puede pedirlo: la gente
no lo paga y pierdes la reserva. Es configurable por local y, cuando se activa, se limita
a los turnos fuertes (viernes noche, sábado mediodía, sábado noche, domingo mediodía).

**No hay reglas de traspaso a humano.** Todos los agentes del mercado escalan por
sentimiento, por intención o por palabras clave. Una reserva es simple y el bot no tiene
por qué callarse: ni siquiera una alergia justifica pasar a una persona, porque se recoge
en el campo libre y llega a cocina igual. Se pasa a un humano **solo si el cliente lo pide**.

## Las siete herramientas

| Herramienta | Qué hace |
|---|---|
| `reservas_listar_locales` | Locales, salas con aforo, turnos por día y si piden depósito |
| `reservas_consultar_disponibilidad` | Si cabe; y si no, la escalera de alternativas |
| `reservas_crear` | Cierra la reserva y devuelve su código |
| `reservas_buscar` | Por teléfono o por nombre; el teléfono se compara por los últimos 9 dígitos |
| `reservas_modificar` | Personas, hora, sala o notas, recalculando el tiempo de mesa |
| `reservas_cancelar` | Marca la reserva como cancelada y libera el aforo |
| `reservas_agenda_del_dia` | La hoja de sala: todo lo reservado por hora, con las alergias aparte |

Las de lectura van anotadas como `read_only_hint`, así que un cliente MCP puede llamarlas
sin pedir permiso. `reservas_cancelar` va marcada como destructiva.

## Por WhatsApp

La misma máquina de estados atiende por WhatsApp Cloud API. `conversacion.py` no sabe
nada de HTTP ni de Meta: recibe texto y devuelve texto, y por eso vale igual para la
ventana de demo que para el webhook.

```
whatsapp.py            cliente de la Cloud API, firma de Meta y lectura de mensajes
servidor_whatsapp.py   el webhook: recibe, contesta y guarda
comprobar_whatsapp.py  simula a Meta entera, sin necesidad de número
```

Tres decisiones que separan esto de un webhook de tutorial:

- **Se comprueba la firma `X-Hub-Signature-256`.** Sin eso, cualquiera que descubra la
  URL puede inventarse reservas. Se compara con `compare_digest`.
- **Se contesta 200 a Meta antes de ponerse a trabajar.** Si tardas, Meta da el envío
  por fallido y lo reintenta, y el cliente recibe la misma respuesta tres veces.
- **Los acuses de entrega se ignoran.** Llegan por el mismo webhook que los mensajes; si
  se colaran, el bot se contestaría a sí mismo.

Sin `WHATSAPP_TOKEN` arranca en **modo seco**: procesa los mensajes y escribe en consola
lo que contestaría, sin llamar a Meta. Así se prueba el circuito entero antes de tener
número.

## Probarlo

```bash
python -m pytest -q            # 91 pruebas: aforo, agenda, conversación y WhatsApp
python comprobar_mcp.py        # handshake MCP real y ciclo completo de reserva
python comprobar_whatsapp.py   # alta del webhook, firma y una reserva por WhatsApp
```

`comprobar_mcp.py` levanta el servidor como subproceso, hace el handshake, comprueba que
anuncia las siete herramientas y mantiene una conversación entera sobre una agenda
temporal: llena el comedor con un grupo de 80, confirma que ya no cabe nadie y que las
alternativas que ofrece siguen siendo del mismo día, crea una de 6 en la terraza, la
encuentra buscando el teléfono escrito de otra forma, cuadra la hoja del día en 86
comensales, la sube a 7 personas comprobando que la mesa pasa de 1 h 30 a 2 h, la cancela
y verifica que la fila sigue en el fichero marcada como cancelada, no borrada.

## Instalarlo en Claude Desktop

```bash
python instalar_claude_desktop.py
```

Hace copia de seguridad de la configuración antes de tocarla y añade solo su entrada.
Después hay que cerrar Claude Desktop del todo y volver a abrirlo.

## Cómo está montado

```
motor_disponibilidad.py   aforo, tiempo de mesa y escalera de alternativas
agenda.py                 quién reserva, códigos y persistencia en JSON
servidor_mcp.py           las siete herramientas MCP
datos/locales.json        los locales: salas, aforos, turnos y depósito
datos/agenda.json         las reservas (se crea solo)
```

La fuente de verdad es `datos/agenda.json`. El objeto del motor se reconstruye desde ella
en cada llamada, así que no hay dos copias del estado que se puedan desincronizar. Se
puede apuntar a otros ficheros con las variables de entorno `RESERVAS_LOCALES` y
`RESERVAS_AGENDA`, que es como cada cliente lleva sus propios datos sin tocar el código.

## Estado

Es un proyecto propio, no un encargo, y aquí no se cuenta como otra cosa.

Funciona de punta a punta contra un cliente MCP real, y el webhook de WhatsApp está
construido y probado contra sobres y firmas idénticos a los de Meta. Lo que le falta es
un número de teléfono dado de alta en la Cloud API: Meta no da número de pruebas a esta
app, y registrar uno propio exige recibir un código por SMS en ese teléfono.

Pendiente además: Google Calendar, y el despliegue con Docker en un VPS para que el
webhook escuche 24/7.
