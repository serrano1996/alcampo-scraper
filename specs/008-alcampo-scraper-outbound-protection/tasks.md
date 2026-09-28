# Tasks 008 — Protección de salida hacia Alcampo

- **Estado:** aprobado (2026-09-28)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 2 PRs encadenados (plan-D10): **PR 1** = T1–T4 (~330 líneas), **PR 2** = T5–T9 (~390 líneas).

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio (**suite completa**). Marcar `[x]`, proponer el commit y **parar**.
5. **Regresiones:** se corrigen en la tarea que las provoca (plan §6).
6. **Tiempo y concurrencia:** relojes y esperas inyectables, y `asyncio.Event` para controlar el orden. Nunca `sleep` real en los tests.
7. **`.env.example`:** lo edita el usuario a mano (regla global de permisos). Cuando una tarea lo necesite, se para y se le pide.

Formato de commit: `<tipo>(008-alcampo-scraper-outbound-protection): <descripción en inglés> (Tn)`.

---

## PR 1 — Piezas aisladas

### [x] T1 — Variables nuevas en `Settings`
> **Nota (2026-09-28):** RED real (11 tests: `AttributeError` por los campos inexistentes y `DID NOT RAISE` en las validaciones). Añadidos dos casos no previstos: `max == base` es válido y el mensaje de error nombra `WAF_COOLDOWN_MAX_SECONDS`. Ningún test existente usaba un enfriamiento > 900, así que la validación cruzada no rompió nada. **`.env.example`:** el usuario añadió las 4 variables a mano (regla global de permisos); el test de sincronización lo confirma.
- **RED:** en `tests/core/test_config.py`:
  - valores por defecto: `alcampo_rate_limit == 20`, `alcampo_rate_window_seconds == 60`, `search_timeout_seconds == 15`, `waf_cooldown_max_seconds == 900`;
  - `ALCAMPO_RATE_LIMIT=0` es válido; `-1` → `ValidationError`;
  - `ALCAMPO_RATE_WINDOW_SECONDS=0` → `ValidationError`;
  - `SEARCH_TIMEOUT_SECONDS=0` y `-1` → `ValidationError`;
  - `WAF_COOLDOWN_MAX_SECONDS=100` con `WAF_COOLDOWN_SECONDS=180` → `ValidationError`; `WAF_COOLDOWN_SECONDS=0` con cualquier máximo `≥ 0` → válido.
  - Añadir las 4 variables a la lista `OPTIONAL` del fixture que limpia el entorno.
- **GREEN:** los 4 campos con `Field` y un `model_validator(mode="after")` para `max >= base` (plan-D8).
- **Regresión (plan §6):** `tests/core/test_env_example.py` fallará → **parar y pedir al usuario** que añada a `.env.example` `ALCAMPO_RATE_LIMIT`, `ALCAMPO_RATE_WINDOW_SECONDS`, `SEARCH_TIMEOUT_SECONDS` y `WAF_COOLDOWN_MAX_SECONDS` (con un comentario breve cada una).
- **Depende:** —
- **RF:** RF-6, RF-7 (validación), RF-10 (validación)
- **Hecho cuando:** los casos pasan y el test de `.env.example` está en verde.

### [x] T2 — Término normalizado
> **Nota (2026-09-28):** RED real: `ImportError` de `normalize_term` en el repositorio; en el servicio, `Leche` iba a Alcampo (`['Leche'] == []`) y el término viajaba sin normalizar (`['LECHE   entera']`). La normalización vive en `_cache_key`, así que `get` y `set` la aplican siempre sin que el llamante lo recuerde; el servicio solo la usa para el término que envía a Alcampo. Sin regresiones: las claves literales de los tests ya estaban en minúsculas.
- **RED:**
  - `tests/services/test_search_cache.py`: `normalize_term("Leche") == "leche"`, `"LECHE"` → `"leche"`, `"leche   entera"` → `"leche entera"`, `"leche\tentera"` → `"leche entera"`, `" leche "` → `"leche"`; `set` con `Leche` y `get` con `LECHE` encuentran la misma entrada (`search:5:leche`).
  - `tests/services/test_product_service.py`: con `leche` en cache, `search(term="Leche")` → acierto, 0 llamadas al scraper, `search.term == "Leche"`; sin cache, `search(term="LECHE")` → el scraper recibe `"leche"` y la respuesta dice `search.term == "LECHE"`.
- **GREEN:** `normalize_term` en `search_cache.py`; la clave se construye con él; el servicio pasa el término normalizado al scraper (plan-D1).
- **Depende:** —
- **RF:** RF-2
- **Hecho cuando:** los casos pasan y la suite sigue en verde.

### [x] T3 — Enfriamiento creciente
> **Nota (2026-09-28):** RED real (`ImportError` de `WAF_COOLDOWN_LAST_KEY`, `TypeError` por `base_seconds` y el log sin `cooldown_s=360`). **Test sustituido:** `test_activate_renews_the_ttl` (spec 002: 180 y después 60 → TTL 60) pasa a `test_recent_second_challenge_doubles_the_cooldown` (180 y después → 360), con un comentario que lo explica; los otros 4 de `test_waf_cooldown.py` se reescriben con la nueva firma sin cambiar lo que comprueban. **Cambio de orden:** el servicio activa el enfriamiento **antes** de registrar el `ERROR`, porque el log necesita la duración aplicada; si Redis fallara en ese momento, se pierde esa línea, pero la petición acaba en `500` igualmente (Redis caído, spec 007).
- **RED:** en `tests/services/test_waf_cooldown.py` (con la nueva firma `activate(base_seconds=…, max_seconds=…) -> int`):
  - sin challenge previo → devuelve 180, `waf:cooldown` con TTL 180 y `waf:cooldown:last == "180"` con TTL 900;
  - con `waf:cooldown:last = 180` → devuelve 360;
  - secuencia de 5 llamadas → 180, 360, 720, 900, 900;
  - sin `waf:cooldown:last` (caducado: se borra la clave) → vuelve a 180;
  - `base_seconds=0` → devuelve 0 y no escribe ninguna clave.
  - En `tests/services/test_product_service.py`: tras dos challenges, el segundo `ERROR` dice `cooldown_s=360` (RF-9).
- **GREEN:** `activate` según plan-D7; el servicio pasa `waf_cooldown_seconds` y `waf_cooldown_max_seconds` y registra la duración devuelta.
- **Regresión (plan §6):** las llamadas `activate(ttl_seconds=…)` de `test_waf_cooldown.py` y `test_product_service.py` pasan a la nueva firma. `test_activate_renews_the_ttl` (180 y después 60) se sustituye por el caso "segundo challenge reciente → 360", y se anota aquí.
- **Depende:** T1
- **RF:** RF-8, RF-9, RF-10
- **Hecho cuando:** los casos pasan y la sustitución del test queda anotada.

### [x] T4 — `OutboundRateLimiter`
> **Nota (2026-09-28):** RED real (`ImportError` de `OutboundRateLimitedError` y del módulo). Añadido un caso: la excepción es `UpstreamUnavailableError` (conserva el `502`). **Mutación adelantada de T8:** sin el `ZREM` del intento rechazado, falla `test_a_rejected_attempt_does_not_consume_quota` y solo ese; restaurado. La ventana borra las entradas con antigüedad `>= window` (`ZREMRANGEBYSCORE -inf now-window`). **Fin del PR 1.**
- **RED:** `tests/services/test_rate_limiter.py`, con `fakeredis` y un reloj falso (`now: Callable[[], float]`):
  - límite 2 / 60 s: dos `acquire()` pasan y el tercero lanza `OutboundRateLimitedError` (se crea la excepción en `app/exceptions.py` como subclase de `UpstreamUnavailableError`; la jerarquía `UpstreamThrottledError` llega en T5);
  - tras avanzar el reloj más de 60 s, vuelve a pasar;
  - **un intento rechazado no consume cupo:** tras el rechazo, `ZCARD` sigue en 2;
  - **ventana deslizante:** en `t=0` y `t=59` pasa uno cada vez; en `t=61` pasa (el de `t=0` ha salido), en `t=62` no;
  - límite 0 → nunca lanza y no crea la clave `ratelimit:alcampo`;
  - la clave tiene TTL (no crece para siempre).
- **GREEN:** `app/services/rate_limiter.py` según plan-D3.
- **Depende:** T1
- **RF:** RF-3, RF-6
- **Hecho cuando:** los casos pasan. **Fin del PR 1.**

---

## PR 2 — Cableado

### [x] T5 — Límite en el scraper y degradación prevista
> **Nota (2026-09-28):** RED real (`ImportError` de `UpstreamThrottledError`, `TypeError` por `rate_limiter` en 11 tests del scraper, el handler sin `WARNING` y la integración con `200` en vez de `502`). **Regresión no prevista en plan §6:** `test_persistent_5xx_returns_502_after_retry_max_attempts_calls` y `test_alcampo_404_returns_502_with_a_single_call` comprobaban "no se cachea nada" con `dbsize() == 0`, y ahora existe la clave legítima `ratelimit:alcampo`. Se cambió la aserción por `keys("search:*") == []`, que es lo que pretendían, con un comentario; el test del `422` mantiene `dbsize() == 0` porque ahí no se toca Redis. **Cambio de texto del log:** el `WARNING` del enfriamiento pasa de `search rejected during WAF cooldown …` a `search throttled reason='WAF cooldown active' …`, común a toda la familia; ningún test dependía del texto antiguo. El README se actualiza en T9.
- **RED:**
  - `tests/test_exceptions.py`: `CooldownActiveError` y `OutboundRateLimitedError` son `UpstreamThrottledError`, que es `UpstreamUnavailableError`.
  - `tests/scrapers/test_alcampo_search.py`: con límite agotado, `search` lanza `OutboundRateLimitedError` y la ruta respx tiene **0** llamadas; con límite 1 y Alcampo devolviendo `503`, sale 1 petición y el reintento se corta con `OutboundRateLimitedError` (RF-5).
  - Integración (`tests/integration/test_products_endpoint.py`): con `ALCAMPO_RATE_LIMIT=1` y una búsqueda ya hecha, otra búsqueda **distinta** → `502` con el cuerpo de siempre, `WARNING` con el motivo, 0 peticiones nuevas a respx; la primera, repetida → `200` desde la cache (RF-4).
- **GREEN:** `UpstreamThrottledError` (plan-D5); el scraper recibe el limitador y llama a `acquire()` dentro de `send()` (plan-D4); `get_product_service` construye el limitador con `state.redis` y los settings; el handler del `502` registra `WARNING` con `reason=%r` para toda `UpstreamThrottledError`.
- **Regresión (plan §6):** los 2 constructores de `test_alcampo_search.py` reciben un limitador con límite 0. El test existente del `WARNING` de enfriamiento debe seguir pasando sin cambios.
- **Depende:** T4
- **RF:** RF-3, RF-4, RF-5
- **Hecho cuando:** los casos pasan y el log del enfriamiento no ha cambiado.

### [ ] T6 — Tiempo máximo por búsqueda
- **RED:**
  - `tests/services/test_product_service.py`: un scraper que espera un `asyncio.Event` que nunca se activa y `search_timeout_seconds=0.05` → `UpstreamUnavailableError` con `reason == "search timeout"`; el scraper queda cancelado.
  - Integración: la misma situación con respx (un `side_effect` que espera) → `502` y `ERROR` con `reason='search timeout'`.
- **GREEN:** `asyncio.timeout` alrededor de `scraper.search` en el servicio (plan-D6).
- **Depende:** T1
- **RF:** RF-7
- **Hecho cuando:** los casos pasan sin esperas reales de más de ~0,1 s.

### [ ] T7 — Búsquedas iguales simultáneas y log de origen
- **RED:**
  - `tests/services/test_in_flight.py`:
    - N `run` simultáneos con la misma clave → `fetch` se ejecuta **1** vez, todos reciben el mismo resultado; el primero es `miss` y el resto `shared`;
    - si `fetch` falla, todos reciben la misma excepción;
    - claves distintas → un `fetch` por clave;
    - tras terminar, la clave desaparece del registro (una búsqueda posterior vuelve a llamar a `fetch`);
    - cancelar la espera de un llamante no cancela la tarea de los demás (`shield`).
  - `tests/services/test_product_service.py`:
    - 10 `search` simultáneos de `leche` sin cache (scraper que espera un `asyncio.Event`) → 1 llamada al scraper y 10 respuestas iguales;
    - `leche` y `Leche` simultáneos → 1 llamada; cada respuesta lleva su propio `search.term`;
    - un challenge en la búsqueda compartida activa el enfriamiento **una** vez;
    - log `INFO "search served source=hit|miss|shared"` en cada caso (RF-11).
- **GREEN:** `app/services/in_flight.py` (plan-D2); `InFlightSearches` creado en el `lifespan` y guardado en `app.state`; el servicio lo recibe y ejecuta dentro la parte "enfriamiento + tiempo máximo + scraper + cache.set".
- **Regresión (plan §6):** el helper `make_service` de `test_product_service.py` crea un `InFlightSearches()`.
- **Depende:** T5, T6
- **RF:** RF-1, RF-11
- **Hecho cuando:** los casos pasan.

### [ ] T8 — Integración transversal
- **RED:** en `tests/integration/test_outbound_protection.py` (app real + `lifespan` + fakeredis + respx), los casos límite de la spec que aún no tengan test de integración:
  - enfriamiento activo y límite agotado → `502` por enfriamiento (se comprueba antes; el `WARNING` lo dice);
  - `hit` con el request id correcto en el log;
  - un challenge seguido de otro dentro de la ventana (simulado dejando `waf:cooldown:last` y borrando `waf:cooldown`) → el segundo `ERROR` dice `cooldown_s=360`.
- **Verificación por mutación:** quitar el `ZREM` del intento rechazado → el test de "no consume cupo" (T4) debe fallar; quitar el `shield` → el de cancelación (T7) debe fallar. Anotarlo aquí.
- **GREEN:** — (solo si algún caso revela un hueco; si pasa, se anota).
- **Depende:** T7
- **RF:** RF-1…RF-11 (transversal)
- **Hecho cuando:** los casos pasan y las mutaciones quedan anotadas.

### [ ] T9 — Docs y verificación manual
- **GREEN:**
  - README: en "Medidas antibaneo", las búsquedas iguales simultáneas, el término normalizado, el límite global (qué pasa al agotarlo y que el valor por defecto es una estimación), el tiempo máximo y el enfriamiento creciente; tabla de variables con las 4 nuevas; en "Logging", la línea `search served source=…`; "Estado" y limitaciones (el límite no conoce el umbral real del WAF; la agrupación es por proceso).
  - `.env.example` ya actualizado en T1 (el test de sincronización lo confirma).
- **Verificación manual:** `docker compose` con un override en el scratchpad (token sintético, como en la 005), **≥10 min desde la última petición a Alcampo**, como mucho 2 búsquedas reales:
  1. 10 búsquedas simultáneas de `yogur` → 10 × `200`, **1** petición a Alcampo en los logs, 1 `source=miss` y 9 `source=shared`.
  2. `YOGUR` → `200` desde la cache (`source=hit`), 0 peticiones nuevas.
  3. Recrear la API con `ALCAMPO_RATE_LIMIT=1`: una búsqueda de `pan` → `200` (2.ª petición real); otra de `arroz` → `502` inmediato con `WARNING` del límite y 0 peticiones nuevas.
  4. `docker compose down`, borrar el override. Anotar aquí.
- **Depende:** T8
- **RF:** RNF-4
- **Hecho cuando:** README revisado, verificación anotada y todo parado. **Fin del PR 2.**

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T7, T8, T9 |
| RF-2 | T2, T7, T9 |
| RF-3 | T4, T5 |
| RF-4 | T5, T8, T9 |
| RF-5 | T5 |
| RF-6 | T1, T4 |
| RF-7 | T1, T6 |
| RF-8 | T3, T8 |
| RF-9 | T3, T8 |
| RF-10 | T1, T3 |
| RF-11 | T7, T8, T9 |

Ningún RF queda huérfano. RNF: RNF-1 (`502` y cuerpo de siempre: T5, T6, T8), RNF-2 (sin dependencias nuevas, en todas), RNF-3 (sin red ni tiempo real, regla 6), RNF-4 (T1 y T9).
