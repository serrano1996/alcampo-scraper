# Tasks 009 — Paridad de contrato con Mercadona y paginación

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 2 PRs: **PR 1** = T1–T4 (forma del contrato) · **PR 2** = T5–T7 (paginación).

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.
4. Tests sin red ni tiempo real. Fixtures de páginas **sintetizadas** a partir de respuestas reales (spec §10), encadenadas por tokens sintéticos.

---

## PR 1 — Forma del contrato

### [ ] T1 — Parámetros de la búsqueda
- **RED:** `tests/models/test_product.py`: `page` por defecto 1, `page_size` por defecto 50; `page=0`, `page=21` (`MAX_PAGE`), `page_size=0`, `page_size=101` → `ValidationError`; `term` de 100 válido y de 101 inválido. Ruta: los mismos casos → `422` sin tocar Redis ni Alcampo.
- **GREEN:** `ProductQuery.page` (`1 ≤ page ≤ MAX_PAGE`), `ProductQuery.page_size` (1–100), `SearchTerm` hasta 100; `MAX_PAGE = 20` como constante (spec-D2).
- **Regresión:** el test de `term` de 51 → `422` pasa a 101; se anota.
- **RF:** RF-1, RF-3 (validación), RF-8 (tope)

### [ ] T2 — Forma de la respuesta y paridad con Mercadona
- **RED:**
  - `tests/fixtures/mercadona_search_response_schema.json`: esquema de `ProductSearchResponse` de Mercadona (copiado de su app; el 2026-10-01 ya se comparó).
  - `tests/api/test_contract_parity.py`: el esquema JSON de la respuesta de Alcampo coincide con la fixture en nombres de campos, tipos y obligatoriedad.
  - `tests/mappers/…`: un producto sin `image.src` o sin `categoryPath` se descarta (y cuenta en el log de descartes).
- **GREEN:** `SearchMetadata.page`, `.page_size`, `.total_pages`; `Product.image_url: str`, `.category: str`; el mapper descarta los que no los tienen (plan-D6, spec-D4). Mientras no llegue T4/T5, el servicio rellena `page=1`, `page_size=50`, `total_pages` de la página única.
- **Regresión:** fixtures y modelos de tests con imagen y categoría; se anota cada cambio.
- **RF:** RF-5 (forma), RF-11; criterio de paridad

### [ ] T3 — Modelo crudo y scraper paginable
- **RED:** el modelo crudo lee `metadata.nextPageToken` (opcional) y `additionalPageInfo.categories[].productCount` (opcional); `search(term, client=…, page_size=…, page_token=…)` envía `maxPageSize`/`maxProductsToDecorate` = `page_size` y `pageToken` solo si hay token; el servicio envía `normalize_term(term)[:50].strip()` (un término de 60 → 50 caracteres, sin espacio final).
- **GREEN:** modelo y scraper (plan-D5).
- **Regresión:** dobles `FakeScraper` con la firma nueva.
- **RF:** RF-3 (envío), RF-7 (token)

### [ ] T4 — Total y páginas
- **RED:** `tests/services/test_pagination.py`: `estimate_total` suma los `productCount` de primer nivel (y 0 sin categorías); en la última página (sin token) el total es exacto `(page − 1) × size + n`; `total_pages = min(ceil(total / size), MAX_PAGE)` o la propia página si es la última; página 1 vacía → 0 y 0.
- **GREEN:** funciones puras en `app/services/pagination.py` (plan-D4) y su uso en el servicio para la primera página.
- **RF:** RF-4, RF-5. **Fin del PR 1.**

---

## PR 2 — Paginación

### [ ] T5 — `PageWalker`, tokens en la sesión y `404`
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

### [ ] T6 — `search.term` normalizado
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
