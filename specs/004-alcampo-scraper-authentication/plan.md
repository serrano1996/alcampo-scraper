# Plan 004 — Autenticación

- **Estado:** aprobado (2026-09-28). Entrega: un solo PR (§10)
- **Fecha:** 2026-09-28
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D4**; las de specs anteriores como **003-plan-Dn**, etc. Las decisiones de diseño de este plan son **D1…**

## 1. Visión general

```
petición HTTP
  └─ RequestContextMiddleware (003)                  request id, inicio (params redactados, RF-14), fin
       └─ ExceptionMiddleware
            └─ router /api/v1  dependencies=[Security(require_api_key)]
                 ├─ sin cabecera / inválida → WARNING + 401 + WWW-Authenticate   RF-1…RF-3, RF-10, RF-15
                 └─ válida → validación de parámetros (422) → servicio (cache, enfriamiento, Alcampo)

/health, /docs, /openapi.json, /redoc  → fuera del router: públicos           RF-6

lifespan: si API_KEYS vacío → WARNING "all /api/v1 requests will be rejected"  RF-8, RF-9
```

## 2. Verificaciones previas (hechas el 2026-09-28)

| Supuesto | Experimento | Resultado | Consecuencia |
|---|---|---|---|
| Con la dependencia en el router, la auth va antes que la validación | Router con `dependencies=[Depends(require_key)]` y `ProductQuery` en la firma | Token malo + `term` inválido → **`401`**; token bueno + `term` inválido → `422` | El orden de la spec (§6) se cumple sin hacer nada especial |
| OpenAPI refleja el requisito | Mismo experimento | `components.securitySchemes` contiene `APIKeyHeader` | RF-13 sale solo con `Security(APIKeyHeader)` |
| `API_KEYS=a,b` se lee como conjunto | `frozenset[str]` en `BaseSettings` | **`SettingsError`**: pydantic-settings intenta leer los tipos complejos como JSON | Hace falta `NoDecode` + un validador (D1) |
| Ocultar los tokens en el `repr` de `Settings` | `Field(repr=False)` | `repr(settings)` no los muestra | D1 |
| `compare_digest` con cualquier cabecera | `compare_digest("clé", "abc")` | **`TypeError`** con texto no ASCII | Sin cuidado, un cliente provocaría un `500`. Se compara en bytes (D3) |

## 3. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `api_keys: Annotated[frozenset[str], NoDecode]`, `repr=False`, validador que separa por comas y limpia | RF-7, RF-12 |
| `app/core/security.py` | **nuevo**: `api_key_header`, `is_valid_api_key()`, dependencia `require_api_key` | RF-1…RF-5, RF-10, RF-15 |
| `app/api/v1/products.py` | `dependencies=[Security(require_api_key)]` en el router | RF-1, RF-6, RF-13 |
| `app/main.py` | `WARNING` en el `lifespan` si no hay tokens | RF-8, RF-9 |
| `app/middleware/request_context.py` | redacción de parámetros con nombre de secreto | RF-14 |
| `.env.example`, `README.md` | `API_KEYS`, cómo generar un token, cómo llamar a la API | RNF-3 |
| `tests/integration/conftest.py`, `tests/api/test_products_route.py` | adaptación a la auth (§6) | — |

## 4. Modelo de datos

```python
api_keys: Annotated[frozenset[str], NoDecode] = Field(default=frozenset(), repr=False)
```

`frozenset` porque el orden y los duplicados no importan y es inmutable. No se persiste nada; sin cambios en Redis ni en los modelos de la API.

Respuesta `401` (idéntica para cabecera ausente e inválida):

```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: ApiKey
X-Request-ID: <32 hex>

{"detail": "Invalid or missing API key"}
```

## 5. Decisiones de diseño

**D1 — `API_KEYS` como `frozenset[str]` con `NoDecode`, validador y `repr=False`.** El validador (`mode="before"`) separa por comas, recorta los espacios y descarta las entradas vacías (RF-7). `repr=False` evita que los tokens aparezcan si alguien registra o imprime `Settings` (RF-12).
- *Descartada:* `api_keys: str` más una propiedad que la trocee. Funciona, pero deja el valor en bruto en el `repr` y obliga a trocear en cada petición.
- *Descartada:* `SecretStr` por token. Oculta más, pero complica la comparación y los tests sin aportar nada, porque `repr=False` ya cubre el riesgo real.

**D2 — Dependencia a nivel de router, no middleware.** `APIRouter(prefix="/api/v1", dependencies=[Security(require_api_key)])`. Todo lo que cuelgue del router queda protegido, y todo lo demás (`/health`, `/docs`, `/openapi.json`) queda público sin listas de exclusión (RF-6). Se ejecuta antes de validar los parámetros, así que no toca la cache ni Alcampo (verificado en §2). Y FastAPI lo documenta en OpenAPI (RF-13).
- *Descartada:* un middleware de auth. Exigiría una lista de rutas públicas mantenida a mano y no aparecería en OpenAPI.

**D3 — Comparación en bytes, sin cortocircuito.** `is_valid_api_key(candidate, valid_keys)` codifica ambos lados en UTF-8 y compara con `secrets.compare_digest` **contra todos** los tokens, acumulando el resultado con `|=`, sin salir en el primer acierto.
- *Descartada:* comparar `str` directamente. Comprobado que lanza `TypeError` con texto no ASCII, lo que daría un `500` (§2).
- *Descartada:* `any(compare_digest(...) for ...)`. Sale en el primer acierto y revela, por temporización, la posición del token válido en la lista. El coste de recorrerlos todos es despreciable (hay pocos).

**D4 — `auto_error=False` en `APIKeyHeader` y un `401` propio.** Con `auto_error=True`, FastAPI decide el código y el cuerpo por nosotros. Con `False` controlamos el cuerpo idéntico para "ausente" e "inválida" (RF-3), la cabecera `WWW-Authenticate` (RF-15) y el log con el motivo (RF-10). Una cabecera vacía llega como `""` o `None`; ambos casos se tratan como ausente (RF-2).

**D5 — La dependencia lee los tokens de `request.app.state.settings`**, que el `lifespan` ya guarda desde la 001. Es la misma fuente que usan el resto de dependencias.
- *Descartada:* `Depends(get_settings)`. Lee el entorno por su cuenta y se salta la configuración que el `lifespan` fijó, justo el problema de cache que vimos en la 003.

**D6 — Aviso de arranque en el `lifespan`, después de `configure_logging`**, para que salga con nuestro formato (RF-9). No hace falta request id: fuera de una petición vale `-`.

**D7 — Redacción de parámetros en el middleware por nombre.** Una constante `SECRET_PARAM_NAMES = {"api_key", "apikey", "x-api-key", "key", "token"}` y una función `redact_params(params) -> dict[str, str]` que sustituye por `"***"` los valores cuyo nombre, en minúsculas, esté en el conjunto (RF-14). Si un nombre se repite (`?token=a&token=b`), `dict(request.query_params)` ya se queda con uno; se redacta igual.
- *Descartada:* redactar por el valor (buscar los tokens configurados en la URL). Solo cubriría tokens **válidos** y obligaría al middleware a conocer los secretos.

**D8 — Nivel y contenido del log de rechazo.** `WARNING` con `reason=missing|invalid` y la ruta (con `%r`). Nunca el valor recibido ni ninguno de sus caracteres (ni prefijos ni hash): RF-10 y RF-12.

## 6. Regresiones previstas (y en qué tarea se corrigen)

Aprendido de la 002: cada regresión se corrige **en la tarea que la provoca**.

| Tests afectados | Por qué | Corrección | Tarea |
|---|---|---|---|
| `tests/core/test_env_example.py` | `Settings` tiene un campo nuevo que `.env.example` no declara | añadir `API_KEYS=` a `.env.example` | la de `Settings` |
| Toda la integración que llama a `/api/v1` (specs 001–003) | sin cabecera → `401` | `integration_env` fija `API_KEYS=test-key` y el fixture `client` envía `X-API-Key: test-key` por defecto | la que protege el router |
| `tests/api/test_products_route.py` | igual; esos tests no ejecutan el `lifespan`, así que no hay `app.state.settings` | `make_client` sustituye `require_api_key` por una función vacía (`dependency_overrides`): esos tests prueban la ruta, no la auth | la que protege el router |
| `tests/integration/test_logging_integration.py` | usa el fixture `client` | cubierto por el cambio del fixture | la que protege el router |

Los tests de OpenAPI (`/openapi.json` público) y el de `/boom` (ruta fuera del router) no se ven afectados.

## 7. Estrategia de test por RF

**U** = unitario, **I** = integración (app real + `lifespan` + fakeredis + respx).

| RF | Test | Tipo |
|---|---|---|
| RF-7 | `" a , ,b "` → `{"a", "b"}`; sin variable → vacío; `repr(settings)` no contiene los tokens | U |
| RF-4 | `is_valid_api_key` acepta cualquiera de varios tokens; rechaza uno distinto; con texto no ASCII devuelve `False` sin excepción | U |
| RF-5 | `" test-key"`, `"TEST-KEY"` → inválidos | U |
| RF-1, RF-2 | sin cabecera o con cabecera vacía → `401`, **0** llamadas a Alcampo y **0** lecturas de Redis (Redis vacío y ruta respx sin llamar) | I |
| RF-3 | token inválido → `401` con el **mismo** body que sin cabecera | I |
| RF-1 | token inválido + `term` inválido → `401`, no `422` | I |
| RF-1 | token inválido con enfriamiento activo → `401`, no `502` | I |
| RF-6 | `/health`, `/docs`, `/openapi.json` sin cabecera → `200` | I |
| RF-8 | `API_KEYS` vacío → todo `/api/v1` da `401`, incluso con cabecera | I |
| RF-9 | `API_KEYS` vacío → `WARNING` al arrancar; con tokens → sin ese `WARNING` | I |
| RF-10, RF-12 | rechazo → `WARNING` con `reason=missing` o `reason=invalid` y la ruta; ni el token recibido ni los configurados aparecen en `caplog.text` | I |
| RF-11 | el `401` lleva `X-Request-ID` y sus líneas de inicio y fin (`status=401`) | I |
| RF-13 | `/openapi.json` declara un esquema `apiKey` en cabecera `X-API-Key` y `/api/v1/products` lo exige | U |
| RF-14 | `redact_params({"api_key": "s", "Token": "t", "term": "token"})` → `{"api_key": "***", "Token": "***", "term": "token"}`; en integración, `?api_key=secret-in-url` no aparece en `caplog.text` | U + I |
| RF-15 | el `401` lleva `WWW-Authenticate: ApiKey` | I |

## 8. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | Desplegar sin `API_KEYS` | Servicio inútil (todo `401`) | `WARNING` al arrancar (RF-9) y documentación |
| R2 | Un token se filtra | Acceso de terceros | Rotación por variable de entorno sin cortes (varios tokens a la vez) |
| R3 | Redacción por nombre incompleta (`?secret=…`) | Un token en la URL podría acabar en logs | Aceptado: el caso cubierto es el error típico; la cabecera es el único canal válido y está documentado |
| R4 | Tests que olviden la cabecera | Falsos `401` | El fixture `client` la envía por defecto; los tests de auth crean sus peticiones sin ella de forma explícita |

## 9. Secuencia de implementación

1. **`Settings`:** `API_KEYS` con `NoDecode`, validador y `repr=False` (+ `.env.example`).
2. **`security.py`:** `is_valid_api_key` (bytes, sin cortocircuito).
3. **Dependencia** `require_api_key` + router protegido + adaptación de los tests existentes.
4. **Logs:** rechazo (`WARNING`, sin token) y aviso de arranque sin tokens.
5. **Middleware:** redacción de parámetros con nombre de secreto.
6. **Integración transversal:** ni cache ni Alcampo antes de la auth, precedencia sobre `422` y `502`, `X-Request-ID`, OpenAPI, verificación por mutación.
7. **Docs** y verificación manual con `uvicorn`.

## 10. Estimación y entrega

| Bloque | `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| Todo | ~80 | ~210 | ~35 | **~325** |

**Por debajo de las 400 líneas: un solo PR.** A diferencia de las specs 002 y 003, no hace falta encadenar.

## 11. Qué no cambia

Contrato de las respuestas `200`, `422` y `502`, lógica de búsqueda, reintentos, enfriamiento y cache. Lo único nuevo para un cliente autorizado es que tiene que enviar `X-API-Key`.
