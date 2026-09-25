# Tasks 003 — Logging

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 2 PRs encadenados. Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio (**suite completa**, no solo el fichero de la tarea). Marcar `[x]`, proponer el commit y **parar**.
5. **Tareas sin RED posible** (verifican algo ya construido): se comprueba que el test detecta el fallo rompiendo a propósito el comportamiento y viendo que falla, y se anota aquí. Lección de la 002.

Formato de commit: `<tipo>(003-alcampo-scraper-logging): <descripción en inglés> (Tn)`.

---

## PR1 — Infraestructura: formato, request id, `500` y `502`

### [x] T1 — Validar `LOG_LEVEL`
- **RED:** en `tests/core/test_config.py`:
  - `LOG_LEVEL=debug` → `log_level == "DEBUG"`;
  - `LOG_LEVEL=Warning` → `"WARNING"`;
  - `LOG_LEVEL=VERBOSE` → `ValidationError`;
  - el default sigue siendo `"INFO"`.
- **GREEN:** `field_validator("log_level")` en `Settings` que pasa a mayúsculas y comprueba contra `{"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}` (plan-D11).
- **Depende:** —
- **RF:** RF-2
- **Hecho cuando:** los 4 casos pasan.

### [x] T2 — `configure_logging`, `request_id_var` y factoría de `LogRecord`
- **RED:** `tests/core/test_logging.py`:
  - `configure_logging("INFO", stream=buf)` + `getLogger("x").info("hola")` → `buf` contiene **una** línea con, en este orden: timestamp, `INFO`, `[-]`, `x` y `hola`;
  - con `request_id_var` fijado a `"abc"`, la línea lleva `[abc]` y `caplog.records[-1].request_id == "abc"`;
  - llamar dos veces a `configure_logging` no duplica la línea en `buf`;
  - tras `configure_logging`, `caplog` **sigue** capturando (verificación del plan §2);
  - `configure_logging("WARNING", …)` → un `info` no aparece en `buf`.
  - Fixture que restaura el nivel y los handlers del logger raíz y la factoría de `LogRecord` tras cada test (plan R1).
- **GREEN:** `app/core/logging.py`: `request_id_var`, `LOG_FORMAT`, envoltorio idempotente de la factoría (plan-D4) y handler propio marcado y sustituible (plan-D3).
- **Depende:** —
- **RF:** RF-1, RF-4
- **Hecho cuando:** todos los casos pasan y la suite completa sigue en verde.

### [x] T3 — `configure_logging` en el `lifespan`
> **Ajustes durante la implementación (2026-09-25):** (1) se prueba con `ERROR` y `DEBUG`, no con `WARNING`: el logger raíz ya está en `WARNING` por defecto y el test habría pasado sin implementar nada. (2) El conftest de integración se dividió en `integration_env` (entorno, `get_settings.cache_clear()` y restauración del logging) y `client`: `get_settings()` está cacheado, y sin limpiar la cache un cambio de `LOG_LEVEL` no se vería y se filtraría a otros tests.
- **RED:** en `tests/integration/test_logging_integration.py` (nuevo): con `LOG_LEVEL=WARNING`, al arrancar la app (`with TestClient(...)`) el logger raíz queda en `WARNING`; con `LOG_LEVEL=DEBUG`, en `DEBUG`.
- **GREEN:** llamar a `configure_logging(settings.log_level)` al principio del `lifespan`.
- **Depende:** T1, T2
- **RF:** RF-1
- **Hecho cuando:** pasa y **toda la suite de integración de las specs 001 y 002 sigue en verde** (plan §6).

### [x] T4 — Middleware: request id, inicio/fin y `X-Request-ID`
> **Ajuste (2026-09-25):** se descartó el caso "fuera de una petición, `request_id_var.get() == '-'`". `TestClient` ejecuta la app en otro hilo, así que en el hilo del test el `ContextVar` vale `-` con o sin middleware: el test no podría fallar nunca. El valor `-` fuera de petición ya lo cubre T2.
- **RED:** en `tests/integration/test_logging_integration.py`, con la búsqueda mockeada por `respx`:
  - `GET /api/v1/products?…` → registros `request started` (método, ruta y parámetros) y `request finished` (status `200` y `duration_ms`), con el **mismo** `request_id` de 32 hex;
  - la respuesta lleva `X-Request-ID` igual a ese id;
  - `term="   "` (`422`) → también hay inicio, fin con status `422` y `X-Request-ID`;
  - petición con `X-Request-ID: abc` → respuesta y registros usan otro id de 32 hex, no `abc`;
  - dos peticiones seguidas → ids distintos;
  - fuera de una petición (tras la respuesta), `request_id_var.get() == "-"`.
- **GREEN:** `app/middleware/request_context.py` con `RequestContextMiddleware(BaseHTTPMiddleware)` (plan-D5). Valores del cliente con `%r` (plan-D10). Registrarlo en `create_app()` como el más externo (plan-D6).
- **Depende:** T2
- **RF:** RF-3, RF-4, RF-5, RF-5b, RF-18 (parámetros de la petición)
- **Hecho cuando:** todos los casos pasan y la suite completa sigue en verde.

### [x] T5 — `500` controlado desde el middleware
- **RED:** en `tests/integration/test_logging_integration.py`, añadiendo al app de test una ruta `/boom` que lanza `RuntimeError("secret detail")`:
  - respuesta `500 {"detail": "Internal server error"}` con `X-Request-ID`;
  - un registro `ERROR` con `exc_info` (traceback) y el mismo `request_id` que la cabecera;
  - `request finished` con status `500`;
  - `"secret detail"` **no** aparece en el body.
  - `TestClient(..., raise_server_exceptions=False)` para este caso.
- **GREEN:** `try/except Exception` alrededor de `call_next` en el middleware, con `logger.exception` y `JSONResponse(500)` (plan-D2).
- **Depende:** T4
- **RF:** RF-6
- **Hecho cuando:** todos los casos pasan.

### [x] T6 — `CooldownActiveError` y nivel del `502` según el tipo
- **RED:**
  - `tests/test_exceptions.py`: `CooldownActiveError` es subclase de `UpstreamUnavailableError`;
  - `tests/services/test_product_service.py`: con la marca activa, un miss lanza `CooldownActiveError`;
  - `tests/api/test_products_route.py` (servicio falso, `caplog`):
    - `UpstreamUnavailableError("upstream returned 503")` → `502` y un `ERROR` que contiene el motivo, `'28001'` y `'leche'` (con `%r`);
    - `CooldownActiveError("WAF cooldown active")` → `502`, un `WARNING` y **ningún** `ERROR`;
    - `UpstreamBlockedError("WAF challenge")` → `ERROR` (no es enfriamiento).
- **GREEN:** la excepción en `app/exceptions.py`; `ProductService` la lanza en lugar de la clase base; el handler de `main.py` registra `WARNING` si es `CooldownActiveError` y `ERROR` en el resto (plan-D1, plan-D8).
- **Depende:** T4
- **RF:** RF-7, RF-12
- **Hecho cuando:** todos los casos pasan y la suite completa sigue en verde. **Fin de PR1.**

---

## PR2 — Eventos de dominio y docs

### [x] T7 — Logs de reintentos
- **RED:** en `tests/scrapers/test_retry.py`, con `caplog` y `url="/search?q=leche"`:
  - `503, 200` → 1 `WARNING` con intento `1`, status `503`, la URL y la espera;
  - `ConnectTimeout, 200` → 1 `WARNING` con el tipo de error `ConnectTimeout`;
  - `503 × 3` → 2 `WARNING` + 1 `ERROR` con `attempts=3` y la URL;
  - `404` → 1 `ERROR` con status `404`, sin `WARNING`;
  - challenge del WAF → **ningún** registro en `retry.py` (lo registra el servicio; plan-D8);
  - sin pasar `url` → el mensaje usa `'-'`.
- **GREEN:** parámetro `url: str = "-"` y `logger` en `app/scrapers/retry.py` (plan-D7).
- **Depende:** —
- **RF:** RF-8, RF-9, RF-10
- **Hecho cuando:** los casos nuevos pasan y los tests existentes de `test_retry.py` siguen en verde sin tocarlos.

### [ ] T8 — Scraper: URL a los reintentos y cuerpo inválido
- **RED:** en `tests/scrapers/test_alcampo_search.py`:
  - el scraper pasa a `send_with_retry` una `url` que contiene la ruta de búsqueda y `q=leche`;
  - `200` con HTML → `ERROR` que menciona JSON inválido y la URL;
  - `200` con `{"foo": 1}` → `ERROR` que menciona schema inesperado y la URL.
  - Ajustar el `fake_send_with_retry` del test de la 002 (T7) para aceptar `url`.
- **GREEN:** construir la URL, pasarla y registrar en los dos `except`, separando `JSONDecodeError` de `ValidationError`.
- **Depende:** T7
- **RF:** RF-13
- **Hecho cuando:** todos los casos pasan.

### [ ] T9 — Servicio: challenge del WAF y enfriamiento
- **RED:** en `tests/services/test_product_service.py`, con `caplog`:
  - el scraper lanza `UpstreamBlockedError` → 1 `ERROR` que dice que la IP está bloqueada y que empieza un enfriamiento de `180` s;
  - con `waf_cooldown_seconds=0` → el `ERROR` dice que el enfriamiento está desactivado;
  - un `UpstreamUnavailableError` normal → ningún registro en el servicio (lo registra el handler).
- **GREEN:** `logger.error` en el `except UpstreamBlockedError` de `ProductService`.
- **Depende:** T6
- **RF:** RF-11
- **Hecho cuando:** todos los casos pasan.

### [ ] T10 — Mapper y cache
- **RED:**
  - `tests/mappers/test_map_search.py`, con `caplog`:
    - 3 productos, 1 sin `name` (`retailerProductId="1"`) → 1 `WARNING` con `discarded=1` y `'1'`;
    - 2 productos, los 2 rotos → 1 `ERROR` (no `WARNING`);
    - fixture sin resultados → ningún registro;
    - fixture real sin productos rotos → ningún registro.
  - `tests/services/test_search_cache.py`: valor corrupto → 1 `WARNING` con la clave `search:5:leche`.
- **GREEN:** contador y lista de ids en `map_search` con un registro al final (plan-D9); `logger.warning` en el `except ValueError` de `SearchCacheRepository.get`.
- **Depende:** —
- **RF:** RF-14, RF-15, RF-16
- **Hecho cuando:** todos los casos pasan.

### [ ] T11 — Integración transversal: correlación, secretos e inyección
- **RED:** en `tests/integration/test_logging_integration.py`:
  - **correlación:** `503` persistente → todos los registros de la petición (inicio, `WARNING` de reintento, `ERROR` agotado, `ERROR` del `502`, fin) comparten `request_id`, y coincide con `X-Request-ID`;
  - **secretos (RF-17):** respuesta mockeada con `Set-Cookie: VISITORID=secret-cookie-value; Path=/` y `Set-Cookie: global_sid=secret-sid-value` → ninguno de los dos valores aparece en `caplog.text`;
  - **inyección (RF-18):** `term="leche\nERROR fake injected"` → no existe ningún registro cuyo mensaje empiece por `ERROR fake`, y el mensaje de `request started` contiene la secuencia escapada.
- **GREEN:** no debería hacer falta código nuevo. Como no hay RED real, **verificación por mutación:** quitar temporalmente el `%r` de la línea de inicio y comprobar que el test de inyección falla; anotar el resultado aquí.
- **Depende:** T4–T10
- **RF:** RF-4, RF-5, RF-17, RF-18
- **Hecho cuando:** todos los casos pasan y la mutación queda anotada.

### [ ] T12 — Docs y verificación manual
- **RED:** ninguno propio (el test de `.env.example` de la 002 ya cubre `LOG_LEVEL`).
- **GREEN:** README: sección **Logging** con `LOG_LEVEL` (niveles válidos y que no distingue mayúsculas), ejemplo de línea, `X-Request-ID` (qué es y para qué sirve al reportar un error), que el id del cliente se ignora, qué eventos quedan como `ERROR` y cuáles como `WARNING`, y que nunca se registran cookies ni cabeceras. Actualizar el "Estado" y quitar el logging de las limitaciones.
- **Verificación manual:** Redis efímero con `docker run --rm -d -p 6379:6379 --name alcampo-redis redis:7` (se para al terminar); `uvicorn app.main:app` con `LOG_LEVEL=INFO`; 1 búsqueda real (≥10 min desde la última petición a Alcampo) y 1 con `term` de 51 caracteres. Comprobar en la consola el formato, el request id, las líneas de inicio y fin, y `X-Request-ID` en la respuesta de `curl -i`. Anotar el resultado aquí.
- **Depende:** T11
- **RF:** RNF-6
- **Hecho cuando:** el README está revisado, la verificación manual queda anotada y el contenedor de Redis está parado. **Fin de PR2.**

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T2, T3 |
| RF-2 | T1 |
| RF-3 | T4 |
| RF-4 | T2, T4, T11 |
| RF-5 | T4, T5, T11 |
| RF-5b | T4 |
| RF-6 | T5 |
| RF-7 | T6 |
| RF-8 | T7 |
| RF-9 | T7 |
| RF-10 | T7 |
| RF-11 | T9 |
| RF-12 | T6 |
| RF-13 | T8 |
| RF-14 | T10 |
| RF-15 | T10 |
| RF-16 | T10 |
| RF-17 | T11 |
| RF-18 | T4, T11 |

Ningún RF queda huérfano. RNF: RNF-1 (todas, sin dependencias nuevas), RNF-2 (asumido), RNF-3 (ruff `ANN`), RNF-4 (`getLogger(__name__)` en T4–T10), RNF-5 (`caplog` en todas), RNF-6 (T12).
