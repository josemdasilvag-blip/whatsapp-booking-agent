# Cómo funciona una reserva de restaurante

Criterio de jose_, 18 de agosto de 2026. Diez años dirigiendo operaciones en grupos de
restauración multi-local. Esto no sale de ninguna documentación: es la parte del proyecto
que no se puede copiar.

## Qué se pregunta, y nada más

En este orden:

1. **Número de personas**
2. **Nombre**
3. **Día y hora**
4. **Sala o ambiente** — solo si el local tiene varios. Si no, no se pregunta.
5. **Teléfono**
6. **Correo**
7. **Campo libre** — alergias, intolerancias, cualquier comentario.

Se acabó. No se pregunta nada más para reservar en un restaurante.

## Depósito: por defecto, no

**El 99% de los restaurantes no puede pedir depósito.** La gente no lo paga y pierdes la
reserva. Solo se lo pueden permitir los locales muy de moda, muy llenos, que rechazan
gente igual.

Si un local sí puede, el depósito **se limita a sus turnos fuertes**, no a todo el
horario.

> Implicación: opción configurable por local, **desactivada de fábrica**, y con la
> posibilidad de activarla solo en turnos concretos.

## Grupos grandes: se empujan a los extremos, no se rechazan

Un grupo grande en mitad del turno fuerte es un problema: te parte el comedor y te bloquea
la rotación. La solución no es decir que no, es **moverlo a un extremo del turno**:

- **Temprano**, a las 20:00 — en España la noche empieza ahí.
- **O tarde**, a las 22:30 o las 23:00.

El medio es lo que hay que evitar.

**Con matiz:** si el restaurante es grande y tiene espacio de sobra, no hace falta empujar
a nadie. Depende del local, no es una regla universal.

## Turnos fuertes (España)

- Viernes noche
- Sábado mediodía
- Sábado noche
- Domingo mediodía

Es donde aplica lo del depósito y lo de empujar a los grupos grandes.

## Disponibilidad: aforo, no mesas

Va vinculado al sistema de reservas del local. El modelo es **aforo total** (ejemplo: 120
personas), no un mapa de mesas. **Las mesas se doblan y se triplican**: el mismo sitio se
vende varias veces en un turno, y lo que decide cuántas veces es el tiempo de ocupación.

**Tiempo estimado por tamaño de grupo:**

| Personas | Ocupación |
|---|---|
| 2 - 3 | 1 h 15 min |
| 4 - 6 | 1 h 30 min |
| 7 - 10 | 2 h |

Sobre eso se calcula cuántos pases caben. Un grupo de nueve a las 21:00 ocupa su aforo
hasta las 23:00; dos personas a las 21:00 lo sueltan a las 22:15.

## Cuando no hay hueco a la hora que piden

Escalera, en este orden y sin saltarse pasos:

1. **Su mismo turno**, lo más cerca posible de su hora. **Aquí no hay corte**: si lo
   único libre de esa cena son tres horas más tarde, se le ofrece igual, porque sigue
   siendo la cena que pidió. Lo único que manda es el orden por cercanía.
2. Si el turno está lleno, **el otro turno del mismo día**. En hostelería son dos:
   mediodía y noche.
3. Si el día está lleno, **otro día cerca de su hora**. Aquí sí corta el margen de
   **una hora arriba o abajo**: quien quería cenar no acaba comiendo.

Y entre reservas **no hay margen ninguno**: entran una detrás de otra. Es la única
forma de triplicar una mesa en un turno.

## Modificar y cancelar: dentro

El bot tiene que saber hacerlo, no solo crear:

- **Añadir o quitar personas** — y recalcular la ocupación, porque pasar de 6 a 7 sube el
  tiempo de mesa de 1 h 30 a 2 h.
- **Cancelar.**

## Traspaso a humano: casi nunca

Aquí jose_ contradice a propósito lo que hace todo el mundo.

**Una reserva es simple y el bot no tiene por qué callarse.** Ni siquiera una alergia
justifica pasar a una persona: se recoge en el campo libre y llega a cocina igual.

> **El bot pasa a un humano solo si el cliente lo pide.** Nada más.

Todos los agentes de reserva del mercado meten reglas de escalado por intención, por
sentimiento, por palabras clave. Sobra. Añade fallos y no mejora la reserva.
