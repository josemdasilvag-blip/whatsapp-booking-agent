# Ficha técnica — Agente de reservas por WhatsApp

Estado real a **10 de septiembre de 2026**, leído del código, no de memoria.
Cuando cambie algo, se actualiza esta ficha. Es lo que se contesta cuando
alguien pregunta «qué infraestructura usas».

---

## Las cuatro capas

### 1. Dónde corre

**Hoy: en tu portátil.** Un servidor HTTP de la librería estándar de Python
(`ThreadingHTTPServer`, en `servidor_whatsapp.py`) escuchando en el puerto que
le digas. Sin Docker, sin configuración de despliegue, y hasta agosto de 2026
el repositorio era solo local.

**Consecuencia que hay que entender:** así **no puede funcionar de verdad**. Meta
necesita una dirección pública con HTTPS para entregarte los mensajes, y tu casa
no la tiene. Mientras esto siga en el portátil, el agente solo funciona en pruebas.

### 2. Dónde viven los datos

**En ficheros JSON**, dentro de la carpeta `datos/`:

- `locales.json` — locales, salas, aforos y turnos.
- `agenda.json` — las reservas. Es la fuente de verdad.

Las rutas se pueden cambiar con las variables `RESERVAS_LOCALES` y `RESERVAS_AGENDA`.
**No hay base de datos.** Y eso es una respuesta perfectamente válida a día de hoy:
un fichero JSON es una base de datos pequeña. Lo que hay que saber es cuándo deja
de servir, y está abajo.

### 3. Cómo se despliega

**No se despliega.** Se ejecuta a mano cuando quieres probarlo. No hay servidor
ni proceso automático.

*(El otro proyecto, `venue-ops-mcp`, sí está subido a GitHub.)*

### 4. Qué pasa si se cae

**Nada.** No hay reinicio automático ni aviso. Si el proceso muere, muere.

---

## Lo que sí tiene, y es bueno

- **Verifica la firma de Meta.** Cada webhook entrante se comprueba con HMAC-SHA256
  contra el app secret (`X-Hub-Signature-256`). Nadie puede colarte mensajes falsos
  haciéndose pasar por Meta.
- **Valida el `verify_token`** en el alta del webhook, y hay un test que comprueba
  que rechaza un token inventado.
- **Las credenciales están en `.env`**, que ya está en `.gitignore`. No hay ni un
  token dentro del código.
- **La interpretacion usa un LLM; la decision no.** `conversacion.py` sigue siendo
  determinista: reglas y expresiones regulares. Cuando esas reglas no sacan nada
  --"para el finde, mejor tarde"-- entra `interprete.py`, que le pide a Claude
  Haiku que devuelva `{personas, fecha, hora, franja}` en JSON. A partir de ahi
  manda otra vez el codigo: aforo, turnos y huecos los decide la agenda.
  **Un modelo no puede decir que una mesa esta libre.**
- **Y se cae hacia el lado seguro.** Sin clave, sin red, con timeout, con JSON
  roto o con un valor absurdo (400 personas, una fecha del año pasado), la
  interpretacion devuelve `None` y el agente repregunta, que es exactamente lo
  que hacia antes de existir esa capa. Hay 20 pruebas dedicadas solo a eso.
- **149 tests y pasan los 149.** Eso es un dato que se dice en una entrevista.

Esa division no es casual, es la posicion del proyecto y hay que saber defenderla:
**una reserva es un problema cerrado y el modelo no aporta nada donde una regla
basta.** El modelo entra solo donde la regla se queda corta, que es entender como
escribe la gente de verdad. Un rango como «somos 4 o 5», por ejemplo, lo resuelve
una regla --y por arriba, que si vienen cinco y reservas para cuatro uno se queda
de pie--, no el modelo.

---

## Los tres huecos, en orden

### 1. La condición de carrera en las reservas — el más urgente

`agenda.py` **no tiene ningún cerrojo**, y el servidor es multihilo. Dos
conversaciones simultáneas pueden leer la agenda a la vez, las dos ver que hay
sitio, y las dos escribir. Gana la última y **acabas con la doble reserva que el
README promete que no ocurre**.

En pruebas no se ve, porque nunca hay dos a la vez. En un viernes de verdad, sí.

Se cierra con un cerrojo alrededor de leer-decidir-escribir, o pasando a una base
de datos que sepa hacerlo sola. Es el punto donde el JSON deja de servir.

### 2. Desplegarlo

Sin dirección pública con HTTPS no hay agente. Es el siguiente paso obligatorio
del proyecto, no un extra.

### 3. Que avise cuando se caiga

Una vez desplegado: que el proceso se levante solo y que te llegue un aviso.
Es lo que convierte «lo tengo hecho» en «lo tengo funcionando».

---

## El párrafo, para cuando pregunten

> El agente de reservas está en Python. La entrada es un webhook de la Cloud API
> de Meta con la firma verificada por HMAC, la lógica de disponibilidad es
> determinista —aforo por sala y tiempo de mesa según el tamaño del grupo, no un
> mapa de mesas— y hoy la persistencia son ficheros JSON. Hay una capa de LLM,
> pero solo para interpretar el mensaje libre del cliente: devuelve campos
> validados y la decisión la sigue tomando el código, porque un modelo no puede
> decir que una mesa está libre. Si esa capa falla, el agente repregunta y sigue
> funcionando. Tiene ciento cuarenta y nueve tests y pasan.
> Todavía no está desplegado: corre en local, y el siguiente paso es sacarlo a un
> sitio con HTTPS público, cerrar el acceso concurrente a la agenda con un cerrojo
> y ponerle aviso de caídas.

Decirlo así, con el hueco incluido y con el orden de lo que falta, suena a alguien
que sabe dónde está. Que es exactamente lo que eres.
