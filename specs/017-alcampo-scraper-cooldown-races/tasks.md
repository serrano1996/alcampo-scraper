# Tasks 017 — Carreras al iniciar el enfriamiento del WAF

- **Estado:** aprobado (2026-10-16), con las decisiones recomendadas
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**: es la reproducción del caso de la spec §1.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(017-alcampo-scraper-cooldown-races): <descripción en inglés> (Tn)`.

---

### [x] T1 — Un solo inicio de enfriamiento por bloqueo (F1, F2, F3)
- **RED:** `test_waf_cooldown.py` (plan §3, RF-1..RF-3). Los cinco fallaban por el motivo esperado: `B 360`; el perdedor del inicio lo acortaba a 60; el de 600 s sin `last` pasaba a 180; con 400 ms de vida crecía a 360 (fakeredis da `TTL 0` con la clave viva, lo que confirma F3 al menos ahí); el puesto sin caducidad se reescribía.
- **GREEN:** `waf_cooldown.py` (plan-D1): `in_force()` con `PTTL`, inicio con `SET … NX`, y quien pierde se suma.
- **Comprobado:**
  - Sin `nx=True` fallan 2 tests (prueba de mutación).
  - La reproducción de la spec §1 da ahora `A 180, B 180, last 180` y `600` sin acortar.
  - `ruff`, `mypy` y `pytest` (594) en verde. Sin cambios visibles en el README.
- **RF:** RF-1, RF-2, RF-3
