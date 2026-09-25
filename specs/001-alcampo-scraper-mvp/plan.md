# Plan 001 — MVP de búsqueda de productos

- **Estado:** aprobado (2026-09-25). Entrega: 3 PRs encadenados (§8)
- **Fecha:** 2026-09-25
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D9** para no confundirlas con las decisiones de diseño de este plan (**D1…**).

## 1. Visión general

Flujo de una petición:

```
GET /api/v1/products?postal_code&term
  │  (validación: ProductQuery en la firma → 422)
  ▼
api/v1/products.py ──► services/product_service.py
                         │ 1. cache.get(warehouse, term) ──► hit: reescribe postal_code/term y devuelve
                         │ 2. scraper.search(term)       ──► AlcampoSearchResponse (crudo validado)
                         │ 3. mapper.map_search(raw)     ──► list[Product] (descarta rotos, deduplica)
                         │ 4. cache.set(...)
                         ▼
                       ProductSearchResponse
Errores: UpstreamUnavailableError ──► exception handler ──► 502
```

Toda la E/S es async. Un único `httpx.AsyncClient` y un único cliente Redis por proceso, creados y cerrados en el `lifespan` (RNF-1).

## 2. Módulos

Todos son nuevos: el repositorio solo tiene el esqueleto.

| Fichero | Responsabilidad | RF |
|---|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings) + `get_settings()` | RF-21 |
| `app/core/dependencies.py` | Providers FastAPI: `get_product_service` a partir de `app.state` | RF-1 |
| `app/exceptions.py` | `AlcampoScraperError` (base) y `UpstreamUnavailableError` | RF-17, RF-20 |
| `app/models/product.py` | Schemas de la API: `ProductQuery`, `Product`, `SearchMetadata`, `ProductSearchResponse` | RF-1, RF-2, RF-10 |
| `app/models/alcampo.py` | Schemas crudos de Alcampo (sobre de la respuesta + producto) | RF-4, RF-19 |
| `app/mappers/product_mapper.py` | Crudo → `Product`; tabla de unidades; descarte y deduplicación | RF-4, RF-6…RF-9 |
| `app/scrapers/http_client.py` | Factoría `create_http_client(settings) -> httpx.AsyncClient` | RNF-1, RNF-3 |
| `app/scrapers/retry.py` | `send_with_retry(...)`: clasifica respuestas, backoff, WAF | RF-15…RF-18 |
| `app/scrapers/alcampo_search.py` | `AlcampoSearchScraper.search(term) -> AlcampoSearchResponse` | RF-3, RF-19, RF-20 |
| `app/services/search_cache.py` | `SearchCacheRepository`: get/set en Redis con TTL | RF-11…RF-14 |
| `app/services/product_service.py` | Orquesta cache → scraper → mapper → cache | RF-1, RF-5, RF-10, RF-11 |
| `app/api/v1/products.py` | Ruta `GET /api/v1/products` | RF-1, RF-2 |
| `app/main.py` | `create_app()`, `lifespan`, handler `UpstreamUnavailableError → 502` | RF-17, RNF-1 |
| `README.md`, `.env.example` | Docs vivas | RNF-6 |

Tests en espejo (`tests/core/`, `tests/models/`, `tests/mappers/`, `tests/scrapers/`, `tests/services/`, `tests/api/`) más `tests/integration/`.

## 3. Modelo de datos

### API (`app/models/product.py`)

```python
SearchTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]

class ProductQuery(BaseModel):
    postal_code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    term: SearchTerm

class Product(BaseModel):
    id: str
    name: str
    price: float
    price_format: str | None
    image_url: str | None
    category: str | None

class SearchMetadata(BaseModel):
    postal_code: str
    term: str
    warehouse: str
    strategy_used: str
    scraped_at: datetime          # UTC, serializado con "Z"
    total_results: int

class ProductSearchResponse(BaseModel):
    search: SearchMetadata
    products: list[Product]
```

`postal_code` solo exige que no esté vacío. El patrón de 5 dígitos llega en la 007, tal como marca la hoja de ruta.

### Crudo de Alcampo (`app/models/alcampo.py`)

Todos con `model_config = ConfigDict(extra="ignore")`: la respuesta real tiene más de 30 campos por producto.

```python
DecimalString = Annotated[str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+)?$")]

class AlcampoMoney(BaseModel):
    amount: DecimalString

class AlcampoUnitPrice(BaseModel):
    price: AlcampoMoney | None = None
    unit_name: str | None = Field(default=None, alias="unitName")

class AlcampoImage(BaseModel):
    src: str | None = None

class AlcampoProduct(BaseModel):
    retailer_product_id: str = Field(alias="retailerProductId", min_length=1)
    name: str = Field(min_length=1)
    price: AlcampoMoney
    unit_price: AlcampoUnitPrice | None = Field(default=None, alias="unitPrice")
    image: AlcampoImage | None = None
    category_path: list[str] = Field(default_factory=list, alias="categoryPath")

class AlcampoProductGroup(BaseModel):
    decorated_products: list[JsonValue] = Field(default_factory=list, alias="decoratedProducts")

class AlcampoSearchResponse(BaseModel):
    product_groups: list[AlcampoProductGroup] = Field(alias="productGroups")   # obligatorio → RF-19
```

### Redis

| Clave | Valor | TTL |
|---|---|---|
| `search:{warehouse}:{term}` (p. ej. `search:5:leche`) | `ProductSearchResponse` en JSON | `CACHE_TTL_SECONDS` |

## 4. Decisiones de diseño

**D1 — Validar los productos uno a uno, no en bloque.** El sobre (`AlcampoSearchResponse`) guarda los productos como `JsonValue`, y el mapper valida cada uno contra `AlcampoProduct` dentro de un `try/except ValidationError`.
- *Descartada:* tipar `decoratedProducts: list[AlcampoProduct]` en el sobre. Un solo producto roto haría fallar toda la validación y la búsqueda acabaría en `502`, en contra de RF-9 y spec-D7.

**D2 — Importe como `str` validado por patrón en el modelo crudo.** `amount` se guarda como string (`"0.88"`) validado con `^[0-9]+(\.[0-9]+)?$`. Se convierte a `float` solo para `Product.price`, y se copia literal en `price_format` (spec-D9).
- *Descartada:* `amount: float` en el modelo crudo. Perdería el formato original (`"0.80"` → `0.8`), y `price_format` ya no llevaría el importe tal como llega.

**D3 — Reintentos en una función pura con `sleep` inyectable.** `send_with_retry(send, *, max_attempts, base_delay, sleep=asyncio.sleep)` recibe un callable que hace la petición y clasifica el resultado:

| Resultado | Acción |
|---|---|
| Cabecera `x-amzn-waf-action` presente (cualquier código) | `UpstreamUnavailableError` inmediato, **sin reintento** (RF-18, spec-D5). Se evalúa **antes** que el código: el challenge llega como `202` |
| `2xx` | devolver la respuesta |
| `5xx` o `429` | reintentar |
| Otro `4xx` | `UpstreamUnavailableError` inmediato (RF-16) |
| `httpx.TransportError` (incluye timeouts) | reintentar |
| Intentos agotados | `UpstreamUnavailableError` (RF-17) |

Espera entre el intento *n* y el *n+1*: `base_delay × 2^(n-1)`. Los tests inyectan un `sleep` falso que registra las esperas; ninguno espera de verdad.
- *Descartada:* una librería de reintentos (`tenacity`). Está fuera del stack fijo de la constitución, y la 002 necesitará `Retry-After` y jitter, que son más fáciles de añadir a una función propia.
- *Descartada:* el `transport=httpx.AsyncHTTPTransport(retries=N)` de httpx. Solo reintenta errores de conexión, no `5xx`/`429`.

**D4 — Clave de cache por `warehouse`, no por `postal_code`.** Se usa `search:{warehouse}:{term}` desde ya. En un hit, el servicio reescribe `search.postal_code` y `search.term` con los de la petición actual.
- *Descartada:* `search:{postal_code}:{term}`. En la 001 todas las búsquedas van a la misma región, así que una clave por CP multiplicaría las peticiones a Alcampo (y el riesgo de WAF) por el número de CPs distintos sin aportar nada. Además, la 007 exige clave por zona, así que se evita migrarla después.

**D5 — `term` en la clave: recortado, sin pasar a minúsculas.** La clave usa el término tal como queda tras `strip()`.
- *Descartada:* normalizar a minúsculas (`casefold`). Ahorraría peticiones, pero **no está verificado** que Alcampo ignore mayúsculas. Si no las ignora, serviríamos resultados de otro término. Se puede revisar con evidencia en la 002.

**D6 — `warehouse` como constante de módulo.** `DEFAULT_WAREHOUSE = "5"` en `app/scrapers/alcampo_search.py`, con un comentario que enlaza a la Fase 0 (spec-D1).
- *Descartada:* una variable de entorno `ALCAMPO_DEFAULT_WAREHOUSE`. La tabla de variables de entorno del proyecto es cerrada, y el valor no es configuración sino un hecho del upstream que la 007 sustituirá por resolución real.

**D7 — Una sola excepción de dominio para todos los fallos de upstream.** `UpstreamUnavailableError(reason: str)` cubre reintentos agotados, `4xx`, WAF y cuerpo inválido. El `reason` es interno (para logs en la 003) y nunca sale en la respuesta, cuyo `detail` es fijo (RF-17).
- *Descartada:* una jerarquía (`UpstreamWafError`, `UpstreamInvalidResponseError`…). Hoy todas acaban en el mismo `502`. Se subdividirá cuando algún comportamiento dependa del tipo, como el `404` de CP sin servicio en la 007.

**D8 — Cache corrupta = miss.** Si el JSON guardado no valida contra `ProductSearchResponse` (por ejemplo, tras un cambio de schema), se trata como miss y se sobrescribe.
- *Descartada:* propagar el error. Un despliegue con cambio de schema devolvería `500` durante una hora.

**D9 — Reloj inyectable en el servicio.** `ProductService(..., clock: Callable[[], datetime])`, con `datetime.now(UTC)` por defecto, para comprobar `scraped_at` en los tests (RF-10, RF-14).
- *Descartada:* `freezegun` u otra librería. Está fuera del stack fijo y no hace falta.

**D10 — Providers en `app/core/dependencies.py` desde el principio.** `get_product_service(request)` construye el servicio a partir de `request.app.state.http_client` y `request.app.state.redis`.
- *Descartada:* definir los providers dentro de la ruta. La estructura de la constitución ya asigna `core/` a las dependencias. La 006 se limitará a añadir `AppState` tipado.

**D11 — Tests de integración con `fastapi.testclient.TestClient` como context manager**, que ejecuta el `lifespan` real. Redis se sustituye parcheando la factoría `create_redis(settings)` para que devuelva un `FakeAsyncRedis()` nuevo por test. Alcampo se sustituye con `respx`.
- *Descartada:* `httpx.AsyncClient(transport=ASGITransport(app))`. No ejecuta el `lifespan`, y el objetivo de estos tests es precisamente probar la app real.
- *Riesgo asociado:* ver R3.

**D12 — Cabeceras fijas del cliente HTTP** en la factoría: un User-Agent de Chrome real (el mismo que se usó en la Fase 0 sin incidencias), `Accept: application/json` y `Accept-Language: es-ES,es;q=0.9`. `timeout=10s` y `base_url=ALCAMPO_BASE_URL`.
- *Descartada:* el User-Agent por defecto de httpx (`python-httpx/…`). El bloqueo por User-Agent no está verificado (Fase 0 §5), y no merece la pena descubrirlo en producción. La rotación llega en la 002.

## 5. Estrategia de test por RF

Tipos: **U** = unitario, **I** = integración (app real + lifespan + fakeredis + respx).

| RF | Test | Tipo | Fichero |
|---|---|---|---|
| RF-1 | `GET` válido → `200` y body valida contra `ProductSearchResponse` | I | `tests/integration/test_products_endpoint.py` |
| RF-2 | falta `term` / `postal_code`, `term="   "`, `term` de 51 caracteres → `422` **y** ninguna ruta respx llamada **y** Redis vacío | I | idem |
| RF-3 | la ruta respx recibe `q`, `tag=web`, `maxPageSize=50`, `maxProductsToDecorate=50`; exactamente 1 llamada | U | `tests/scrapers/test_alcampo_search.py` |
| RF-4 | 2 grupos con un id repetido → concatenados, sin duplicado, orden preservado | U | `tests/mappers/test_product_mapper.py` |
| RF-5 | fixture real `alcampo_search_no_results.json` → `products: []`, `total_results: 0` | U + I | `tests/services/…`, integración |
| RF-6 | fixture real `alcampo_search_leche.json` → campos esperados (`"54180"`, `5.28`, `"Leche semidesnatada"`, `image.src`) | U | mapper |
| RF-6 | `categoryPath` vacío o ausente → `category: None` | U | mapper |
| RF-7 | `PER_LITRE` + `"0.88"` → `"0.88 €/L"`; `"0.80"` se conserva (no `"0.8"`) | U | mapper |
| RF-8 | sin `unitPrice`, `unitPrice` sin `price`, `unitName="PER_DOSE"` → `None` | U | mapper |
| RF-9 | producto sin `name` / sin `retailerProductId` / `amount="abc"` → descartado; el resto sí | U | mapper |
| RF-10 | `warehouse="5"`, `strategy_used="api"`, `scraped_at` = reloj inyectado, `total_results` = len | U | `tests/services/test_product_service.py` |
| RF-11 | con entrada en Redis → respuesta de cache y **0 llamadas** a Alcampo | U + I | servicio, integración |
| RF-11 | hit con otro `postal_code` → la respuesta lleva el `postal_code` de la petición | U | servicio |
| RF-12 | miss → `set` con TTL = `CACHE_TTL_SECONDS` (se comprueba el `ttl` de la clave en fakeredis) | U | `tests/services/test_search_cache.py` |
| RF-12 | búsqueda sin resultados también se cachea | U | servicio |
| RF-13 | fallo del scraper → Redis sigue vacío | U | servicio |
| RF-14 | hit → `scraped_at` original, no el del reloj actual | U | servicio |
| RF-15 | `503, 503, 200` → éxito en el 3.er intento; esperas registradas `[0.5, 1.0]`; `429` y `ConnectTimeout` también se reintentan | U | `tests/scrapers/test_retry.py` |
| RF-16 | `404` → 1 sola llamada y excepción | U | retry |
| RF-17 | `503 × RETRY_MAX_ATTEMPTS` → `UpstreamUnavailableError`; en la API → `502 {"detail": "Upstream service unavailable"}` sin el cuerpo de Alcampo | U + I | retry, integración |
| RF-18 | `202` + `x-amzn-waf-action: challenge` → excepción en el 1.er intento, 0 esperas | U | retry |
| RF-19 | `200` con HTML, `200` con JSON sin `productGroups` → `UpstreamUnavailableError` | U | scraper |
| RF-20 | la excepción que sale del scraper nunca es de `httpx` (transporte agotado → `UpstreamUnavailableError`) | U | scraper |
| RF-21 | sin `ALCAMPO_BASE_URL` o sin `REDIS_URL` → `ValidationError`; defaults correctos | U | `tests/core/test_config.py` |
| D8 | valor corrupto en la clave → miss y sobrescritura | U | search_cache |

Fixtures: se reutilizan las reales de `tests/fixtures/`. Los casos que no se han observado en vivo (sin `unitPrice`, 2 grupos, id duplicado, producto roto) se construyen en el test **a partir** de la fixture real, modificando solo el campo bajo prueba, para no inventar formas.

## 6. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | Alcampo cambia su región por defecto y `warehouse="5"` deja de ser verdad | Dato incorrecto (no hay fallo) | Aceptado en spec-D1. Documentado en el README. La 007 lo sustituye |
| R2 | El WAF bloquea la IP del servidor en producción | `502` durante 2–4 min | Cache de 1 h, 1 petición por búsqueda, sin reintento ante challenge. La 002 añade más defensas |
| R3 | `respx` intercepta también las peticiones del `TestClient` | Tests de integración rotos | `TestClient` usa su propio transporte, no `HTTPTransport`, así que en principio respx no lo toca. **Se verifica en la primera tarea de integración**; si falla, se para y se avisa (regla del proceso) |
| R4 | La versión local es Python 3.14 y el objetivo es 3.11+; alguna dependencia (`fakeredis`, `pydantic-core`) podría no tener wheel para 3.14 | Instalación fallida | La tarea T1 instala y ejecuta `pytest` vacío. Si falla, se crea un venv 3.11/3.12 |
| R5 | Unidades `PER_KG`, `PER_EACH` y `PER_METER` sin verificar (spec-D8) | `price_format: null` si el nombre real es otro | Degradación aceptable por RF-8. Se revisará con evidencia |
| R6 | `.env.example` está bloqueado por los permisos del agente | RNF-6 incumplido | La tarea correspondiente pedirá al usuario que lo cree o que conceda permiso |

## 7. Secuencia de implementación

Orden por dependencias, de dentro hacia fuera:

1. **Base:** instalación, `config`, `exceptions`.
2. **Modelos:** API y crudo.
3. **Mapper:** unidades, producto, búsqueda (descarte y deduplicación).
4. **HTTP:** factoría del cliente, `retry`, scraper.
5. **Cache:** repositorio.
6. **Servicio:** orquestación.
7. **API:** providers, ruta, `main` + `lifespan` + handler `502`.
8. **Integración:** `tests/integration/conftest.py` (helper respx de búsqueda + fakeredis) y escenarios end-to-end.
9. **Docs:** README, `.env.example` y comprobación de `/docs`.

## 8. Estimación y entrega

| Bloque | Código `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| 1–3 Base, modelos, mapper | ~150 | ~200 | — | ~350 |
| 4 HTTP, retry, scraper | ~110 | ~180 | — | ~290 |
| 5–9 Cache, servicio, API, integración, docs | ~140 | ~230 | ~50 | ~420 |
| **Total** | **~400** | **~610** | **~50** | **~1060** |

**Supera las 400 líneas**, así que propongo **3 PRs encadenados** (stacked, cada uno contra el anterior), uno por fila de la tabla:

```
main ◄── PR1 base+modelos+mapper ◄── PR2 http+retry+scraper ◄── PR3 cache+servicio+api+docs
```

- Cada PR deja **toda la suite en verde**: ningún PR intermedio deja tests en rojo, porque cada bloque solo depende de los anteriores.
- PR1 y PR2 no exponen ningún endpoint; la funcionalidad visible llega con PR3.
- PR3 sale en torno a 420 líneas, ligeramente por encima del límite, por los tests de integración. Si prefieres respetarlo estrictamente, divido PR3 en 3a (cache + servicio) y 3b (API + integración + docs).

## 9. Qué no cambia

Constitución, `pyproject.toml` (las dependencias ya están declaradas) y fixtures de la Fase 0.
