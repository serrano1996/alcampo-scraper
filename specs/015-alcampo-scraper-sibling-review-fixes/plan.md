# Plan 015 — Fallos encontrados por las revisiones de dia-scraper

- **Estado:** aprobado (2026-10-09)
- **Fecha:** 2026-10-09
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D4**; las de este plan, **D1…**.
- **Base:** las correcciones ya hechas en `dia-scraper`: `app/scrapers/retry.py`, `app/middleware/request_context.py`, `app/core/config.py`, `scripts/lock.sh`, `Dockerfile`, `.github/workflows/ci.yml`, `app/services/{redis_circuit,rate_limiter,cooldown}.py` y sus tests.

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `app/scrapers/retry.py` | captura `httpx.RequestError`; `parse_retry_after` devuelve `None` ante `OverflowError` o una espera no finita | RF-1, RF-2 |
| `app/core/config.py` | `api_keys`: `exclude=True` | RF-3 |
| `app/middleware/request_context.py` | redacción por subcadena, `multi_items()`, tope de 500 caracteres; el `500` registra tipo y frames | RF-4, RF-5, RF-10 |
| `scripts/lock.sh` | tercer lock (`requirements-build.lock`), solo `--upgrade`, limpieza siempre | RF-6 |
| `requirements-build.lock` | **nuevo**, generado | RF-6 |
| `Dockerfile`, `.github/workflows/ci.yml`, `.dockerignore` | instalan el lock de build con hashes y construyen con `--no-build-isolation` | RF-6 |
| `app/services/redis_circuit.py` | una sola prueba a la vez | RF-7 |
| `app/services/rate_limiter.py` | `_acquire_in_redis` devuelve si admite; la devolución del hueco va aparte | RF-8 |
| `app/services/waf_cooldown.py` | `LocalCooldown.hold(duration)`; `activate` lo llama tras escribir en Redis; `is_active` mira lo local primero | RF-9 |
| `README.md` | redacción, locks, comportamiento sin Redis | — |

## 2. Decisiones de diseño

**D1 — `RequestError` como fallo de transporte** (spec-D1): `except httpx.RequestError` en el mismo sitio que `TransportError`, que ya es una subclase. El motivo del log es `type(exc).__name__`, como hoy.

**D2 — `Retry-After` desbordado = ausente**: se añade `OverflowError` a las excepciones de `parsedate_to_datetime`, y un valor que no sea finito (`math.isfinite`) cuenta como ausente. Un entero enorme pero finito sigue acotado a 60 s, como hoy.

**D3 — Redacción y parámetros copiados de Dia** (spec-D2):
- `_is_secret_name(name)`: el nombre recortado, en minúsculas y con `-` como `_`, contiene `key`, `token`, `secret`, `auth` o `pass`.
- `redact_params` pasa a recibir y devolver la lista de pares `multi_items()`, en orden.
- `_params_for_log` hace `repr` de la lista y la corta a `MAX_PARAMS_LOGGED = 500` con `...(truncated, N chars)`.
- Ninguno de los parámetros propios de la API (`postal_code`, `term`, `page`, `page_size`) contiene esas subcadenas; un test lo fija.

**D4 — El `500`, sin mensaje**: `logger.error("unhandled error type=%s frames=%r", type(exc).__name__, frames)`, donde `frames` es la lista `(fichero, línea, función)` de `traceback.extract_tb`, igual que Dia. Sin `exc_info`: el traceback estándar incluye el mensaje.

**D5 — Lock de build, igual que Dia (su spec 007 T5)**:
- `lock.sh` extrae `[build-system].requires` con `tomllib` y lo compila a `requirements-build.lock`.
- Solo acepta `""` o `--upgrade`; cualquier otra cosa sale con código 2.
- `rm -rf alcampo_scraper.egg-info build` siempre, también si falla.
- **Imagen:** `requirements-build.lock` y `requirements.lock` con `--require-hashes`, luego `pip install --no-deps --no-build-isolation .`.
- **CI:** lo mismo con `requirements-dev.lock` y `-e .`.
- `.dockerignore` readmite el lock de build.

**D6 — Circuito: una prueba a la vez**: un indicador `_probing`. Pasado el periodo, la primera llamada lo pone y prueba; las demás reciben `RedisCircuitOpenError`. Se limpia en un `finally`. Así, un error ajeno o una cancelación dejan que la siguiente llamada pruebe, y solo una prueba fallida reabre el circuito y avisa.

**D7 — El rechazo del limitador, fuera del circuito**:
- `_acquire_in_redis` devuelve `count <= limit` en vez de lanzar.
- `acquire` hace la devolución (`ZREM`) en su propia llamada al circuito; si falla, deja un `DEBUG`, porque el hueco caduca con la ventana. Después lanza `OutboundRateLimitedError`.
- Vale igual para las dos ventanas (spec 010).

**D8 — Enfriamiento del WAF: lo local refleja lo de Redis** (spec-D3):
- `LocalCooldown.hold(duration, max_seconds)` fija `until = max(until, now + duration)` y recuerda `duration` como la última. Así, si Redis cae, el crecimiento local sigue desde ahí.
- `WafCooldownRepository.activate` lo llama con la duración que devolvió Redis.
- `is_active()` = lo local o Redis; antes solo se miraba lo local si Redis fallaba.

## 3. Estrategia de test

| RF | Test | Fichero |
|---|---|---|
| RF-1 | `DecodingError` y `TooManyRedirects`: se reintentan; agotados, `UpstreamUnavailableError` | `tests/scrapers/test_retry.py` |
| RF-2 | la fecha con año `99999999999999999999` y `inf` → `None`; un `429` con ella usa el backoff | `test_retry.py` |
| RF-3 | el token no aparece en `model_dump()` ni en `model_dump_json()` | `tests/core/test_config.py` |
| RF-4, RF-5 | `access_token`, `api-key`, `apiToken`, `password`, `client_secret` → `***`; los parámetros de la API, en claro; `t=a&t=b` registra los dos; una query de 2000 caracteres se corta | `tests/middleware/test_request_context.py` |
| RF-10 | un `500` cuyo mensaje lleva un marcador: el marcador no está en el log y el tipo sí; sin `exc_info` | `test_request_context.py` |
| RF-6 | el lock de build fija con hash lo de `[build-system].requires`; el `Dockerfile` y la CI lo instalan y usan `--no-build-isolation`; `.dockerignore` lo readmite; `lock.sh` rechaza otras opciones | `tests/infra/*` |
| RF-7 | 20 llamadas concurrentes pasado el periodo → 1 prueba y 19 `RedisCircuitOpenError`, un solo `WARNING`; un error ajeno en la prueba deja probar a la siguiente | `tests/services/test_redis_circuit.py` |
| RF-8 | con el `ZREM` fallando, la segunda `acquire` se rechaza; un rechazo durante una prueba cierra el circuito | `tests/services/test_rate_limiter.py` |
| RF-9 | un enfriamiento iniciado con Redis sano sigue activo si Redis cae; varios desafíos con Redis sano: la duración local sigue a la de Redis | `tests/services/test_waf_cooldown.py` |

## 4. Entrega

Un PR de unas 300 líneas de código y tests, más el lock de build, que se genera y no se revisa línea a línea. T6 necesita Docker para generar el lock y probar el build.
