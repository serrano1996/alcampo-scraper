# Plan 008 — Protección de salida hacia Alcampo

- **Estado:** borrador, pendiente de aprobación
- **Fecha:** 2026-09-28
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D6**; las de specs anteriores como **002-plan-Dn**, etc. Las decisiones de este plan son **D1…**

## 1. Visión general

```
GET /api/v1/products  (auth 004, validación)
  └─ ProductService.search(query)
       key = normalize_term(query.term)                                   RF-2   (D1)
       ├─ cache.get(key) → hit ──────────────────────── log source=hit    RF-11  (D9)
       └─ miss → in_flight.run(key, fetch)                                RF-1   (D2)
            ├─ ya hay una en curso → espera su resultado  log source=shared
            └─ primera → fetch():                          log source=miss
                 cooldown.is_active() → CooldownActiveError (502, WARNING)       (002)
                 async with asyncio.timeout(SEARCH_TIMEOUT_SECONDS):              RF-7  (D6)
                     scraper.search(key)
                        └─ por cada intento: rate_limiter.acquire()               RF-3…RF-5 (D3, D4)
                              agotado → OutboundRateLimitedError (502, WARNING)
                 challenge → cooldown.activate(base, max) → duración real         RF-8…RF-10 (D7)
                 map + cache.set(key)

Handler 502: UpstreamThrottledError (enfriamiento, límite) → WARNING; resto → ERROR      (D5)
```

El contrato no cambia: todo lo nuevo termina en el `502` y el cuerpo de siempre (spec RNF-1).

## 2. Verificaciones previas (hechas el 2026-09-28)

| Supuesto | Experimento | Resultado | Consecuencia |
|---|---|---|---|
| Búsquedas iguales simultáneas van todas a Alcampo | `ProductService` con `fakeredis` y un scraper falso de 300 ms; 10 `search("leche")` con `asyncio.gather` | **10** llamadas al scraper | RF-1 es necesario (D2) |
| La cache distingue mayúsculas | Con `leche` en cache: `Leche`, `LECHE`, `leche  ` | **2** llamadas extra (el recorte de extremos ya funciona) | RF-2 es necesario (D1) |
| **spec-D4:** Alcampo ignora las mayúsculas | **En vivo**, con el scraper y el cliente HTTP de la app, 35 s entre peticiones (09:44:49Z, 09:45:24Z, 09:46:00Z): `leche`, `Leche`, `LECHE` | 50 productos cada una: **mismos IDs, mismo orden, datos idénticos** | RF-2 se mantiene |
| Alcampo ignora los espacios internos repetidos | **En vivo**, 35 s entre peticiones (09:46:49Z, 09:47:24Z): `leche entera` y `leche   entera` | **Mismos IDs, mismo orden, datos idénticos** | Colapsar espacios es seguro. Total: 5 peticiones en 2,5 min, **sin challenge** |
| `fakeredis` soporta conjuntos ordenados en transacción | `pipeline(transaction=True)` con `ZREMRANGEBYSCORE`, `ZADD`, `ZCARD`, `EXPIRE`; después `ZREM` | `[0, 1, 1, True]`, `ZREM` → 1 | La ventana deslizante (D3) se puede probar sin Redis real |
| `asyncio.timeout` en la versión mínima | Existe desde Python 3.11 | Sí | D6 sin dependencias |

## 3. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `alcampo_rate_limit`, `alcampo_rate_window_seconds`, `search_timeout_seconds`, `waf_cooldown_max_seconds` + validación cruzada | RF-6, RF-7, RF-10 |
| `app/services/search_cache.py` | `normalize_term()`; la clave usa el término normalizado | RF-2 |
| `app/services/in_flight.py` | **nuevo**: `InFlightSearches` (agrupación por clave dentro del proceso) | RF-1 |
| `app/services/rate_limiter.py` | **nuevo**: `OutboundRateLimiter` (ventana deslizante en Redis) | RF-3, RF-6 |
| `app/services/waf_cooldown.py` | `activate(base, max)` con duración creciente; devuelve la aplicada | RF-8…RF-10 |
| `app/exceptions.py` | `UpstreamThrottledError` (padre de `CooldownActiveError`) y `OutboundRateLimitedError` | RF-4 |
| `app/scrapers/alcampo_search.py` | `acquire()` del limitador antes de cada intento | RF-3, RF-5 |
| `app/services/product_service.py` | normalización, agrupación, tiempo máximo, log de origen, enfriamiento creciente | RF-1, RF-2, RF-7, RF-9, RF-11 |
| `app/core/dependencies.py`, `app/main.py` | `InFlightSearches` en el `lifespan`; limitador por petición; el handler trata `UpstreamThrottledError` como `WARNING` | RF-1, RF-4 |
| `README.md`, `.env.example` | variables nuevas y comportamiento | RNF-4 |

## 4. Modelo de datos

Claves de Redis (nuevas o cambiadas):

| Clave | Tipo | Contenido | TTL |
|---|---|---|---|
| `search:{warehouse}:{término normalizado}` | string | como hoy; solo cambia el término de la clave | `CACHE_TTL_SECONDS` |
| `ratelimit:alcampo` | sorted set | un miembro único (`uuid4().hex`) por petición, con su instante como puntuación | `ALCAMPO_RATE_WINDOW_SECONDS` (se renueva en cada petición) |
| `waf:cooldown` | string | como hoy | la duración aplicada |
| `waf:cooldown:last` | string | duración del último enfriamiento, en segundos | `WAF_COOLDOWN_MAX_SECONDS` (define qué es "reciente", RF-8) |

Las entradas antiguas con mayúsculas en la clave (`search:5:Leche`) no se migran: caducan solas en ≤ 1 h.

## 5. Decisiones de diseño

**D1 — `normalize_term(term) = " ".join(term.split()).casefold()`, y a Alcampo se envía el término normalizado.** La misma clave sirve para la cache y para la agrupación (RF-1, RF-2). Enviar el normalizado hace que la petición compartida no dependa de quién llegó primero; los resultados son idénticos (§2). `search.term` en la respuesta sigue siendo el del cliente: el servicio ya lo reescribe en los aciertos de cache y lo hará igual en los resultados compartidos.
- *Descartada:* normalizar solo la clave y enviar el término original. La respuesta compartida dependería de la grafía del primero en llegar.

**D2 — `InFlightSearches`: un `dict[str, asyncio.Task]` por proceso, creado en el `lifespan`.** `run(key, fetch)` devuelve el resultado de la tarea en curso para esa clave, o crea una con `fetch()`. Cada llamante espera con `asyncio.shield(task)`: si un cliente se desconecta, se cancela **su** espera, no la búsqueda de los demás. La tarea se elimina del `dict` al terminar (`add_done_callback`), con éxito o con error. `run` devuelve también si la petición fue la primera o una compartida, para el log (RF-11).
- Vive en `app.state` porque `ProductService` se crea **por petición** (`get_product_service`): una instancia por servicio no agruparía nada.
- *Descartada:* `asyncio.Future` manual. Obliga a propagar a mano éxito, error y cancelación, algo que `Task` ya hace.
- *Descartada:* candado en Redis (spec-D1).

**D3 — Límite de tasa con ventana deslizante (sorted set), no con ventana fija.** En cada `acquire()`, una transacción: `ZREMRANGEBYSCORE` (borra lo que ha salido de la ventana), `ZADD` (este intento), `ZCARD` y `EXPIRE`. Si el recuento supera el límite, se hace `ZREM` del miembro recién añadido (un intento rechazado no consume cupo) y se lanza `OutboundRateLimitedError`. Con `ALCAMPO_RATE_LIMIT=0` no se toca Redis.
- *Descartada:* ventana fija (`INCR` + `EXPIRE`). Es más simple, pero permite **el doble del límite** en un segundo alrededor del cambio de ventana (20 al final de una y 20 al principio de la siguiente). Justo la ráfaga que se quiere evitar.
- *Descartada:* token bucket en Lua. `fakeredis` necesitaría `lupa`, una dependencia nueva.
- El instante sale del reloj local (inyectable en los tests). Con varias instancias, relojes desincronizados distorsionan la ventana: riesgo aceptado (R3).

**D4 — `acquire()` dentro del `send()` del scraper, antes de cada intento.** Así cuenta cada petición real, reintentos incluidos (RF-3), y un límite agotado entre intentos corta los reintentos (RF-5): `send_with_retry` solo captura `httpx.TransportError`, así que `OutboundRateLimitedError` sale sin reintentarse. `retry.py` no cambia.

**D5 — `UpstreamThrottledError(UpstreamUnavailableError)` como padre de `CooldownActiveError` y del nuevo `OutboundRateLimitedError`.** El handler del `502` registra `WARNING` para toda la familia "degradación prevista" y `ERROR` para el resto, sin una lista de `isinstance` que crezca. Ambos siguen siendo `UpstreamUnavailableError`: el `502` y su cuerpo no cambian. El `WARNING` indica el motivo (`reason=%r`).

**D6 — `asyncio.timeout(SEARCH_TIMEOUT_SECONDS)` alrededor de `scraper.search` en el servicio**, dentro de la tarea compartida: todos los que esperan esa búsqueda reciben el mismo `502`. El `TimeoutError` se convierte en `UpstreamUnavailableError("search timeout")`; el `ERROR` lo registra el handler del `502` con ese motivo (RF-7), sin una línea más. Un challenge durante el tiempo máximo se trata como hoy.
- *Descartada:* bajar el timeout de httpx. Limita cada intento, no la suma de intentos y esperas.

**D7 — Enfriamiento creciente con una segunda clave.** `activate(base, max) -> int`: si `base == 0`, no hace nada y devuelve 0 (RF-10). Si no, lee `waf:cooldown:last`: sin valor, la duración es `base`; con valor, es `min(2 × último, max)`. Escribe `waf:cooldown` con esa duración y `waf:cooldown:last` con TTL `max` (RF-8), y devuelve la duración para el `ERROR` del servicio (RF-9).
- Con `max = 900`, "reciente" significa en los últimos 15 min: 5 min después → 360 s; 1 h después → 180 s (casos de la spec).
- *Descartada:* guardar un contador de challenges. La duración anterior ya contiene la información y evita recalcular potencias.

**D8 — Validación en `Settings`.** `alcampo_rate_limit: int = Field(20, ge=0)`, `alcampo_rate_window_seconds: int = Field(60, ge=1)`, `search_timeout_seconds: float = Field(15, gt=0)`, `waf_cooldown_max_seconds: int = Field(900, ge=0)`, y un `model_validator` que exige `max >= base` (RF-10). Todo fallo impide arrancar, como en las specs 002 y 003.

**D9 — Log de origen en el servicio:** `INFO "search served source=%s"` (`hit`, `miss` o `shared`), con el request id que ya pone la spec 003. Es una línea más por búsqueda.
- *Descartada:* un campo en la línea de fin del middleware. El middleware tendría que saber de la cache (acoplamiento), y la 006 lo va a reescribir como ASGI puro.

**D10 — Entrega en 2 PRs encadenados** (estimación ~700 líneas, §9): **PR 1** con las piezas aisladas (config, normalización, enfriamiento creciente, limitador), **PR 2** con el cableado (scraper, excepciones, tiempo máximo, agrupación, integración, docs).

## 6. Regresiones previstas

Cada una se corrige **en la tarea que la provoca**.

| Tests afectados | Por qué | Corrección |
|---|---|---|
| `tests/core/test_env_example.py` | 4 campos nuevos en `Settings` | el **usuario** añade las 4 variables a `.env.example` (regla de permisos) |
| `tests/services/test_waf_cooldown.py` (4 llamadas a `activate(ttl_seconds=…)`) | nueva firma `activate(base, max)` y nueva semántica (una segunda llamada ya no renueva con otro TTL, sino que duplica) | se reescriben según D7; el caso "renovar con 60 tras 180" se sustituye por "segundo challenge reciente duplica" |
| `tests/services/test_product_service.py` (3 `activate(ttl_seconds=180)` + el constructor) | firma de `activate`; `ProductService` recibe `InFlightSearches` y el limitador | ajuste de llamadas y constructor |
| `tests/scrapers/test_alcampo_search.py` (2 constructores) | el scraper recibe el limitador | se pasa un limitador desactivado (`limit=0`) salvo en los tests nuevos |
| Tests de cache con claves literales (`search:5:leche`) | ninguno: `leche` ya está normalizado | — |

## 7. Estrategia de test por RF

**U** = unitario, **I** = integración (app real + `lifespan` + fakeredis + respx).

| RF | Test | Tipo |
|---|---|---|
| RF-2 | `normalize_term`: `"Leche"`, `"LECHE"`, `"leche   entera"`, `"leche\tentera"` → `"leche"`, `"leche entera"`; `search.term` devuelve la grafía del cliente | U |
| RF-2 | con `leche` en cache, `Leche` → `200` de la cache, 0 llamadas a Alcampo, `search.term == "Leche"` | I |
| RF-1 | N llamadas simultáneas a `InFlightSearches.run` con la misma clave → `fetch` se ejecuta **1** vez y todas reciben el mismo resultado; con error, todas reciben el error; claves distintas no se agrupan; la entrada se borra al terminar | U |
| RF-1 | cancelar a un llamante no cancela la búsqueda de los demás (`shield`) | U |
| RF-1 | 10 `service.search` simultáneos (scraper que espera un `asyncio.Event`) → 1 llamada al scraper; `leche` y `Leche` simultáneos también → 1 | U |
| RF-3 | con límite 2/60 s y reloj falso: 2 `acquire` pasan, el 3.º lanza; al avanzar el reloj más allá de la ventana, vuelve a pasar; un intento rechazado no consume cupo | U |
| RF-6 | límite 0 → nunca lanza y no escribe en Redis; valores negativos o ventana 0 → `ValidationError` | U |
| RF-4 | límite agotado: búsqueda sin cache → `502`, `WARNING` con el motivo, **0** peticiones respx; búsqueda cacheada → `200` | I |
| RF-5 | límite de 1 con Alcampo devolviendo `503`: el primer intento sale, el reintento se corta → `502`, 1 sola petición respx | I |
| RF-7 | scraper que no responde, `SEARCH_TIMEOUT_SECONDS` pequeño → `502` y `ERROR` con `reason='search timeout'`; `≤ 0` → `ValidationError` | U + I |
| RF-8 | `activate`: primero 180; segundo con `last` presente → 360; secuencia 180→360→720→900→900; sin `last` (caducado) → 180 | U |
| RF-9 | el `ERROR` del challenge lleva la duración aplicada (360 en el segundo) | U |
| RF-10 | `base=0` → no escribe y devuelve 0; `max < base` → `ValidationError` | U |
| RF-11 | `hit`, `miss` y `shared` aparecen en `INFO` con el request id de cada petición | U + I |

**Tareas sin RED posible:** ninguna prevista; todas introducen comportamiento nuevo.

## 8. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | El límite por defecto (20/60 s) es una estimación: el umbral real de la búsqueda es desconocido | Demasiado bajo: `502` innecesarios; demasiado alto: no evita el bloqueo | Configurable; el log de origen (RF-11) y los `WARNING` del límite permiten ajustarlo con datos |
| R2 | El limitador añade una ida y vuelta a Redis por intento | +~1 ms por petición a Alcampo (~400 ms) | Aceptable. Con `ALCAMPO_RATE_LIMIT=0` desaparece |
| R3 | Relojes desincronizados entre instancias distorsionan la ventana | Límite algo más laxo o estricto | Hoy hay una instancia. Con varias, se exige NTP (documentado) |
| R4 | Una tarea compartida que nunca termina bloquearía a sus llamantes | Peticiones colgadas | El tiempo máximo (RF-7) va **dentro** de la tarea: siempre termina |
| R5 | Cambiar el término enviado a Alcampo (normalizado) | Resultados distintos | Verificado en vivo que son idénticos (§2) |

## 9. Secuencia de implementación y entrega

| # | Tarea | PR |
|---|---|---|
| 1 | `Settings`: 4 variables y validación (+ `.env.example` a mano) | 1 |
| 2 | `normalize_term`, clave de cache normalizada y término normalizado hacia Alcampo | 1 |
| 3 | Enfriamiento creciente (`activate(base, max)`) y duración real en el `ERROR` | 1 |
| 4 | `OutboundRateLimiter` (ventana deslizante) | 1 |
| 5 | Límite en el scraper + `UpstreamThrottledError`/`OutboundRateLimitedError` + `WARNING` en el handler | 2 |
| 6 | Tiempo máximo por búsqueda | 2 |
| 7 | `InFlightSearches` + cableado en el `lifespan` + log de origen | 2 |
| 8 | Integración transversal (casos límite de la spec) | 2 |
| 9 | Docs y verificación manual con `docker compose` | 2 |

| Bloque | `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| PR 1 (T1–T4) | ~110 | ~220 | — | ~330 |
| PR 2 (T5–T9) | ~120 | ~220 | ~50 | ~390 |
| **Total** | ~230 | ~440 | ~50 | **~720** |

Más de 400 líneas: **2 PRs encadenados** (D10), cada uno por debajo del límite.

## 10. Qué no cambia

Contrato de la API, autenticación, formato de logs, `retry.py`, el orden cache → enfriamiento → Alcampo, y el comportamiento con Redis caído (`500`, spec 007).
