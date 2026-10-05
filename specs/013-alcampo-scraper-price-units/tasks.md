# Tasks 013 — Unidades de precio reales

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 1 PR: T1–T2.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.

---

### [ ] T1 — Fixture real y tabla de unidades
- **Captura (en vivo, plan-D1, plan-D2):** ≥10 min desde la última petición; script en el scratchpad con el cliente de la app: portada + búsqueda `arroz` (2 peticiones). Recortar a 3 productos `PER_1KG` y 1 `PER_LITRE`; quitar `metadata` y `additionalPageInfo`; comprobar que no hay cookies, CSRF ni `visitorId`. Guardar `tests/fixtures/alcampo_search_arroz.json` y anotar fecha y recuento de unidades de la respuesta completa.
- **RED:**
  - `map_search` sobre la fixture de arroz: los productos `PER_1KG` dan `"<amount> €/kg"` y ningún `WARNING` de unidades (RF-1).
  - `UNIT_SUFFIXES.keys() == {"PER_LITRE", "PER_1KG"}` (plan-D3, RF-2).
  - `PER_KG`, `PER_EACH`, `PER_METER` → `price_format is None` (spec-D1, RF-3).
- **GREEN:** la tabla y el docstring del mapper (evidencia de cada unidad).
- **Regresión:** los casos de plan §3; se anotan.
- **RF:** RF-1, RF-2, RF-3

### [ ] T2 — Docs
- README: la limitación de `price_format` reescrita (verificadas `PER_LITRE` y `PER_1KG`; el resto, `null` + `WARNING`); nota en la spec 001 (spec-D8) remitiendo a esta.
- Sin verificación en vivo adicional: la captura de T1 lo es (spec §7).
- **RF:** criterios de finalización
