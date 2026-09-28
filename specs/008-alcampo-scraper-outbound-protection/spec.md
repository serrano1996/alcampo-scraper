# Spec 008 — Protección de salida hacia Alcampo

- **Estado:** aprobada (2026-09-28)
- **Fecha:** 2026-09-28
- **Referencia:** sin equivalente en `mercadona-scraper`. Sale del análisis del proyecto del 2026-09-28 y amplía la spec 002 (antibaneo)

> **Origen:** el análisis del 2026-09-28 se repartió en tres specs: la **006** (mantenibilidad y calidad), esta (protección de salida y observabilidad de la cache) y la **007** (los timeouts de Redis se suman a su degradación sin Redis). El número no fija el orden: esta spec no depende de la 007 y puede implementarse antes.

## 1. Contexto y objetivo

El riesgo principal del proyecto es el AWS WAF de Alcampo: con muy poco tráfico bloquea **la IP entera** durante 2–4 minutos (Fase 0 §5). La spec 002 añadió fingerprint, reintentos con jitter y un enfriamiento tras cada challenge, y la cache (spec 001) evita repetir búsquedas. Aun así, quedan huecos por los que el propio servicio puede provocar ráfagas. Los marcados como **verificado** se reprodujeron el 2026-09-28 con `fakeredis` y un scraper falso de 300 ms:

- **Búsquedas iguales simultáneas** (verificado): 10 búsquedas a la vez de `leche` sin cache generan **10 peticiones** a Alcampo. La cache solo protege a partir de la primera respuesta.
- **Mayúsculas** (verificado): con `leche` en cache, `Leche` y `LECHE` generan **2 peticiones** más. Los espacios de los extremos ya se recortan; las mayúsculas y los espacios internos no.
- **Sin límite propio de tasa:** el enfriamiento es **reactivo** (actúa cuando el WAF ya ha bloqueado). Con muchos términos distintos, que no se benefician de la cache, nada impide una ráfaga. El umbral del WAF para la búsqueda es **desconocido**: los tres bloqueos de la Fase 0 saltaron en otro endpoint (`temporary-delivery-destinations`).
- **Enfriamiento fijo** de 180 s frente a bloqueos observados de hasta ~4 min: si se queda corto, la siguiente búsqueda recibe otro challenge y el ciclo se repite.
- **Sin tiempo máximo por búsqueda:** 3 intentos × 10 s de timeout, más las esperas, dan ≈ 30 s en el peor caso.
- **Cache invisible en los logs:** no se sabe si una respuesta salió de la cache o de Alcampo, así que no se puede medir cuánto protege.

**Objetivo:** que el servicio **nunca** genere ráfagas hacia Alcampo y que el peor caso sea un `502` rápido, **sin cambiar el contrato de la API**.

## 2. Usuarios y actores

- **Aplicación cliente:** no nota nada en condiciones normales. En los picos recibe `502` rápidos en lugar de provocar un bloqueo que la dejaría minutos sin servicio.
- **Responsable del servicio:** ajusta los límites con variables de entorno y ve en los logs qué sale de la cache.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que búsquedas iguales simultáneas generen una sola petición a Alcampo.
- **H2.** Como responsable del servicio, quiero un tope global de peticiones hacia Alcampo, para no provocar el bloqueo del WAF aunque lleguen muchas búsquedas distintas.
- **H3.** Como responsable del servicio, quiero que un Alcampo lento dé un `502` en un tiempo acotado.
- **H4.** Como responsable del servicio, quiero ver en los logs si cada búsqueda salió de la cache, de Alcampo o de una petición compartida.

## 4. Requisitos funcionales (EARS)

### Búsquedas iguales y clave de cache

- **RF-1.** CUANDO lleguen varias búsquedas con la misma clave de cache mientras una de ellas ya consulta Alcampo **en el mismo proceso**, EL sistema DEBERÁ hacer **una sola** petición y dar su resultado, o su error, a todas (D1).
- **RF-2.** EL sistema DEBERÁ construir la clave de cache con el término en minúsculas (`casefold`) y los espacios internos colapsados a uno, de modo que `Leche`, `LECHE` y `leche  entera` compartan entrada con `leche` y `leche entera`. La respuesta DEBERÁ seguir devolviendo en `search.term` el término del cliente (recortado en los extremos, como hoy). **Condicionado a la verificación en vivo de D4.**

### Límite global de tasa

- **RF-3.** EL sistema DEBERÁ limitar las peticiones salientes a Alcampo a `ALCAMPO_RATE_LIMIT` por ventana de `ALCAMPO_RATE_WINDOW_SECONDS` (por defecto 20 cada 60 s, D2), contadas en Redis **entre todas las instancias**. Cuenta cada petición real, reintentos incluidos.
- **RF-4.** SI una búsqueda no cacheada necesita llamar a Alcampo y el límite está agotado, ENTONCES EL sistema DEBERÁ responder `502` al momento, **sin** llamar a Alcampo, y registrar un `WARNING` (degradación prevista, como el enfriamiento). Las búsquedas cacheadas DEBERÁN seguir respondiendo `200` (D3).
- **RF-5.** SI el límite se agota entre dos intentos de una misma búsqueda, ENTONCES EL sistema DEBERÁ dejar de reintentar y responder `502`.
- **RF-6.** `ALCAMPO_RATE_LIMIT=0` DEBERÁ desactivar el límite. Un valor negativo en `ALCAMPO_RATE_LIMIT`, o un valor menor que 1 en `ALCAMPO_RATE_WINDOW_SECONDS`, DEBERÁ impedir que la app arranque.

### Tiempo máximo por búsqueda

- **RF-7.** SI una búsqueda no obtiene respuesta de Alcampo en `SEARCH_TIMEOUT_SECONDS` (por defecto 15, D5) en total, intentos y esperas incluidos, ENTONCES EL sistema DEBERÁ cancelarla, responder `502` y registrar un `ERROR`. Un valor menor o igual que 0 DEBERÁ impedir que la app arranque.

### Enfriamiento creciente

- **RF-8.** CUANDO llegue un challenge del WAF y haya habido otro en los últimos `WAF_COOLDOWN_MAX_SECONDS` segundos, EL sistema DEBERÁ **duplicar** la duración del enfriamiento anterior, sin superar `WAF_COOLDOWN_MAX_SECONDS` (por defecto 900, D6). Sin challenges recientes, la duración vuelve a `WAF_COOLDOWN_SECONDS`.
- **RF-9.** El `ERROR` del challenge (spec 003 RF-11) DEBERÁ indicar la duración real del enfriamiento aplicado.
- **RF-10.** `WAF_COOLDOWN_SECONDS=0` DEBERÁ seguir desactivando todo el enfriamiento. SI `WAF_COOLDOWN_MAX_SECONDS` es menor que `WAF_COOLDOWN_SECONDS`, ENTONCES la app NO DEBERÁ arrancar.

### Observabilidad de la cache

- **RF-11.** CUANDO se sirva una búsqueda, EL sistema DEBERÁ registrar en `INFO`, con el request id, su origen: `hit` (cache), `miss` (Alcampo) o `shared` (resultado de otra petición simultánea, RF-1).

## 5. Requisitos no funcionales

- **RNF-1. Contrato intacto:** ni códigos ni campos nuevos. El límite de tasa y el tiempo máximo reutilizan el `502` y su cuerpo (spec 001 RF-17). Mercadona sigue siendo intercambiable.
- **RNF-2. Sin dependencias nuevas** (constitución #1): `asyncio` y los comandos de Redis que ya se usan.
- **RNF-3. Tests sin red ni tiempo real:** concurrencia, límite y tiempos con `fakeredis`, `respx` y relojes y esperas inyectables, como en la spec 002.
- **RNF-4. Docs vivas:** README y `.env.example` documentan `ALCAMPO_RATE_LIMIT`, `ALCAMPO_RATE_WINDOW_SECONDS`, `SEARCH_TIMEOUT_SECONDS` y `WAF_COOLDOWN_MAX_SECONDS`. **`.env.example` lo edita el usuario a mano** (la regla global de permisos lo impide, como en la spec 004). El test de sincronización de la 002 lo exige.

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| 10 búsquedas simultáneas de `leche` sin cache | 1 petición a Alcampo; 10 respuestas `200` iguales; 1 log `miss` y 9 `shared` (RF-1, RF-11) |
| Esa petición única falla (`502` o challenge) | las 10 reciben `502`; el enfriamiento se activa **una** vez |
| `leche` y `Leche` a la vez | comparten la petición (RF-1, RF-2) |
| `Leche` con `leche` en cache | `200` de la cache con `search.term: "Leche"` (RF-2) |
| Límite agotado, búsqueda cacheada | `200` (RF-4) |
| Límite agotado, búsqueda sin cache | `502` inmediato, `WARNING`, 0 peticiones a Alcampo (RF-4) |
| Límite agotado durante los reintentos | se abandona la búsqueda, `502` (RF-5) |
| Enfriamiento activo y límite agotado | manda el enfriamiento, que se comprueba antes (sin cambio respecto a la 002) |
| Alcampo tarda más que `SEARCH_TIMEOUT_SECONDS` | `502` y `ERROR`; la petición en curso se cancela (RF-7) |
| Segundo challenge 5 min después del primero | enfriamiento de 360 s (RF-8) |
| Challenges seguidos | 180 → 360 → 720 → 900 → 900 (RF-8) |
| Challenge 1 h después del anterior | vuelve a 180 s (RF-8) |
| Varias instancias con el mismo Redis | comparten límite y enfriamiento; RF-1 solo agrupa dentro de cada instancia (D1) |
| Redis caído o colgado | sin cambios respecto a hoy (`500`): los timeouts y la degradación son de la spec 007 |

## 7. Fuera de alcance

- **Agrupar búsquedas iguales entre instancias** con un candado en Redis (D1).
- **Cola o espera** cuando el límite se agota (D3).
- **Timeouts de Redis** y degradación sin Redis: spec 007.
- **Descubrir el umbral real del WAF** para la búsqueda: exigiría provocar bloqueos a propósito.
- **Métricas** (Prometheus u otras): RF-11 se limita a los logs.

## 8. Criterios de finalización

- [ ] RF-1 a RF-11 cubiertos por tests en verde.
- [ ] Un test demuestra que N búsquedas simultáneas iguales producen **1** petición a Alcampo.
- [ ] Un test demuestra que con el límite agotado no sale **ninguna** petición a Alcampo y la cache sigue respondiendo.
- [ ] La suite de las specs 001–005 sigue en verde (cambios en aserciones solo si el plan los justifica uno a uno).
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios (y `mypy`, si la 006 ya está cerrada).
- [ ] README y `.env.example` actualizados.
- [ ] **Verificación en vivo de D4 antes del plan:** 3 búsquedas espaciadas (≥30 s entre ellas y ≥10 min desde la última petición a Alcampo) de `leche`, `Leche` y `LECHE`, comparando los IDs de producto devueltos. Resultado anotado en el plan.
- [ ] **Verificación manual** con `docker compose`: 10 búsquedas simultáneas → 1 petición a Alcampo en los logs; la misma búsqueda con otras mayúsculas sale de la cache; con `ALCAMPO_RATE_LIMIT=1`, una segunda búsqueda distinta da `502` sin salir a Alcampo. Como mucho 2 búsquedas reales, ≥10 min desde la última.

## 9. Decisiones (dudas resueltas el 2026-09-28)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Alcance de la agrupación de búsquedas iguales | Solo dentro de cada proceso (`asyncio`). Entre instancias ya protege el límite global | RF-1. Un candado en Redis añadiría casos difíciles (dueño caído, TTL, sondeo) para un despliegue que hoy es de una instancia |
| D2 | Límite por defecto | 20 peticiones cada 60 s, configurable | RF-3. El umbral real es desconocido; es un valor prudente. Por encima de 20 términos **distintos** por minuto, `502` |
| D3 | Qué hacer al agotar el límite | `502` inmediato, como el enfriamiento | RF-4. Un `503` con `Retry-After` sería más correcto en HTTP, pero rompe el contrato compartido con Mercadona |
| D4 | Normalizar mayúsculas | Sí, **tras comprobar en vivo** que Alcampo devuelve lo mismo para `leche`, `Leche` y `LECHE`. Si difiere, RF-2 se descarta y se anota | RF-2 condicionado. Constitución: las dudas que dependen de Alcampo se verifican en vivo |
| D5 | Tiempo máximo por búsqueda | 15 s | RF-7. Una búsqueda normal tarda ~0,4 s (spec 005, T6); corta el peor caso actual (~30 s) a la mitad |
| D6 | Enfriamiento creciente | Duplicar con tope de 900 s | RF-8. Evita el ciclo challenge → espera corta → challenge |
