# Plan 009 — Paridad de contrato con Mercadona y paginación

- **Estado:** aprobado (2026-10-05). Entrega: 2 PRs (§7)
- **Fecha:** 2026-10-05
- **Spec:** [spec.md](spec.md) (aprobada; verificación en vivo en su sección 10). Sus decisiones se citan como **spec-D1…spec-D7**; las de este plan son **D1…**
- **Depende de:** spec 010 (ritmo de salida), ya implementada: cada página recorrida pasa por la puerta de salida

## 1. Visión general

```
GET /api/v1/products?postal_code&term&page=1&page_size=50
  └─ ProductQuery: page ≥ 1 y ≤ MAX_PAGE (20), page_size 1–100, term 1–100   → 422      RF-1, RF-3, RF-8
     └─ ProductService.search
          region = RegionService.region_for(cp)                                         (007)
          term = normalize_term(query.term)[:50].strip()     lo que se envía a Alcampo   RF-3, RF-10
          cache search:{retailer}:{term}:{page}:{page_size}  → hit                        RF-9
          miss → InFlight "{retailer}:{term}:{page}:{page_size}" →
              session = RegionSessions.get(region)
              PageWalker: desde la mayor página k ≤ page cuyo token conoce la sesión,
                 pide k, k+1, …, page  (cada una por la puerta de salida, spec 010)
                 guarda el token de la siguiente en la sesión y cachea cada página
                 una página sin nextPageToken antes de llegar → PageOutOfRangeError → 404  RF-6
              total = Σ productCount de categorías de 1.er nivel; exacto en la última página RF-4
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/models/product.py` | `ProductQuery.page`, `.page_size`; `SearchTerm` hasta 100; `SearchMetadata.page`, `.page_size`, `.total_pages`; `Product.image_url`/`.category` no nulos | RF-1, RF-3, RF-5, RF-11 |
| `app/models/alcampo.py` | `AlcampoSearchResponse.metadata.next_page_token` y `additional_page_info.categories[].product_count`, opcionales | RF-4, RF-7 |
| `app/mappers/product_mapper.py` | descartar productos sin imagen o sin categoría (como cualquier mal formado) | RF-11 |
| `app/scrapers/alcampo_search.py` | `search(term, *, client, page_size, page_token=None)` | RF-1, RF-7 |
| `app/services/pagination.py` | **nuevo**: `estimate_total`, `total_pages`, `PageWalker` | RF-4…RF-8 |
| `app/scrapers/alcampo_session.py` | `cursors`: tokens de página de esta sesión | RF-7 |
| `app/services/product_service.py` | página pedida, término enviado, clave de cache y de agrupación con `page`/`page_size` | RF-2, RF-9, RF-10 |
| `app/exceptions.py`, `app/main.py` | `PageOutOfRangeError` → `404 {"detail": "Page out of range"}` | RF-6 |
| `tests/fixtures/mercadona_search_response_schema.json` | copia del esquema de respuesta de Mercadona (2026-10-01) | criterio de paridad |

## 3. Decisiones de diseño

**D1 — Los tokens viven en la sesión de la región** (`AlcampoSessionClient.cursors: dict[(término, page_size, página), token]`), porque Alcampo los liga a la sesión (spec §10: `400` en otra sesión). Al renovarse la sesión (cada 50 min, spec 007) se crea otro cliente con `cursors` vacío: los tokens viejos **no se pueden reutilizar** por construcción, que es justo lo que pide la spec 010 RF-4 (no provocar `4xx`).
- *Descartada:* un registro aparte indexado por sesión. Habría que limpiarlo a mano al renovar; ligado al objeto, desaparece con él.

**D2 — `PageWalker`: recorrer desde la mayor página conocida.** Para la página N busca la mayor `k ≤ N` con token en la sesión (la 1 no lo necesita) y pide `k…N` una a una. **Cada página intermedia se cachea** (ya se ha pagado) y deja el token de la siguiente. Una página sin `nextPageToken` antes de llegar a N → `PageOutOfRangeError`.
- Cada petición pasa por la puerta de salida (espaciado y ventanas de la spec 010) y por su **propio** tiempo máximo. *Concreción:* un tiempo máximo para todo el recorrido no cabe con el espaciado (~1 s por petición).

**D3 — Tensión con los límites de la spec 010, asumida y documentada.** Llegar en frío a la página 20 son 20 peticiones; la ventana corta admite 10 por minuto, así que el recorrido **se corta con `502`** a mitad. No se pierde lo hecho: las páginas recorridas quedan en cache y sus tokens en la sesión, y el siguiente intento sigue desde ahí. Se acepta: es el precio de no dar más tráfico al WAF, y lo habitual es pedir las páginas en orden (cada una cuesta 1 petición).
- *Descartada:* exceptuar el recorrido de los límites. Es exactamente la ráfaga que la 010 quiere evitar.

**D4 — Total y páginas** (spec-D3): `estimate_total` suma los `productCount` de `additionalPageInfo.categories` de primer nivel; en una página sin `nextPageToken` el total es **exacto**: `(page − 1) × page_size + productos de esa página`. `total_pages = min(ceil(total / page_size), MAX_PAGE)`, o la propia página si es la última. Página 1 sin resultados: `total_results = 0`, `total_pages = 0`, `200`.

**D5 — Término enviado y término devuelto** (spec-D5, spec-D6): `normalize_term(term)[:50].strip()`; eso es lo que se busca, la clave de cache y lo que devuelve `search.term` ("el término que se usó realmente", como en Mercadona).

**D6 — Prueba de paridad:** el esquema de Mercadona se guarda como fixture (copiado de su app el 2026-10-01) y un test compara, de `ProductSearchResponse`, nombres de campos, tipos y obligatoriedad. Si Mercadona cambia, se actualiza la fixture conscientemente.

## 4. Regresiones previstas

| Tests | Por qué | Corrección |
|---|---|---|
| Claves `search:5:leche` (cache, auth, límites) | la clave incluye página y tamaño | `search:5:leche:1:50` |
| `term` de 51 caracteres → `422` | el máximo pasa a 100 | 101 → `422`; 51 válido |
| `search.term` con la grafía del cliente (spec 008 T2/T7) | spec-D5 | se espera el término normalizado; se anota cada test |
| Productos sin imagen o categoría en tests | ahora se descartan | fixtures con ambos campos (la real ya los trae) |
| `FakeScraper.search(term, client)` | nuevos parámetros | firma ampliada en los dobles |

## 5. Estrategia de test

| RF | Test |
|---|---|
| RF-1, RF-3, RF-8 | `page` 0/21, `page_size` 0/101, `term` 101 → `422`; `term` de 60 → a Alcampo van 50 |
| RF-2 | sin parámetros: misma primera página de 50 |
| RF-4, RF-5 | estimación por categorías; total exacto y `total_pages` en la última página; página 1 vacía → 0/0 |
| RF-6 | página más allá de la última → `404 "Page out of range"`, nada cacheado |
| RF-7 | página 3 con los tokens de 1 y 2 en la sesión → **1** petición; tras renovar la sesión, se vuelve a recorrer |
| RF-9 | cada página en su clave; las intermedias de un recorrido quedan cacheadas |
| RF-10 | `search.term` normalizado (y recortado a 50) |
| RF-11 | producto sin imagen o sin categoría → descartado |
| Paridad | esquema de `ProductSearchResponse` = fixture de Mercadona |

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Recorridos profundos cortados por la ventana corta (D3) | Progreso guardado; documentado |
| R2 | La estimación del total cambia entre páginas (669/670 en vivo) | Documentado como estimación; exacto en la última |
| R3 | Un token rechazado por Alcampo pese a D1 | `WARNING` de la puerta (spec 010); se borra el token y `502` |
| R4 | Tamaño | 2 PRs (§7) |

## 7. Secuencia y entrega

| # | Tarea | PR |
|---|---|---|
| 1 | `ProductQuery`: `page`, `page_size`, `term` ≤ 100 | 1 |
| 2 | Forma de la respuesta (`page`, `page_size`, `total_pages`, no nulos) + descarte en el mapper + test de paridad con Mercadona | 1 |
| 3 | Modelo crudo (token, categorías) + `search(…, page_size, page_token)` y término recortado a 50 | 1 |
| 4 | `estimate_total` y `total_pages` | 1 |
| 5 | `PageWalker`, tokens en la sesión, `404`, claves con página | 2 |
| 6 | `search.term` normalizado + regresiones | 2 |
| 7 | Integración, docs y verificación manual (páginas 1–3 de `leche`; la 3 repetida sale de cache; fuera de rango → `404`) | 2 |

~800 líneas: **2 PRs** de ~400.
