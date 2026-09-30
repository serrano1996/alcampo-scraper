# Plan 006 — Refactor y calidad

- **Estado:** aprobado (2026-09-30) por instrucción del usuario ("termina lo que queda ya"), sin revisión intermedia. Entrega: un solo PR (§7)
- **Fecha:** 2026-09-30
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D3**; las de este plan son **D1…**

## 1. Verificaciones previas (2026-09-30)

| Supuesto | Experimento | Resultado | Consecuencia |
|---|---|---|---|
| Cuánto cuesta `mypy --strict` | `mypy` 2.3.1 con `strict` y el plugin de Pydantic sobre `app/` | **6 errores** en 4 ficheros: un `StreamHandler` sin tipo, dos `Any` devueltos, un `TypeAdapter` sin anotar, un `TypeVar` acotado a `BaseModel` que no admite `list[...]`, y un `params` de httpx tipado como `dict[str, object]` | Una sola tarea (T1) |
| Qué pide el `StarletteDeprecationWarning` | Lectura de `starlette/testclient.py` (1.7.0) | Prefiere `httpx2` para el cliente de pruebas; con solo `httpx`, avisa | RF-12: `httpx2` como dependencia **de desarrollo**; la app sigue con `httpx` |
| ¿Rompe algo `httpx2`? | Instalado y `pytest -q -W error` | **394 pasan**, ningún warning | RF-11 sin excepciones documentadas |
| RF-8 (README de `postal_code`) | Lectura del README tras la 007 | Ya describe los 5 dígitos y el `422` | Se da por cubierto por la 007; se comprueba en T7 |
| RF-7 (docstring de `dependencies.py`) | `grep` de `AppState`/"spec 006" | Solo queda la mención "llega en la spec 006" | Se corrige al implementar `AppState` (T3) |

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `pyproject.toml` | `[tool.mypy]` estricto + plugin de Pydantic; límites de versión; `mypy` y `httpx2` en `dev`; `filterwarnings = ["error"]` | RF-3, RF-10…RF-12 |
| `app/core/logging.py`, `app/scrapers/retry.py`, `app/scrapers/alcampo_session.py`, `app/scrapers/alcampo_search.py` | los 6 errores de tipos | RF-3 |
| `app/core/state.py` | **nuevo**: `AppResources` (dataclass) y `resources(app)` | RF-1 |
| `app/main.py`, `app/core/dependencies.py` | el `lifespan` guarda un único `AppResources`; las dependencias lo leen | RF-1, RF-2, RF-7 |
| `app/middleware/request_context.py` | middleware ASGI puro | RF-5, RF-6 |
| `.github/workflows/ci.yml` | **nuevo** | RF-9 |
| `tests/infra/test_ci_workflow.py` | **nuevo**: el workflow ejecuta los 5 pasos | RF-9 |
| `AGENTS.md`, `docs/constitution.md`, `README.md` | `mypy` en el cierre de cada tarea; CI | RF-4, RNF-3 |

## 3. Decisiones de diseño

**D1 — `AppResources` como `dataclass` y un único atributo `app.state.resources`.** El `lifespan` construye un `AppResources(settings, redis, redis_circuit, rate_limiter, cooldown, in_flight, region_sessions, region_service)` y lo guarda entero. `resources(app) -> AppResources` es el único punto que lee `app.state` (y hace el `cast`), así que un nombre mal escrito en cualquier otro sitio es un error de `mypy` (RF-1).
- *Descartada:* subclasificar `starlette.datastructures.State`. `FastAPI` crea su propio `State` y cambiarlo exige tocar internos.
- *Descartada:* mantener también los atributos sueltos por compatibilidad. Duplicaría la fuente de verdad; los 12 usos en tests (`client.app.state.redis`/`.settings`) pasan a `resources(client.app).redis`.

**D2 — Middleware ASGI puro.** Una clase con `__call__(scope, receive, send)`: solo actúa en `scope["type"] == "http"`; genera el request id, registra el inicio con los parámetros redactados (`QueryParams(scope["query_string"])`), envuelve `send` para añadir `X-Request-ID` al `http.response.start` y guardar el estado, y registra el fin en un `finally`. Ante una excepción sin tratar: `logger.exception` y, si la respuesta no ha empezado, envía el `500` propio con `X-Request-ID`; si ya empezó, relanza (no se puede cambiar una respuesta en curso).
- *Descartada:* dejar `BaseHTTPMiddleware`. Funciona, pero ejecuta el endpoint en otra tarea, copia el contexto y tiene limitaciones conocidas con respuestas en streaming.

**D3 — `mypy` solo sobre `app/`** (spec-D1): `[tool.mypy] files = ["app"]`, `strict = true`, `plugins = ["pydantic.mypy"]`, `python_version = "3.11"`. Los 6 errores se corrigen con tipos, no con `# type: ignore`.

**D4 — Límites de versión:** `<` la siguiente versión mayor instalada (`pydantic>=2,<3`, `redis>=8,<9`, `pytest>=9,<10`…); para paquetes `0.x` (`fastapi`, `httpx`, `uvicorn`, `respx`, `ruff`), `<1`.

**D5 — CI:** un job en `ubuntu-latest`, Python 3.11 (la mínima declarada): `pip install -e ".[dev]"`, `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`, `docker build .`. Sin publicar nada (spec §7). Se valida con un test que lee el YAML (el paquete `yaml` llega con `uvicorn[standard]`; si no está, el test se salta).

## 4. Regresiones previstas

| Tests | Por qué | Corrección |
|---|---|---|
| 12 usos de `client.app.state.redis`/`.settings` | los recursos pasan a `AppResources` | `resources(client.app).redis`, sin cambiar aserciones |
| Tests del middleware y de logging | reimplementación | ninguna: son la red de seguridad (RF-6) |

## 5. Estrategia de test

- **RF-1:** un test de `resources(app)` y otro que comprueba que el `lifespan` deja un `AppResources` con todos sus campos; `mypy` detecta el resto.
- **RF-3:** `mypy` en el cierre de cada tarea y en el CI.
- **RF-5/RF-6:** la suite existente sin cambios de aserciones + un test nuevo: una respuesta en streaming lleva `X-Request-ID` (lo que `BaseHTTPMiddleware` hacía peor).
- **RF-9:** `tests/infra/test_ci_workflow.py`.
- **RF-10…RF-12:** `pytest -W error` pasa porque `filterwarnings = error` está en la configuración; un test comprueba que cada dependencia de `pyproject.toml` tiene límite superior.

## 5b. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | El CI no se ha ejecutado nunca (no hay remoto) | Se validan los mismos comandos en local y el YAML con un test; se anota como pendiente |
| R2 | El middleware ASGI cambia un detalle sutil de logs o del `500` | Suite existente intacta + test de streaming |

## 6. Secuencia

1. `mypy` estricto y los 6 errores.
2. `mypy` en el cierre de cada tarea (AGENTS.md, constitución).
3. `AppResources` tipado.
4. Middleware ASGI puro.
5. Dependencias: límites, `httpx2`, `filterwarnings = error`.
6. Workflow de CI.
7. Docs y comprobaciones finales (RF-7, RF-8).

## 7. Entrega

~300 líneas: un solo PR.
