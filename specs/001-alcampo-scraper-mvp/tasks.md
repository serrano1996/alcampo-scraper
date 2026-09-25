# Tasks 001 — MVP de búsqueda de productos

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 3 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado** (no por un error de import ajeno).
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(001-alcampo-scraper-mvp): <descripción en inglés> (Tn)`.

---

## PR1 — Base, modelos y mapper

### [x] T1 — Entorno y paquete `app`
> Hecho el 2026-09-25. Instalación limpia en Python 3.14.3: el riesgo R4 queda descartado. Ruff 0.16 también formatea los bloques de código Python dentro de los `.md`, así que `plan.md` se reformateó (solo espacios) para que `ruff format --check .` pase.

- **RED:** `tests/test_package.py` importa `app`, `app.api.v1`, `app.core`, `app.models`, `app.mappers`, `app.scrapers` y `app.services` → `ModuleNotFoundError`.
- **GREEN:** crear los `__init__.py` vacíos. Crear el venv e instalar con `pip install -e ".[dev]"`.
- **Depende:** —
- **RF:** — (infraestructura; plan R4)
- **Hecho cuando:** la instalación termina sin errores en el Python local, `pytest -q` pasa 1 test y `ruff check .` sale limpio. Si alguna dependencia falla en Python 3.14, **parar y avisar** (R4).

### [x] T2 — `Settings`
- **RED:** `tests/core/test_config.py`:
  - sin `ALCAMPO_BASE_URL` → `ValidationError`;
  - sin `REDIS_URL` → `ValidationError`;
  - con ambas → defaults `CACHE_TTL_SECONDS=3600`, `RETRY_MAX_ATTEMPTS=3`, `RETRY_BASE_DELAY=0.5`, `LOG_LEVEL="INFO"`;
  - las variables de entorno sobrescriben los defaults.
  - Todo con `monkeypatch` y `_env_file=None`, para no leer un `.env` local.
- **GREEN:** `app/core/config.py` con `Settings(BaseSettings)` y `get_settings()` con `lru_cache`.
- **Depende:** T1
- **RF:** RF-21
- **Hecho cuando:** los 4 casos pasan.

### [x] T3 — Excepciones de dominio
- **RED:** `tests/test_exceptions.py`: `UpstreamUnavailableError("x")` es subclase de `AlcampoScraperError`, expone `.reason == "x"` y su módulo no importa `httpx` (se comprueba que `"httpx"` no está en `app.exceptions.__dict__`).
- **GREEN:** `app/exceptions.py`.
- **Depende:** T1
- **RF:** RF-17, RF-20 (plan-D7)
- **Hecho cuando:** el test pasa.

### [x] T4 — Schemas de la API
- **RED:** `tests/models/test_product.py`:
  - `ProductQuery(term="  leche ")` → `term == "leche"`;
  - `term="   "`, `term` de 51 caracteres, `postal_code=""` → `ValidationError`;
  - `term` de exactamente 50 caracteres → válido;
  - `Product(price_format=None, category=None, image_url=None)` → válido;
  - `ProductSearchResponse` serializa `scraped_at` con sufijo `Z`.
- **GREEN:** `app/models/product.py` (plan §3).
- **Depende:** T1
- **RF:** RF-2, RF-8, RF-10
- **Hecho cuando:** todos los casos pasan.

### [x] T5 — Schemas crudos de Alcampo
- **RED:** `tests/models/test_alcampo.py`:
  - la fixture real `alcampo_search_leche.json` valida contra `AlcampoSearchResponse` con 1 grupo y 3 productos crudos;
  - un JSON sin `productGroups` → `ValidationError`;
  - el primer producto de la fixture valida contra `AlcampoProduct` (`retailer_product_id == "54180"`, `price.amount == "5.28"`);
  - `amount="abc"` → `ValidationError`;
  - sin `categoryPath` → `[]`;
  - sin `unitPrice` → `None`.
- **GREEN:** `app/models/alcampo.py` (plan-D1, plan-D2).
- **Depende:** T1
- **RF:** RF-9, RF-19
- **Hecho cuando:** todos los casos pasan usando la fixture real, sin copiarla a mano.

### [x] T6 — Formato del precio por unidad
- **RED:** `tests/mappers/test_product_mapper.py::test_format_unit_price_*`:
  - `PER_LITRE` + `"0.88"` → `"0.88 €/L"`;
  - `"0.80"` → `"0.80 €/L"` (no `"0.8"`);
  - `PER_KG` → `kg`, `PER_EACH` → `ud`, `PER_METER` → `m`;
  - `PER_DOSE` → `None`;
  - `unit_price=None` → `None`;
  - `unit_price.price=None` → `None`.
- **GREEN:** `UNIT_SUFFIXES` y `format_unit_price()` en `app/mappers/product_mapper.py`.
- **Depende:** T5
- **RF:** RF-7, RF-8 (spec-D8, spec-D9)
- **Hecho cuando:** todos los casos pasan.

### [x] T7 — Mapeo de un producto
- **RED:** `test_map_product_*`:
  - el 1.er producto de la fixture real → `Product(id="54180", price=5.28, price_format="0.88 €/L", category="Leche semidesnatada", image_url=<image.src>)`;
  - `categoryPath=[]` → `category=None`;
  - sin `image` → `image_url=None`.
- **GREEN:** `map_product(raw: AlcampoProduct) -> Product`.
- **Depende:** T4, T6
- **RF:** RF-6
- **Hecho cuando:** todos los casos pasan.

### [x] T8 — Mapeo de la búsqueda (descarte y deduplicación)
- **RED:** `test_map_search_*`, todos construidos a partir de la fixture real:
  - fixture → 3 productos en orden;
  - 2 grupos con un `retailerProductId` repetido → sin duplicado, primera aparición conservada;
  - un producto sin `name`, otro sin `retailerProductId` y otro con `amount="abc"` → descartados, el resto se conserva;
  - fixture `alcampo_search_no_results.json` → `[]`.
- **GREEN:** `map_search(raw: AlcampoSearchResponse) -> list[Product]`, con validación por producto (plan-D1).
- **Depende:** T7
- **RF:** RF-4, RF-5, RF-9 (spec-D7)
- **Hecho cuando:** todos los casos pasan. **Fin de PR1.**

---

## PR2 — Cliente HTTP, reintentos y scraper

### [x] T9 — Factoría del cliente HTTP
- **RED:** `tests/scrapers/test_http_client.py`: `create_http_client(settings)` devuelve un `httpx.AsyncClient` con `base_url` igual a `ALCAMPO_BASE_URL`, `timeout` de 10 s y cabeceras `User-Agent` (contiene `Chrome/`), `Accept: application/json` y `Accept-Language` que empieza por `es-ES`. Se cierra con `aclose()`.
- **GREEN:** `app/scrapers/http_client.py` (plan-D12).
- **Depende:** T2
- **RF:** RNF-1, RNF-3
- **Hecho cuando:** el test pasa.

### [x] T10 — Reintentos ante fallos transitorios
- **RED:** `tests/scrapers/test_retry.py`, con un `send` falso que devuelve una secuencia de `httpx.Response` y un `sleep` falso que registra las esperas:
  - `200` → 1 llamada, 0 esperas;
  - `503, 503, 200` → 3 llamadas, esperas `[0.5, 1.0]`;
  - `429, 200` → reintenta;
  - `ConnectTimeout`, luego `200` → reintenta.
- **GREEN:** `send_with_retry()` en `app/scrapers/retry.py` (plan-D3), solo la rama de reintento.
- **Depende:** T3
- **RF:** RF-15
- **Hecho cuando:** todos los casos pasan sin esperas reales (la suite no tarda más por esto).

### [x] T11 — Fallos definitivos y challenge del WAF
- **RED:** en el mismo fichero:
  - `404` → 1 llamada y `UpstreamUnavailableError`;
  - `503 × 3` con `max_attempts=3` → `UpstreamUnavailableError`, 3 llamadas y 2 esperas;
  - `ConnectError × 3` → `UpstreamUnavailableError` (no `httpx.ConnectError`);
  - `202` + `x-amzn-waf-action: challenge` → excepción en la **1.ª** llamada, 0 esperas;
  - `200` + `x-amzn-waf-action` → también excepción (la cabecera manda sobre el código).
- **GREEN:** completar la clasificación de `send_with_retry()`.
- **Depende:** T10
- **RF:** RF-16, RF-17, RF-18, RF-20 (spec-D5)
- **Hecho cuando:** todos los casos pasan.

### [x] T12 — Scraper de búsqueda: petición y respuesta correctas
- **RED:** `tests/scrapers/test_alcampo_search.py`, con `respx` sobre `https://alcampo.test`:
  - `search("leche")` hace **exactamente 1** `GET` a `/api/webproductpagews/v6/product-pages/search` con `q=leche`, `tag=web`, `maxPageSize=50` y `maxProductsToDecorate=50`;
  - con la fixture real como respuesta, devuelve un `AlcampoSearchResponse` con 3 productos crudos;
  - `DEFAULT_WAREHOUSE == "5"`.
- **GREEN:** `AlcampoSearchScraper` en `app/scrapers/alcampo_search.py` (usa `send_with_retry`; plan-D6).
- **Depende:** T5, T9, T11
- **RF:** RF-3
- **Hecho cuando:** todos los casos pasan.

### [x] T13 — Scraper de búsqueda: cuerpos inválidos y traducción de errores
- **RED:**
  - `200` con HTML → `UpstreamUnavailableError`;
  - `200` con `{"foo": 1}` → `UpstreamUnavailableError`;
  - `503` persistente (`max_attempts=2`, `sleep` falso) → `UpstreamUnavailableError`;
  - `ConnectError` persistente → `UpstreamUnavailableError`, sin ninguna excepción de `httpx`.
- **GREEN:** validación del sobre con captura de `ValueError`/`ValidationError` → excepción de dominio.
- **Depende:** T12
- **RF:** RF-19, RF-20
- **Hecho cuando:** todos los casos pasan. **Fin de PR2.**

---

## PR3 — Cache, servicio, API, integración y docs

### [x] T14 — Repositorio de cache
- **RED:** `tests/services/test_search_cache.py`, con un `FakeAsyncRedis()` nuevo por test:
  - `get` en clave vacía → `None`;
  - `set` y luego `get` → la misma `ProductSearchResponse`;
  - la clave es `search:5:leche`;
  - `ttl` de la clave igual a `CACHE_TTL_SECONDS` (±1 s);
  - valor corrupto en la clave → `get` devuelve `None`.
- **GREEN:** `SearchCacheRepository` en `app/services/search_cache.py` (plan-D4, plan-D5, plan-D8).
- **Depende:** T4
- **RF:** RF-11, RF-12
- **Hecho cuando:** todos los casos pasan.

### [x] T15 — Servicio: camino sin cache
- **RED:** `tests/services/test_product_service.py`, con un scraper falso, cache real sobre fakeredis y reloj fijo:
  - miss → llama al scraper 1 vez;
  - `search` con `warehouse="5"`, `strategy_used="api"`, `scraped_at` igual al reloj y `total_results` igual al número de productos;
  - la respuesta queda guardada en cache;
  - una búsqueda sin resultados → `products=[]`, `total_results=0` **y** también se guarda en cache.
- **GREEN:** `ProductService.search(query)` en `app/services/product_service.py` (plan-D9).
- **Depende:** T8, T13, T14
- **RF:** RF-5, RF-10, RF-12
- **Hecho cuando:** todos los casos pasan.

### [x] T16 — Servicio: acierto de cache y errores
- **RED:**
  - hit → 0 llamadas al scraper;
  - hit guardado con `postal_code="28001"` y pedido con `"08001"` → la respuesta lleva `"08001"`;
  - hit → `scraped_at` original, no el del reloj actual;
  - el scraper lanza `UpstreamUnavailableError` → se propaga y Redis sigue vacío.
- **GREEN:** rama de hit (con reescritura de `postal_code` y `term`) y ausencia de `set` en error.
- **Depende:** T15
- **RF:** RF-11, RF-13, RF-14
- **Hecho cuando:** todos los casos pasan.

### [x] T17 — Ruta, providers, `lifespan` y handler `502`
- **RED:** `tests/api/test_products_route.py`, con `app.dependency_overrides[get_product_service]` apuntando a un servicio falso:
  - `GET /api/v1/products?postal_code=28001&term=leche` → `200` con el body del servicio;
  - sin `term` → `422`;
  - `term` de 51 caracteres → `422`;
  - el servicio lanza `UpstreamUnavailableError("secret upstream body")` → `502 {"detail": "Upstream service unavailable"}` y el body **no** contiene `"secret"`.
- **GREEN:** `app/core/dependencies.py`, `app/api/v1/products.py` (con `Annotated[ProductQuery, Query()]`) y `app/main.py` (`create_app`, `lifespan` con `create_http_client` y `create_redis`, handler).
- **Depende:** T16
- **RF:** RF-1, RF-2, RF-17 (plan-D10)
- **Hecho cuando:** todos los casos pasan y `uvicorn app.main:app` arranca con un `.env` válido (comprobación manual, sin llamar a la ruta).

### [x] T18 — Harness de integración + miss/hit end-to-end
- **RED:** `tests/integration/conftest.py`:
  - fixture `client`: `TestClient(create_app())` como context manager, con variables de entorno de test (`ALCAMPO_BASE_URL=https://alcampo.test`, `RETRY_BASE_DELAY=0`) y `create_redis` parcheado para devolver un `FakeAsyncRedis()` nuevo;
  - helper `mock_alcampo_search(respx_mock, json=..., status=..., headers=...)`.
  - Test `tests/integration/test_products_endpoint.py::test_miss_then_hit`: 1.ª petición → `200` y 1 llamada a Alcampo; 2.ª petición igual → `200`, mismo body y **seguimos en 1 llamada**.
- **GREEN:** solo el harness. Si la app no pasa, el problema está en T17.
- **Depende:** T17
- **RF:** RF-1, RF-11 (plan-D11)
- **Hecho cuando:** el test pasa. **Punto de control R3:** si `respx` interfiere con el `TestClient`, **parar y avisar** con la traza.

### [ ] T19 — Escenarios de integración de error y validación
- **RED:** en `test_products_endpoint.py`:
  - búsqueda sin resultados (fixture real) → `200`, `[]`;
  - `term="   "` → `422`, 0 llamadas a Alcampo y 0 claves en Redis;
  - `503` persistente → `502` y `RETRY_MAX_ATTEMPTS` llamadas;
  - `404` → `502` y **1** llamada;
  - `202` + `x-amzn-waf-action: challenge` → `502` y **1** llamada;
  - tras un `502`, Redis sin claves.
- **GREEN:** no debería hacer falta código nuevo. Si hace falta, se documenta el hueco que se ha descubierto.
- **Depende:** T18
- **RF:** RF-2, RF-5, RF-13, RF-16, RF-17, RF-18
- **Hecho cuando:** todos los casos pasan.

### [ ] T20 — Docs vivas
- **RED:** `tests/api/test_openapi.py`: `/openapi.json` contiene los schemas `ProductSearchResponse`, `Product` y `SearchMetadata`, y la ruta `/api/v1/products` con los parámetros `postal_code` y `term`.
- **GREEN:** ajustar `response_model` y los metadatos de la ruta si hace falta. Actualizar `README.md` con: uso del endpoint, ejemplo de respuesta, la limitación de la región por defecto (plan R1), `502` ante el WAF y la tabla de variables. Crear o actualizar `.env.example`: **está bloqueado por los permisos del agente (plan R6); se pedirá al usuario**.
- **Depende:** T19
- **RF:** RNF-6
- **Hecho cuando:** el test pasa, el README está revisado y `.env.example` existe con las 6 variables de la 001. **Fin de PR3.**

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T17, T18 |
| RF-2 | T4, T17, T19 |
| RF-3 | T12 |
| RF-4 | T8 |
| RF-5 | T8, T15, T19 |
| RF-6 | T7 |
| RF-7 | T6 |
| RF-8 | T4, T6 |
| RF-9 | T5, T8 |
| RF-10 | T4, T15 |
| RF-11 | T14, T16, T18 |
| RF-12 | T14, T15 |
| RF-13 | T16, T19 |
| RF-14 | T16 |
| RF-15 | T10 |
| RF-16 | T11, T19 |
| RF-17 | T3, T11, T17, T19 |
| RF-18 | T11, T19 |
| RF-19 | T5, T13 |
| RF-20 | T3, T11, T13 |
| RF-21 | T2 |

Ningún RF queda huérfano. RNF cubiertos: RNF-1 (T9, T17), RNF-2 (T12: exactamente 1 petición), RNF-3 (T9: timeout), RNF-4 (ruff `ANN`, en todas), RNF-5 (T14–T19: fakeredis nuevo por test, respx), RNF-6 (T20), RNF-7 (en la 001 no se añade logging propio, así que no hay nada que filtrar; se verificará con tests en la 003).

## Criterios de finalización de la spec (recordatorio)

La prueba manual contra Alcampo real (una sola petición) se hace **después de T20**, antes de abrir PR3, y se documenta en su descripción.
