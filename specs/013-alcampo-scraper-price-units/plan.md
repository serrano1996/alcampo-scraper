# Plan 013 — Unidades de precio reales

- **Estado:** aprobado (2026-10-05)
- **Fecha:** 2026-10-05
- **Spec:** [spec.md](spec.md) (aprobada; decisiones citadas como **spec-D1, spec-D2**; las de este plan, **D1…**)
- **Entrega:** 1 PR (~100 líneas, sin contar la fixture)

## 1. Cambios

| Fichero | Cambio | RF |
|---|---|---|
| `app/mappers/product_mapper.py` | `UNIT_SUFFIXES = {"PER_LITRE": "L", "PER_1KG": "kg"}`; docstring con la evidencia de cada una | RF-1, RF-2 |
| `tests/fixtures/alcampo_search_arroz.json` | **nueva**, real y recortada (D1, D2) | RF-2 |
| `tests/mappers/…` | casos de la tabla; fixture real | RF-1…RF-3 |
| `README.md` | limitación de unidades reescrita | — |

## 2. Decisiones de diseño

**D1 — Captura con el propio código de la app.** Un script en el scratchpad (fuera del repo) crea el cliente HTTP de la app (`create_http_client`: mismo User-Agent y cabeceras), abre una sesión (`AlcampoSessionClient.open()`, 1 petición: portada) y busca `arroz` con `AlcampoSearchScraper` (1 petición), ambas por la puerta de salida de la spec 010. Así la captura tiene la misma huella que el tráfico normal. Sin cadena de región (spec-D2): la unidad no depende de ella. Pausa previa ≥10 min desde la última petición.

**D2 — Fixture recortada y sin datos de sesión.** Se guardan **3 productos `PER_1KG` y 1 `PER_LITRE`** completos, tal como llegan (solo se recorta la lista). Se quitan `metadata` (token de página ligado a la sesión) y `additionalPageInfo` (irrelevante aquí). La respuesta de búsqueda no lleva cookies, CSRF ni `visitorId`; se comprueba antes de guardar (búsqueda de esas cadenas en el JSON).

**D3 — La prueba de la tabla es explícita.** Un test fija `UNIT_SUFFIXES.keys() == {"PER_LITRE", "PER_1KG"}`: añadir una unidad obliga a tocar el test, que pide su fixture (RF-2).

## 3. Regresiones previstas

| Test | Por qué | Corrección |
|---|---|---|
| `tests/mappers/test_product_mapper.py::test_format_unit_price_known_units` (casos `PER_KG`, `PER_EACH`, `PER_METER`) | spec-D1: ya no están en la tabla | pasan a dar `null`; se añade `PER_1KG` |
| `tests/mappers/test_map_search.py::test_known_price_units_are_not_warned` (usa `PER_KG`) | ahora es desconocida | `PER_1KG` |

## 4. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Existe un `PER_EACH` real que hoy funcionaba por casualidad | No: el nombre nunca se ha visto. Si existe, el `WARNING` de la spec 011 lo dice y se añade con su fixture |
| R2 | Challenge del WAF al capturar | 2 peticiones, ≥10 min después de la anterior, sin errores provocados |
