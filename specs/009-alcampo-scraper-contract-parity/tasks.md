# Tasks 009 — Paridad de contrato con Mercadona y paginación

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 2 PRs: **PR 1** = T1–T4 (forma del contrato) · **PR 2** = T5–T7 (paginación).

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.
4. Tests sin red ni tiempo real. Fixtures de páginas **sintetizadas** a partir de respuestas reales (spec §10), encadenadas por tokens sintéticos.

---

## PR 1 — Forma del contrato

### [x] T1 — Parámetros de la búsqueda
> **Nota (2026-10-05):** RED real (`ImportError: MAX_PAGE` y 4 casos de `page`/`page_size` sin `422`); el de `term` de 101 ya pasaba (máximo 50) y queda como red de seguridad del nuevo límite. GREEN: `ProductQuery.page` (1–`MAX_PAGE` = 20) y `page_size` (1–100, por defecto 50), `SearchTerm` hasta 100. **Aserciones cambiadas, una a una:** `test_invalid_term_raises` (51 → 101) y `test_term_at_max_length_is_valid` (50 → 100) en `tests/models/test_product.py`; `test_term_over_50_chars_returns_422` pasa a `test_term_over_100_chars_returns_422` (51 → 101) en `tests/api/test_products_route.py`. Integración nueva: los 5 casos fuera de rango → `422` sin tocar Alcampo ni Redis.
- **RED:** `tests/models/test_product.py`: `page` por defecto 1, `page_size` por defecto 50; `page=0`, `page=21` (`MAX_PAGE`), `page_size=0`, `page_size=101` → `ValidationError`; `term` de 100 válido y de 101 inválido. Ruta: los mismos casos → `422` sin tocar Redis ni Alcampo.
- **GREEN:** `ProductQuery.page` (`1 ≤ page ≤ MAX_PAGE`), `ProductQuery.page_size` (1–100), `SearchTerm` hasta 100; `MAX_PAGE = 20` como constante (spec-D2).
- **Regresión:** el test de `term` de 51 → `422` pasa a 101; se anota.
- **RF:** RF-1, RF-3 (validación), RF-8 (tope)

### [x] T2 — Forma de la respuesta y paridad con Mercadona
> **Nota (2026-10-05):** fixture `tests/fixtures/mercadona_search_response_schema.json` generada desde la app de Mercadona (commit `3126851`, su `main`). `tests/api/test_contract_parity.py` compara la **forma** resolviendo referencias (nombres de modelo distintos). RED real, y con una diferencia **no vista en el análisis**: en Mercadona `products` es obligatorio y aquí tenía `default_factory=list`. GREEN: `page`, `page_size`, `total_pages` en `SearchMetadata` (el servicio rellena por ahora `1`, `50` y `1`/`0`); `image_url` y `category` no nulos; `products` obligatorio; en el modelo crudo `image.src` y `categoryPath` obligatorios, así que un producto sin ellos se descarta y cuenta como mal formado sin código nuevo en el mapper. **Tests sustituidos (afirmaban lo contrario de RF-11):** `test_map_product_without_category_path_returns_none_category` y `…_without_image_returns_none_image_url` (por 4 casos de descarte en `test_map_search.py`), `test_product_optional_fields_accept_none` (por "solo `price_format` es opcional" + "`image_url`/`category` nulos → error") y `test_product_without_category_path_defaults_to_empty_list` (→ `…_is_invalid`). **Ajustes:** 6 construcciones de `SearchMetadata` en tests con los 3 campos nuevos; `None` → valores sintéticos en `image_url`/`category`; `RAW_PRODUCT` de `test_product_service.py` con imagen. Paridad: **en verde**.
- **RED:**
  - `tests/fixtures/mercadona_search_response_schema.json`: esquema de `ProductSearchResponse` de Mercadona (copiado de su app; el 2026-10-01 ya se comparó).
  - `tests/api/test_contract_parity.py`: el esquema JSON de la respuesta de Alcampo coincide con la fixture en nombres de campos, tipos y obligatoriedad.
  - `tests/mappers/…`: un producto sin `image.src` o sin `categoryPath` se descarta (y cuenta en el log de descartes).
- **GREEN:** `SearchMetadata.page`, `.page_size`, `.total_pages`; `Product.image_url: str`, `.category: str`; el mapper descarta los que no los tienen (plan-D6, spec-D4). Mientras no llegue T4/T5, el servicio rellena `page=1`, `page_size=50`, `total_pages` de la página única.
- **Regresión:** fixtures y modelos de tests con imagen y categoría; se anota cada cambio.
- **RF:** RF-5 (forma), RF-11; criterio de paridad

### [x] T3 — Modelo crudo y scraper paginable
> **Nota (2026-10-05):** RED real en 4 tests (modelo sin `metadata`/`additionalPageInfo`, `search()` sin `page_size`/`page_token`, término de 80 enviado entero). `test_the_first_page_sends_no_page_token` ya pasaba (nunca se enviaba token) y queda como red de seguridad. GREEN: `AlcampoSearchMetadata.next_page_token`, `AlcampoAdditionalPageInfo.categories[].product_count`, todo opcional; `search(…, page_size=PAGE_SIZE, page_token=None)` con `pageToken` solo si hay token; `sent_term()` = `normalize_term(term)[:50].strip()` (`MAX_SENT_TERM_LENGTH`). Claves de cache y de agrupación sin cambios hasta T5/T6. **Regresión:** los 5 dobles de `test_product_service.py` con la firma nueva; ninguna aserción cambiada.
- **RED:** el modelo crudo lee `metadata.nextPageToken` (opcional) y `additionalPageInfo.categories[].productCount` (opcional); `search(term, client=…, page_size=…, page_token=…)` envía `maxPageSize`/`maxProductsToDecorate` = `page_size` y `pageToken` solo si hay token; el servicio envía `normalize_term(term)[:50].strip()` (un término de 60 → 50 caracteres, sin espacio final).
- **GREEN:** modelo y scraper (plan-D5).
- **Regresión:** dobles `FakeScraper` con la firma nueva.
- **RF:** RF-3 (envío), RF-7 (token)

### [x] T4 — Total y páginas
> **Nota (2026-10-05):** RED real (`ModuleNotFoundError: app.services.pagination` y, en el servicio, `total_results == 1` en vez de 669). GREEN: `estimate_total`, `is_last_page` y `page_totals(raw, *, page, page_size, on_page) -> PageTotals` en `app/services/pagination.py`; el servicio los usa para la primera página (aún `PAGE_SIZE` hasta T5). **Concreción del plan-D4 (no lo contradice):** fuera de la última página, el total nunca baja de lo ya servido (`max(estimación, (page − 1) × size + n)`), porque la estimación puede quedarse corta (669 frente a 670 en vivo); test propio. "La propia página si es la última" con 0 resultados da 0 páginas (página 1 vacía → 0 y 0). Sin regresiones: los tests existentes usan respuestas sin `metadata` (última página), cuyo total exacto es el `len(products)` de antes.
- **RED:** `tests/services/test_pagination.py`: `estimate_total` suma los `productCount` de primer nivel (y 0 sin categorías); en la última página (sin token) el total es exacto `(page − 1) × size + n`; `total_pages = min(ceil(total / size), MAX_PAGE)` o la propia página si es la última; página 1 vacía → 0 y 0.
- **GREEN:** funciones puras en `app/services/pagination.py` (plan-D4) y su uso en el servicio para la primera página.
- **RF:** RF-4, RF-5. **Fin del PR 1.**

---

## PR 2 — Paginación

### [x] T5 — `PageWalker`, tokens en la sesión y `404`
> **Nota (2026-10-05):** RED real (errores de colección: no existían `PageWalker`, `CursorKey`, `PageOutOfRangeError`; y la clave de cache sin página). GREEN: `PageWalker` y `CursorSession` en `app/services/pagination.py` (el protocolo `SearchScraper` se mueve allí desde el servicio); `AlcampoSessionClient.cursors` (y en el protocolo `RegionSession`); `PageOutOfRangeError` → `404 {"detail": "Page out of range"}` con log `INFO`; cache y agrupación con `{término enviado}:{page}:{page_size}`; cada página con su propio `search_timeout_seconds`. **Concreciones:** (1) la última página alcanzada antes de la pedida también se cachea (ya se pagó), pero nada para la pedida; (2) el token se borra solo ante un `4xx` (`status_code` presente) en una página pedida con token; (3) la clave usa ya el término enviado (`sent_term`, plan-D5), así que dos términos con los mismos 50 primeros caracteres comparten entrada; `search.term` sigue con la grafía del cliente hasta T6. Doble compartido `tests/services/pagination_doubles.py` (`ChainedScraper`, productos válidos `p{n}`). **Regresiones (todas por la clave/firma del cache, previstas en plan §4):** `search:5:leche` → `search:5:leche:1:50` en `test_search_cache.py` (5, incluido el mensaje de clave corrupta), `test_products_endpoint.py`, `test_auth.py`, `test_region_resolution.py` (`agua` en 5 y 32) y `test_product_service.py` (`search:32:leche`); `page=1, page_size=50` en 16 llamadas a `get`/`set` del repositorio (`test_search_cache.py`, `test_redis_circuit.py`, `test_product_service.py`); `test_key_is_search_warehouse_term` renombrado a `…_page_and_size`; `FakeSession` con `cursors`.
- **RED:** `tests/services/test_pagination.py` (scraper falso con páginas encadenadas por token):
  - página 3 sin tokens → 3 peticiones (1, 2, 3); las páginas 1 y 2 quedan cacheadas; la sesión guarda los tokens de 2, 3 y 4;
  - página 3 con los tokens de 1 y 2 → **1** petición (RF-7);
  - página 5 de una búsqueda de 3 páginas → `PageOutOfRangeError`, nada cacheado para la 5;
  - otra sesión (renovada) no ve los tokens de la anterior → vuelve a recorrer;
  - un `4xx` con un token guardado → se borra el token y `502`.
  - Ruta: `PageOutOfRangeError` → `404 {"detail": "Page out of range"}` con `X-Request-ID`.
- **GREEN:** `PageWalker` (plan-D2), `AlcampoSessionClient.cursors` (plan-D1), `PageOutOfRangeError` y su handler, claves de cache y de agrupación con `page` y `page_size`; cada página con su propio tiempo máximo.
- **Regresión:** claves `search:5:leche` → `search:5:leche:1:50` en los tests de cache, auth y límites.
- **RF:** RF-6, RF-7, RF-8, RF-9

### [x] T6 — `search.term` normalizado
> **Nota (2026-10-05):** RED real en 4 tests. GREEN: la respuesta lleva `term` = término enviado (`sent_term`); `_for_query` ya solo reetiqueta el código postal, porque la entrada de cache está indexada por el término enviado y es la misma para todos sus clientes. **Aserciones cambiadas (cambia plan-D1 de la spec 008 por spec-D5), una a una, en `tests/services/test_product_service.py`:** `test_other_case_hits_the_cache_and_keeps_the_client_term` → `…_returns_the_normalized_term` (`"Leche"` → `"leche"`); `test_miss_sends_the_normalized_term_and_keeps_the_client_term` → `test_miss_sends_and_returns_the_normalized_term` (`"LECHE   entera"` → `"leche entera"`); `test_spelling_variants_share_the_request_and_keep_their_own_term` → `…_and_the_normalized_term` (`"Leche"` → `"leche"`). Nuevo: un término de 80 caracteres devuelve los 49 cortados (`"a" * 49`). Ningún test de integración afirmaba la grafía del cliente.
- **RED:** `Leche` → `search.term == "leche"`; en cache y en respuestas compartidas también.
- **GREEN:** el servicio devuelve el término enviado (plan-D5).
- **Regresión:** los tests de la spec 008 que esperaban la grafía del cliente (`"Leche"`, `"LECHE   entera"`) pasan al normalizado; se anota cada uno (cambia la decisión plan-D1 de la 008 por spec-D5).
- **RF:** RF-10

### [ ] T7 — Integración, docs y verificación manual
- **RED:** `tests/integration/test_pagination.py` (app real + fakeredis + respx con páginas encadenadas): páginas 1, 2 y 3; la 3 repetida sin peticiones; una página fuera de rango → `404`; sin parámetros, la respuesta de siempre más los campos nuevos.
- README: parámetros `page`/`page_size`, total estimado y exacto en la última página, recorridos cortados por la ventana corta (plan-D3), cambios visibles (`search.term` normalizado, `image_url`/`category` nunca nulos).
- **Verificación manual** (`docker compose`, ≥10 min desde la última petición a Alcampo, **sin provocar errores** como en la spec 010): `leche` páginas 1, 2 y 3 en orden (3 búsquedas + la resolución de la región); la 3 repetida sale de cache; `page=20` de un término con pocas páginas → `404`. Comprobar `recent_traffic` si hubiera challenge.
- **RF:** RNF-2, RNF-4. **Fin del PR 2.**

## Trazabilidad

| RF | Tareas |
|---|---|
| RF-1 | T1, T3 |
| RF-2 | T7 |
| RF-3 | T1, T3 |
| RF-4, RF-5 | T2, T4 |
| RF-6 | T5, T7 |
| RF-7 | T3, T5 |
| RF-8 | T1, T5 |
| RF-9 | T5 |
| RF-10 | T6 |
| RF-11 | T2 |
