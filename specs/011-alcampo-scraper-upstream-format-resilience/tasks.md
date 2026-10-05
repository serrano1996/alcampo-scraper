# Tasks 011 — Resiliencia ante cambios de formato de Alcampo

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 1 PR (~250 líneas): T1–T3.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.
4. Tests sin red. Los cambios de formato se simulan modificando la fixture real (`alcampo_search_leche.json`), nunca con datos inventados de cero.

---

### [x] T1 — Qué campo falló y unidades desconocidas
> **Nota (2026-10-05):** RED real en 3 tests (log sin `fields=`, sin aviso de unidades); `test_known_price_units_are_not_warned` ya pasaba y queda como guarda contra falsos avisos. GREEN: `_failed_fields()` reúne `ruta:tipo` de `ValidationError.errors()` con los nombres de Alcampo (`loc` usa los alias: `image`, `price.amount`), sin `input` ni `msg`; el log de descartes añade `fields=[…]` ordenado y sin repetir (el valor `5.28` no aparece, comprobado); `WARNING` `unknown price units units=[…]` una vez por página mapeada. Sin regresiones: el resto del mensaje de descartes no cambia.
- **RED:** `tests/mappers/test_map_search.py`:
  - 1 producto sin `image` y otro con `price.amount` numérico entre válidos → `WARNING` con `fields=['image:missing', 'price.amount:string_type']` (ordenados, sin repetir) además de los ids; **el valor** (`5.28`) **no** aparece en el log (plan-D3).
  - dos productos con el mismo fallo → el campo aparece una vez.
  - `unitName: "PER_100G"` → `price_format is None` y un `WARNING` `unknown price units` con `'PER_100G'`; dos productos con la misma unidad → una sola línea, la unidad una vez (plan-D5).
  - unidades conocidas → ningún aviso.
- **GREEN:** `map_search` reúne `ruta:tipo` de `ValidationError.errors()` (sin `input` ni `msg`) y las `unitName` fuera de `UNIT_SUFFIXES`.
- **RF:** RF-4, RF-5, RF-6

### [ ] T2 — Todo roto → `502` sin cache
- **RED:**
  - `tests/mappers/test_map_search.py`: todo roto → `UpstreamFormatError` con `discarded=N` y los campos en el motivo, y **ningún** log del mapper (plan-D2); sin productos (`productGroups` vacío o grupos sin productos) → `[]`, sin excepción (RF-2).
  - `tests/services/test_product_service.py`: todo roto → `UpstreamFormatError`, nada en cache y **sin** enfriamiento (plan-D4); recorrido en frío a la página 3 con la página 2 rota → `UpstreamFormatError`, solo la página 1 en cache (RF-3).
- **GREEN:** `UpstreamFormatError(UpstreamUnavailableError)` en `app/exceptions.py`; el mapper la lanza si llegaron productos y no queda ninguno (plan-D1).
- **Regresión:** `test_discarding_every_product_is_an_error` pasa a esperar la excepción (plan §4); se anota.
- **RF:** RF-1, RF-2, RF-3

### [ ] T3 — Campos no usados, integración, docs y verificación manual
- **Tests (sin RED posible, RF-7 ya se cumple; mutación anotada):** `tests/models/test_alcampo.py` sobre la fixture real: quitar campos no usados (`brand`, `productId`, `promotions`, `available`, `images`) y cambiarlos de tipo (`available` a texto, `brand` a número, `promotions` a objeto) → los mismos productos mapeados. Mutación: un campo no usado declarado como obligatorio en el modelo → el test falla.
- **Integración** (`tests/integration/test_upstream_format.py`, app real + fakeredis + respx): fixture real con `price.amount` numérico en todos → `502 {"detail": "Upstream service unavailable"}`, un `ERROR` de `app.main` con `price.amount:string_type`, nada en cache, enfriamiento inactivo; acto seguido Alcampo "arreglado" (fixture original) → `200` con productos sin esperar al TTL.
- README: el `502` por cambio de formato (antes `200 []` cacheado), qué dicen los logs, unidades desconocidas.
- **Verificación manual** (`docker compose`, ≥10 min desde la última petición a Alcampo): 1 búsqueda real → `200` con productos, sin `WARNING` de descartes ni de unidades (o, si aparece una unidad desconocida, anotarla: es un dato nuevo para spec-D3). ~10 peticiones (resolución + búsqueda).
- **RF:** RF-7; criterios de finalización

## Trazabilidad

| RF | Tareas |
|---|---|
| RF-1, RF-2, RF-3 | T2, T3 |
| RF-4, RF-5, RF-6 | T1 |
| RF-7 | T3 |
