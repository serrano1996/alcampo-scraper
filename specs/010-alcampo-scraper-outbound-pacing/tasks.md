# Tasks 010 — Ritmo de salida hacia Alcampo

- **Estado:** aprobado (2026-10-01)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Entrega:** 2 PRs: **PR 1** = T1–T4 · **PR 2** = T5–T7.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Tiempo y aleatoriedad inyectables: nunca `sleep` real en los tests.
4. `.env.example` lo edita el usuario.

---

### [ ] T1 — `Settings`
- **RED:** `alcampo_rate_limit == 10`; `alcampo_rate_limit_long == 30` (`≥ 0`); `alcampo_rate_window_long_seconds == 900` (`≥ 1`); `alcampo_min_interval_ms == 500` (`≥ 0`); `alcampo_interval_jitter_ms == 500` (`≥ 0`); negativos → `ValidationError`.
- **Regresión:** `.env.example` → **parar y pedir al usuario** las 4 variables nuevas (y el nuevo 10 de `ALCAMPO_RATE_LIMIT`).
- **RF:** RF-1, RF-2, RF-3

### [ ] T2 — `release` en el limitador
- **RED:** `acquire()` devuelve un id; `release(id)` devuelve el hueco (en Redis y en el respaldo local); con límite 0, id vacío y `release` sin efecto.
- **RF:** RF-1 (plan-D2)

### [ ] T3 — `OutboundPacer`
- **RED:** con reloj, espera y `uniform` falsos: la 1.ª petición no espera; la 2.ª espera `min + jitter`; peticiones concurrentes salen en fila; `min = 0` → nunca espera.
- **RF:** RF-3

### [ ] T4 — `TrafficLog`
- **RED:** `record(kind)` y `record_status(...)`; `summary()` con recuentos 1/5/15 min por tipo y `4xx`; las entradas de más de 15 min se descartan.
- **RF:** RF-6. **Fin del PR 1.**

### [ ] T5 — `OutboundGate` y cableado
- **RED:** orden espaciado → larga → corta; rechazo de la corta libera la larga; la puerta registra tipo y estado; `401` → `WARNING` con código, endpoint y tipo; `404`/`429` sin `WARNING`. Scraper y cliente de sesión pasan por la puerta (tipo por endpoint, plan-D5).
- **Regresión (plan §4):** `integration_env` con espaciado y límites desactivados; helpers de scraper y sesión con una puerta desactivada.
- **RF:** RF-1, RF-3, RF-5

### [ ] T6 — Desglose en el challenge
- **RED:** tras tráfico simulado y un `400`, el `ERROR` del challenge incluye `recent_traffic` con los recuentos 1/5/15 min por tipo y los `4xx`.
- **RF:** RF-6

### [ ] T7 — Docs y verificación manual
- README (límites revisados y por qué, espaciado, desglose en los challenges) y `.env.example` (ya hecho en T1).
- **Verificación manual:** `docker compose`, **≥ 1 h después del bloqueo del 01-10**, 1 búsqueda real de un CP nuevo (1 resolución): comprobar en los logs que las ~10 peticiones salen espaciadas (~0,5–1 s) y la búsqueda termina en menos de 15 s. Sin provocar challenges.
- **RF:** RNF-4. **Fin del PR 2.**

## Trazabilidad

| RF | Tareas |
|---|---|
| RF-1 | T1, T2, T5 |
| RF-2 | T1 |
| RF-3 | T1, T3, T5 |
| RF-4 | T5 (registro de 4xx); cumplimiento en la spec 009 |
| RF-5 | T5 |
| RF-6 | T4, T6 |
