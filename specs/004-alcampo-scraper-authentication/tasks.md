# Tasks 004 — Autenticación

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** un solo PR (~325 líneas).

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio (**suite completa**). Marcar `[x]`, proponer el commit y **parar**.
5. **Tareas sin RED posible:** se valida rompiendo a propósito el comportamiento y comprobando que el test falla. Se anota aquí.
6. **Regresiones:** se corrigen en la tarea que las provoca (plan §6).

Formato de commit: `<tipo>(004-alcampo-scraper-authentication): <descripción en inglés> (Tn)`.

---

### [x] T1 — `API_KEYS` en `Settings`
> **Nota (2026-09-28):** la regla global `Read(.env.*)`/`Edit(.env.*)` de `~/.claude/settings.json` bloquea ahora también la lectura y escritura de `.env.example` por Bash. El usuario añadió `API_KEYS` a mano (opción elegida para no debilitar la regla global); el test de sincronización confirma el cambio. T7 necesitará lo mismo. Verificación por mutación: sin `repr=False`, `test_api_keys_are_hidden_from_repr` falla.
- **RED:** en `tests/core/test_config.py`:
  - `API_KEYS=" a , ,b "` → `api_keys == frozenset({"a", "b"})`;
  - sin la variable → `frozenset()`;
  - `API_KEYS=" , ,"` → `frozenset()`;
  - con `API_KEYS="secret-one,secret-two"`, `repr(settings)` no contiene `secret-one` ni `secret-two`.
  - Añadir `API_KEYS` a la lista `OPTIONAL` del fixture que limpia el entorno.
- **GREEN:** `api_keys: Annotated[frozenset[str], NoDecode] = Field(default=frozenset(), repr=False)` con un `field_validator(mode="before")` que separa por comas y limpia (plan-D1).
- **Regresión (plan §6):** `tests/core/test_env_example.py` fallará por el campo nuevo → añadir `API_KEYS=` a `.env.example` (por Bash; `Write` está bloqueado en `.env*`).
- **Depende:** —
- **RF:** RF-7, RF-12 (`repr`)
- **Hecho cuando:** los casos pasan y la suite completa está en verde, incluido el test de `.env.example`.

### [x] T2 — `is_valid_api_key`
> **Verificación por mutación (2026-09-28):** comparando `str` en vez de bytes, los 2 tests no ASCII fallan con `TypeError`; restaurado, pasan. Añadido un caso no previsto: un token **configurado** no ASCII también funciona.
- **RED:** `tests/core/test_security.py`:
  - con `{"k1", "k2"}`, `"k1"` y `"k2"` son válidos y `"k3"` no;
  - `" k1"`, `"k1 "` y `"K1"` no son válidos (RF-5);
  - `"clé"` → `False`, **sin excepción** (plan §2: `compare_digest` con `str` no ASCII lanza `TypeError`);
  - con `frozenset()` → siempre `False`;
  - `None` y `""` → `False`.
- **GREEN:** `is_valid_api_key(candidate: str | None, valid_keys: frozenset[str]) -> bool` en `app/core/security.py`: bytes UTF-8, `compare_digest` contra **todos** los tokens, acumulando con `|=` (plan-D3).
- **Depende:** —
- **RF:** RF-4, RF-5
- **Hecho cuando:** todos los casos pasan.

### [x] T3 — Dependencia `require_api_key` y router protegido
> **Regresiones (2026-09-28):** al proteger el router fallaron 18 tests, exactamente en los 3 ficheros previstos en el plan §6 (7 en `test_products_route.py`, 7 en `test_products_endpoint.py`, 4 en `test_logging_integration.py`). Corregidas aquí: `integration_env` fija `API_KEYS=test-key` (constante `TEST_API_KEY`) y `client` la envía por defecto; `make_client` anula `require_api_key`.
- **RED:** `tests/integration/test_auth.py` (nuevo), con un `TestClient` **sin** cabecera por defecto:
  - sin `X-API-Key` → `401 {"detail": "Invalid or missing API key"}` con `WWW-Authenticate: ApiKey`;
  - `X-API-Key: ` (vacía) → el mismo `401`;
  - `X-API-Key: wrong` → **el mismo body** que sin cabecera;
  - `X-API-Key: test-key` → `200` (con la búsqueda mockeada);
  - `/health`, `/docs` y `/openapi.json` sin cabecera → `200`;
  - con `API_KEYS` vacío, incluso `X-API-Key: test-key` → `401` (fallo cerrado).
- **GREEN:** `api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)` y `require_api_key(request, api_key)` en `app/core/security.py`, que lee `request.app.state.settings.api_keys` (plan-D5) y lanza `HTTPException(401, headers={"WWW-Authenticate": "ApiKey"})` (plan-D4). En `app/api/v1/products.py`, `dependencies=[Security(require_api_key)]` en el router (plan-D2).
- **Regresiones (plan §6), en esta misma tarea:**
  - `tests/integration/conftest.py`: `integration_env` fija `API_KEYS=test-key`, y el fixture `client` envía `X-API-Key: test-key` por defecto (`TestClient(..., headers=...)`);
  - `tests/api/test_products_route.py`: `make_client` añade `app.dependency_overrides[require_api_key] = lambda: None`.
- **Depende:** T1, T2
- **RF:** RF-1, RF-2, RF-3, RF-6, RF-8, RF-15
- **Hecho cuando:** los casos pasan y **toda la suite de las specs 001–003 sigue en verde**.

### [x] T4 — Logs de rechazo y aviso de arranque sin tokens
> **Verificación por mutación (2026-09-28):** metiendo el valor recibido en el mensaje de rechazo, `test_invalid_key_is_logged_without_its_value` falla (`'wrong-secret-value'` aparece en `caplog.text`); restaurado, pasa. El test de secretos arranca la app con `LOG_LEVEL=DEBUG`: con `caplog.set_level(DEBUG)` solo no bastaría, porque el `lifespan` vuelve a fijar el nivel del raíz.
- **RED:** en `tests/integration/test_auth.py`, con `caplog`:
  - sin cabecera → 1 `WARNING` con `reason=missing` y la ruta `'/api/v1/products'`;
  - `X-API-Key: wrong-secret-value` → 1 `WARNING` con `reason=invalid`;
  - en ninguno de los dos casos aparecen `wrong-secret-value` ni `test-key` en `caplog.text`, a ningún nivel (`caplog.set_level(DEBUG)`);
  - arrancar con `API_KEYS` vacío → `WARNING` de arranque que dice que se rechazarán todas las peticiones a `/api/v1`;
  - arrancar con tokens → ese `WARNING` **no** aparece.
- **GREEN:** `logger.warning` en `require_api_key` con `reason` y `path` (con `%r`), nunca el valor (plan-D8); aviso en el `lifespan`, justo después de `configure_logging` (plan-D6).
- **Depende:** T3
- **RF:** RF-9, RF-10, RF-12
- **Hecho cuando:** todos los casos pasan.

### [x] T5 — Ocultar parámetros con nombre de secreto
> **Ajuste (2026-09-28):** el test de integración comprueba solo los registros `app.*`, no todo `caplog.text`. `TestClient` usa httpx, que registra la URL que **pide el cliente de test** (`secret-in-url` incluido); se comprobó que tras el GREEN ese es el único registro con el secreto. En producción ese log sería del cliente, no nuestro. RED real antes del GREEN: la línea de inicio de la app sí contenía `secret-in-url`.
- **RED:**
  - `tests/middleware/test_request_context.py` (nuevo): `redact_params({"api_key": "s", "Token": "t", "KEY": "k", "x-api-key": "x", "apikey": "a", "term": "token", "postal_code": "28001"})` → los 5 primeros como `"***"` y `term` y `postal_code` intactos;
  - `tests/integration/test_auth.py`: `GET /api/v1/products?…&api_key=secret-in-url` → `secret-in-url` no aparece en `caplog.text`, y la línea de inicio contiene `'api_key': '***'`.
- **GREEN:** `SECRET_PARAM_NAMES` y `redact_params()` en `app/middleware/request_context.py`, usados en la línea de inicio (plan-D7).
- **Depende:** —
- **RF:** RF-14
- **Hecho cuando:** todos los casos pasan y los tests de logging de la 003 siguen en verde.

### [x] T6 — Integración transversal
> **Verificación por mutación (2026-09-28):** sin `dependencies=[Security(require_api_key)]` en el router fallan 10 tests de `test_auth.py`, incluidos los 4 nuevos (antes de cache/enfriamiento/Alcampo, precedencia sobre `422`, trazabilidad y OpenAPI); restaurado, 17 pasan. Los casos "cache presente" y "enfriamiento activo" se cubren en un mismo test: con los dos sembrados en Redis, la respuesta es `401` y no el `200` cacheado ni el `502`.
- **RED:** en `tests/integration/test_auth.py`:
  - **nada antes de la auth:** petición sin token, con la ruta de Alcampo mockeada, una entrada en cache para esa búsqueda y la marca de enfriamiento activa → `401`, la ruta de respx con **0** llamadas, y la respuesta no es la cacheada;
  - **precedencia:** token inválido + `term="   "` → `401`, no `422`; token inválido con el enfriamiento activo → `401`, no `502`;
  - **trazabilidad:** el `401` lleva `X-Request-ID` y deja inicio y fin (`status=401`) con ese mismo id;
  - **OpenAPI:** `/openapi.json` declara un esquema `apiKey` en cabecera con nombre `X-API-Key`, y `/api/v1/products` lo exige en `security`.
- **GREEN:** no debería hacer falta código nuevo. **Verificación por mutación:** quitar temporalmente `dependencies=[…]` del router y comprobar que los tests de "nada antes de la auth" y de precedencia fallan; restaurar y anotar el resultado aquí.
- **Depende:** T3, T4
- **RF:** RF-1, RF-11, RF-13
- **Hecho cuando:** todos los casos pasan y la mutación queda anotada.

### [ ] T7 — Docs y verificación manual
- **RED:** ninguno propio (el test de `.env.example` ya cubre `API_KEYS` desde T1).
- **GREEN:** `.env.example` con un comentario sobre `API_KEYS` (por Bash). README: sección **Autenticación** con cómo enviar la cabecera (`curl -H "X-API-Key: …"`), `API_KEYS` (varios tokens, rotación sin cortes, qué pasa si está vacía), cómo generar un token fuerte (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), qué endpoints son públicos, y que el `401` es igual para cabecera ausente o inválida. Actualizar los ejemplos de `curl` existentes, el "Estado" y las limitaciones.
- **Verificación manual:** `uvicorn` con `API_KEYS` sintético. Si el daemon de Docker está arrancado, con Redis en contenedor; si no, con el lanzador con fakeredis del scratchpad (como en la 003) y anotándolo. Tres peticiones: sin cabecera (`401`), con cabecera inválida (`401`) y con cabecera válida (`200`, 1 búsqueda real a Alcampo, ≥10 min desde la última). Comprobar `WWW-Authenticate` y `X-Request-ID` con `curl -i`, y que la consola no muestra ningún token. Anotar el resultado aquí y parar el servidor.
- **Depende:** T6
- **RF:** RNF-3
- **Hecho cuando:** el README está revisado, la verificación manual queda anotada y el servidor está parado.

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T3, T6 |
| RF-2 | T3 |
| RF-3 | T3 |
| RF-4 | T2 |
| RF-5 | T2 |
| RF-6 | T3 |
| RF-7 | T1 |
| RF-8 | T3 |
| RF-9 | T4 |
| RF-10 | T4 |
| RF-11 | T6 |
| RF-12 | T1, T4 |
| RF-13 | T6 |
| RF-14 | T5 |
| RF-15 | T3 |

Ningún RF queda huérfano. RNF: RNF-1 (sin dependencias nuevas, en todas), RNF-2 (ruff `ANN`), RNF-3 (T1 y T7), RNF-4 (tokens sintéticos en todos los tests: `test-key`, `wrong-secret-value`, `secret-in-url`).
