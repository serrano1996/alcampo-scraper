# Plan 011 — Resiliencia ante cambios de formato de Alcampo

- **Estado:** aprobado (2026-10-05)
- **Fecha:** 2026-10-05
- **Spec:** [spec.md](spec.md) (aprobada; decisiones citadas como **spec-D1…spec-D4**; las de este plan, **D1…**)
- **Entrega:** 1 PR (~250 líneas)

## 1. Visión general

```
scraper → AlcampoSearchResponse (envoltorio; roto → 502, ya hoy)
  └─ map_search(raw)
       cada producto: AlcampoProduct.model_validate
         ├─ válido → Product (dedupe por id); unitName desconocida → se anota         RF-6
         └─ inválido → descartado; se anotan id y "campo:tipo" de cada error         RF-5
       recibidos > 0 y válidos = 0 → UpstreamFormatError (→ 502, sin cache)          RF-1, RF-3
       recibidos = 0               → []  (200, cacheado)                             RF-2
       descartes parciales         → WARNING con ids y campos                        RF-4, RF-5
       unidades desconocidas       → WARNING con las unidades                        RF-6
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/exceptions.py` | **nuevo** `UpstreamFormatError(UpstreamUnavailableError)` | RF-1 |
| `app/mappers/product_mapper.py` | campos fallidos en el log; unidades desconocidas; lanzar `UpstreamFormatError` si no queda ninguno | RF-1, RF-4…RF-6 |
| `app/services/product_service.py` | nada: el mapeo ya ocurre antes de cachear, y en un recorrido cada página se mapea antes de cachearla (spec 009) | RF-1, RF-3 |
| `README.md` | `502` por formato, logs nuevos, limitación | — |

## 3. Decisiones de diseño

**D1 — El mapper lanza la excepción de dominio.** `map_search` ya es quien sabe cuántos productos llegaron y cuántos se descartaron; lanzar ahí `UpstreamFormatError` hace que el servicio no cambie: `_response()` mapea **antes** de `_store()`, así que no se cachea nada (RF-1), y en un recorrido la página rota lanza dentro de `on_passed` después de cachear las anteriores (RF-3, spec-D4).
- *Descartada:* que `map_search` devuelva un informe y el servicio decida. Más código en el servicio para una regla que es del mapeo.

**D2 — Un único `ERROR` por incidente.** En el caso de RF-1 el mapper **no** registra nada: el motivo de la excepción lleva el detalle (`all 50 products malformed fields=['price.amount:string_type']`) y el manejador del `502` ya registra un `ERROR` con el motivo (spec 003 RF-11), con el término y el código postal. Hoy serían dos `ERROR` para lo mismo.
- *Consecuencia:* el test de la spec 003 que espera el `ERROR` del mapper (`test_discarding_every_product_is_an_error`) pasa a esperar la excepción con el detalle. Se anota.

**D3 — Detalle de un error: `ruta:tipo`** (spec-D2). De `ValidationError.errors()`: `loc` unido con `.` (con los **nombres de Alcampo**, p. ej. `retailerProductId`, `image.src`, que es lo que hay que buscar en su JSON) y `type` de Pydantic (`missing`, `string_type`, `string_pattern_mismatch`…). Nunca `input` ni `msg` (pueden incluir el valor). Sin repetir y ordenados, para que dos búsquedas con el mismo fallo den la misma línea.

**D4 — No es un challenge:** `UpstreamFormatError` hereda de `UpstreamUnavailableError`, no de `UpstreamBlockedError`, así que no activa el enfriamiento del WAF, y no es `UpstreamThrottledError`, así que se registra como `ERROR` (accionable), no `WARNING`.

**D5 — Unidades desconocidas** (spec-D3): `WARNING` `unknown price units units=['PER_100G']` una vez por página mapeada (sin repetir unidades). Solo en un *miss*: las páginas cacheadas no se vuelven a mapear, así que no inunda el log.

## 4. Regresiones previstas

| Test | Por qué | Corrección |
|---|---|---|
| `tests/mappers/test_map_search.py::test_discarding_every_product_is_an_error` | ahora lanza en vez de registrar (D2) | espera `UpstreamFormatError` con `discarded`/campos en el motivo y ningún log del mapper |

## 5. Estrategia de test

| RF | Test |
|---|---|
| RF-1 | mapper: todo roto → `UpstreamFormatError`; servicio: nada en cache; integración: `502` y, con Alcampo "arreglado", `200` al momento |
| RF-2 | mapper: sin productos → `[]` sin excepción; servicio: cacheado |
| RF-3 | servicio con `ChainedScraper` (spec 009) cuya página 2 está rota: `502` pidiendo la 3, página 1 cacheada |
| RF-4, RF-5 | mapper: 1 roto de 3 → `WARNING` con `image.src:missing`, sin el valor |
| RF-6 | mapper: `unitName` desconocida → `WARNING` con la unidad; `price_format: null` |
| RF-7 | modelo sobre la fixture real: quitar y cambiar de tipo campos no usados → mismos productos |
| D4 | integración: `502` sin enfriamiento activado |

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Un fallo parcial grande (p. ej. el 90 % roto) sigue dando `200` | spec-D1 asumido; el `WARNING` con campos lo hace visible |
| R2 | `WARNING` de unidades ruidoso si Alcampo usa muchas unidades nuevas | una línea por *miss*; si molesta, se baja a `INFO` |

## 7. Secuencia

| # | Tarea |
|---|---|
| 1 | Campos fallidos en el log de descartes y aviso de unidades desconocidas (RF-4, RF-5, RF-6) |
| 2 | `UpstreamFormatError`: todo roto → `502` sin cache, también en un recorrido (RF-1, RF-2, RF-3) |
| 3 | Campos no usados fijados con tests (RF-7), integración, README y verificación manual |
