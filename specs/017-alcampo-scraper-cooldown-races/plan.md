# Plan 017 — Carreras al iniciar el enfriamiento del WAF

- **Estado:** aprobado (2026-10-16), con las decisiones recomendadas
- **Fecha:** 2026-10-16
- **Spec:** [spec.md](spec.md). Sus decisiones se citan como **spec-D1…spec-D4**; las de este plan, **D1…**.

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `app/services/waf_cooldown.py` | `activate_in_redis`: `PTTL`; inicio con `SET NX`; si pierde o ya hay enfriamiento, se suma sin escribir | RF-1..RF-3 |
| `tests/services/test_waf_cooldown.py` | tests de las tres reproducciones | RF-1..RF-3 |

## 2. Decisiones de diseño

**D1 — `activate_in_redis`, paso a paso** (spec-D1..D3):

1. `left = await _left()`: `PTTL waf:cooldown`. `-2` (no existe) → `None`; `-1` (sin caducidad) → se trata como activo con lo que queda desconocido; `≥ 0` → `ceil(ms / 1000)`, mínimo 1.
2. Si hay enfriamiento: devuelve `(left, max(left, last or 0))` sin escribir (RF-2). Con `-1`, `left` es la duración base, solo para el espejo local.
3. Si no: `duration = _grow(last)` y `SET waf:cooldown 1 EX duration NX`.
   - Si escribió: `SET waf:cooldown:last duration EX max_seconds`; devuelve `(duration, duration)`.
   - Si no (otro lo inició entre medias): vuelve al paso 1 una sola vez y se suma (RF-1).
4. El resto de `activate` no cambia: `hold(left, last=duration)` y devuelve `left`.

**D2 — Por qué no `WATCH`**: con `WATCH` sobre dos claves, quien pierde reintenta la transacción entera. Con `NX`, quien pierde sabe que ya hay enfriamiento y solo lee, y no hace falta `multi()`, que no tiene anotaciones en redis-py 8.1.

**D3 — La escritura de `last` no es atómica con el inicio**: si el proceso muere entre los dos `SET`, queda un enfriamiento sin `last`. Ese es justo el caso de RF-2, que ya no lo reescribe; el siguiente bloqueo, sin `last`, empezará desde la base. Aceptado.

## 3. Estrategia de test

| RF | Test |
|---|---|
| RF-1 | La reproducción de la spec §1: dos repositorios sobre el mismo `FakeServer`, con el `GET` de B retenido hasta que A termina → `A 180`, `B ≤ 180`, `last == 180`, `TTL ≤ 180`. Y una carrera en el `SET NX`: B lee `PTTL` vacío, A inicia, el `SET NX` de B no escribe → B se suma |
| RF-2 | `waf:cooldown` con 600 s y sin `last` → `activate` devuelve ≈ 600 y el `TTL` sigue ≈ 600; con Redis caído después, el local sigue activo y crece desde 600 |
| RF-3 | `PTTL` de 400 ms → `activate` se suma (devuelve 1) y no crece; `waf:cooldown` sin caducidad → se suma y no la cambia |
| — | Regresión: todos los tests actuales de `test_waf_cooldown.py`, `test_product_service.py` y de integración del WAF, sin cambios |

## 4. Entrega

Un PR de unas 80 líneas de código y tests. Una sola tarea.
