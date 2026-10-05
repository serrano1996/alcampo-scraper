# Tasks 010 — Ritmo de salida hacia Alcampo

- **Estado:** completada (2026-10-05): T1–T7
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Entrega:** 2 PRs: **PR 1** = T1–T4 · **PR 2** = T5–T7.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Tiempo y aleatoriedad inyectables: nunca `sleep` real en los tests.
4. `.env.example` lo edita el usuario.

---

### [x] T1 — `Settings`
> **Nota (2026-10-01):** RED real (8 fallos: campos inexistentes, validaciones que no saltaban y el `20 == 10`). **Aserción cambiada, justificada:** `test_outbound_protection_defaults` (spec 008) pasa de `alcampo_rate_limit == 20` a `10` por spec-D2, con un comentario. **Regresión adelantada de T5 a T1** (regla: se corrige en la tarea que la provoca): bajar el límite a 10 rompía 5 tests de integración que superan 10 peticiones (cadena + búsqueda); `integration_env` desactiva ahora `ALCAMPO_RATE_LIMIT`, `ALCAMPO_RATE_LIMIT_LONG` y `ALCAMPO_MIN_INTERVAL_MS`, y los tests de límite fijan los suyos. `.env.example`: el usuario añadió las 4 variables y cambió `ALCAMPO_RATE_LIMIT` a 10.
- **RED:** `alcampo_rate_limit == 10`; `alcampo_rate_limit_long == 30` (`≥ 0`); `alcampo_rate_window_long_seconds == 900` (`≥ 1`); `alcampo_min_interval_ms == 500` (`≥ 0`); `alcampo_interval_jitter_ms == 500` (`≥ 0`); negativos → `ValidationError`.
- **Regresión:** `.env.example` → **parar y pedir al usuario** las 4 variables nuevas (y el nuevo 10 de `ALCAMPO_RATE_LIMIT`).
- **RF:** RF-1, RF-2, RF-3

### [x] T2 — `release` en el limitador
> **Nota (2026-10-05):** RED real (`AttributeError: release` y `acquire` sin valor). `acquire()` devuelve el id del hueco (el mismo miembro del sorted set de Redis, o una entrada del respaldo local); `release(id)` lo quita del respaldo local si está ahí y, si no, de Redis (por el circuito; un fallo de Redis solo deja un aviso). Con límite 0, `""` y `release` sin efecto. `mypy` detectó el protocolo `ResolutionLimiter` de `RegionService` con `-> None`: pasa a `-> str`.
- **RED:** `acquire()` devuelve un id; `release(id)` devuelve el hueco (en Redis y en el respaldo local); con límite 0, id vacío y `release` sin efecto.
- **RF:** RF-1 (plan-D2)

### [x] T3 — `OutboundPacer`
> **Nota (2026-10-05):** RED real (`ModuleNotFoundError: app.services.outbound`). `OutboundPacer.wait_turn()` en `app/services/outbound.py`: un `asyncio.Lock` y la hora de la siguiente salida (`ahora + min + uniform(0, jitter)`); devuelve los segundos esperados. 5 tests con reloj y espera falsos (la espera avanza el reloj): la 1.ª no espera, la 2.ª espera min + jitter, el tiempo ya transcurrido descuenta, 4 concurrentes salen a 0,5 s una tras otra, y `min = 0` lo desactiva. **Mutación:** sin el lock, falla solo el test de concurrencia. Restaurado.
- **RED:** con reloj, espera y `uniform` falsos: la 1.ª petición no espera; la 2.ª espera `min + jitter`; peticiones concurrentes salen en fila; `min = 0` → nunca espera.
- **RF:** RF-3

### [x] T4 — `TrafficLog`
> **Nota (2026-10-05):** RED real (`ImportError: TrafficLog`). `TrafficLog` en `app/services/outbound.py`: `record(kind)` al salir una petición (`search`, `resolution`, `session`), `record_status(status)` al llegar la respuesta (solo guarda los `4xx`), y `summary()` con los recuentos 1/5/15 min por tipo y los `4xx` de 15 min. `str(summary)` es un único campo de log (`1m[search=1 …] … 4xx_15m=1`). Las entradas de más de 15 min se descartan, así que la memoria no crece sin límite (un test lo comprueba con `len`). 5 tests con reloj falso. **Fin del PR 1.**
- **RED:** `record(kind)` y `record_status(...)`; `summary()` con recuentos 1/5/15 min por tipo y `4xx`; las entradas de más de 15 min se descartan.
- **RF:** RF-6. **Fin del PR 1.**

### [x] T5 — `OutboundGate` y cableado
> **Nota (2026-10-05):** RED real (`ImportError: OutboundGate`; después, 35 fallos por `gate=` en scraper y cliente de sesión). `OutboundGate` en `app/services/outbound.py`: `before_request(kind)` = espaciado → ventana larga → ventana corta (si la corta rechaza, libera el hueco de la larga) → registro; `after_response(...)` registra el estado y avisa (`WARNING` con código, endpoint y tipo) de los `4xx` salvo `404`/`429`. Scraper y cliente de sesión reciben `gate` en vez de `rate_limiter`; el tipo de cada paso de la cadena sale de su ruta (`resolution` para los pasos 1–5, `session` para la portada y los pasos 6–7, plan-D5). `AppResources.rate_limiter` pasa a `gate`; nueva clave `ratelimit:alcampo:long`. Dobles compartidos en `tests/services/outbound_doubles.py` (`gate_for`, `RecordingGate`). Test de punta a punta añadido: con la ventana larga llena, una búsqueda da `502` sin salir a Alcampo. **Mutación:** ignorar la ventana larga en la puerta → fallan el test de punta a punta y los de orden de la puerta. Restaurado. La regresión de `integration_env` prevista aquí ya se hizo en T1.
- **RED:** orden espaciado → larga → corta; rechazo de la corta libera la larga; la puerta registra tipo y estado; `401` → `WARNING` con código, endpoint y tipo; `404`/`429` sin `WARNING`. Scraper y cliente de sesión pasan por la puerta (tipo por endpoint, plan-D5).
- **Regresión (plan §4):** `integration_env` con espaciado y límites desactivados; helpers de scraper y sesión con una puerta desactivada.
- **RF:** RF-1, RF-3, RF-5

### [x] T6 — Desglose en el challenge
> **Nota (2026-10-05):** RED real (31 fallos: `ProductService` sin `traffic`). El servicio recibe el `TrafficLog` de la puerta y el `ERROR` del challenge añade `recent_traffic=1m[…] 5m[…] 15m[…] 4xx_15m=N` (también con el enfriamiento desactivado). Las aserciones existentes (`cooldown_s=180`, etc.) siguen valiendo: el texto solo se amplía. Test de punta a punta añadido: con la app real, un challenge en la búsqueda tras resolver la región registra `1m[search=1 resolution=5 session=4]`, lo que confirma también la clasificación por ruta de plan-D5.
- **RED:** tras tráfico simulado y un `400`, el `ERROR` del challenge incluye `recent_traffic` con los recuentos 1/5/15 min por tipo y los `4xx`.
- **RF:** RF-6

### [x] T7 — Docs y verificación manual
> **Nota (2026-10-05):** README: "Medidas antibaneo" con las dos ventanas (y por qué el límite corto bajó a 10), el espaciado y el aviso de `4xx`; tabla de variables (4 nuevas y el 10); limitación reescrita (los límites no están demostrados y cada challenge registra `recent_traffic`); el `ERROR` del challenge en la tabla de niveles; se quita la limitación "una resolución sale de golpe", ya resuelta.
>
> **Verificación manual (2026-10-05T08:18Z):** `docker compose` con los valores por defecto (10/60 s, 30/900 s, 500 ms + 500 ms), token sintético, 4 días después del bloqueo del 01-10. **1 búsqueda real** (`28001` + `agua`, 1 resolución con 1 destino) → `200` en **7,6 s** (por debajo de los 15 s del tiempo máximo). Las 10 peticiones a Alcampo salieron separadas **0,60–1,04 s** (antes, 1–3 s en total). Ningún `WARNING`, `ERROR` ni challenge; 0 apariciones del token. `docker compose down`; scratchpad limpio. **Fin del PR 2.**
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
