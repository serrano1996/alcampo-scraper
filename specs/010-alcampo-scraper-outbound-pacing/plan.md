# Plan 010 — Ritmo de salida hacia Alcampo

- **Estado:** aprobado (2026-10-01). Entrega: 2 PRs (§7)
- **Fecha:** 2026-10-01
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D4**; las de este plan son **D1…**

## 1. Visión general

Hoy cada petición a Alcampo pasa por un solo punto antes de salir: `rate_limiter.acquire()` en `AlcampoSearchScraper.send` y en `AlcampoSessionClient._send`. Ese punto se convierte en una **puerta de salida** (`OutboundGate`) que hace todo lo de esta spec:

```
send()  (búsqueda, pasos 0–7 de la cadena)
  └─ await gate.before_request(kind)
       ├─ pacer.wait_turn()            espaciado ≥ 500 ms + jitter, por proceso        RF-3
       ├─ ventana larga  .acquire()    30 / 900 s, Redis + respaldo local              RF-1
       ├─ ventana corta  .acquire()    10 / 60 s (si falla, libera el hueco de la larga) RF-1, RF-2
       └─ traffic.record(kind)                                                          RF-6
  └─ respuesta → gate.after_response(kind, status, endpoint)
       ├─ traffic.record_status(...)                                                    RF-6
       └─ 4xx ≠ 404/429 → WARNING código + endpoint + tipo                              RF-5

Challenge → ProductService._on_waf_block → ERROR "... recent_traffic=<desglose 1/5/15 min>"   RF-6
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `alcampo_rate_limit` por defecto 10; `alcampo_rate_limit_long` (30), `alcampo_rate_window_long_seconds` (900), `alcampo_min_interval_ms` (500), `alcampo_interval_jitter_ms` (500) | RF-1…RF-3 |
| `app/services/rate_limiter.py` | `acquire()` devuelve un id; `release(id)` deshace un hueco (también en el respaldo local) | RF-1 |
| `app/services/outbound.py` | **nuevo**: `OutboundPacer`, `TrafficLog`, `OutboundGate` | RF-1, RF-3, RF-5, RF-6 |
| `app/scrapers/alcampo_search.py`, `alcampo_session.py` | reciben la puerta en vez del limitador; informan del estado de cada respuesta | RF-5, RF-6 |
| `app/services/product_service.py` | el `ERROR` del challenge incluye el desglose | RF-6 |
| `app/main.py`, `app/core/state.py` | la puerta se crea en el `lifespan` y sustituye a `rate_limiter` en `AppResources` | — |
| `tests/integration/conftest.py` | `integration_env` fija `ALCAMPO_MIN_INTERVAL_MS=0` y límites holgados | §5 |
| `README.md`, `.env.example` | variables y comportamiento | RNF-4 |

## 3. Decisiones de diseño

**D1 — Una sola puerta de salida.** Pacer, ventanas y registro de tráfico detrás de `OutboundGate.before_request(kind)` / `after_response(...)`. El scraper y el cliente de sesión solo conocen la puerta. Evita repetir en dos sitios el orden "esperar turno → limitar → registrar", que es donde se cometen errores.

**D2 — Orden: espaciado, ventana larga, ventana corta.** Primero se espera el turno (no consume cupo). Luego la ventana larga y después la corta; si la corta rechaza, se **libera** el hueco recién tomado en la larga (`release`), para que un rechazo no consuma cupo en ninguna (spec 008 RF-3).
- *Descartada:* una transacción Redis sobre las dos claves. Más atómica, pero exige un script Lua (que `fakeredis` no ejecuta sin una dependencia nueva) para algo que solo afecta a un caso límite.

**D3 — `OutboundPacer` por proceso:** un `asyncio.Lock` y la hora a partir de la cual sale la siguiente petición. `wait_turn()` espera lo que falte y fija la siguiente en `ahora + min + uniform(0, jitter)`. Reloj, espera y aleatoriedad inyectables. El tiempo esperado cuenta dentro del tiempo máximo de la operación (spec 008), así que nunca puede colgar una petición.

**D4 — `TrafficLog` en memoria por proceso:** un `deque` de `(instante, tipo, estado)` de los últimos 15 min. `summary()` devuelve recuentos en 1, 5 y 15 min por tipo y el número de `4xx`. Por proceso, no en Redis: el desglose describe **lo que envió esta instancia**, que es lo que se puede corregir.

**D5 — Tipos de petición por endpoint:** `search` (búsqueda), `resolution` (pasos 1–5 de la cadena) y `session` (portada, `proposition` y `active`). *Concreción de la spec:* "renovación de sesión" pasa a `session` e incluye también las confirmaciones de regiones nuevas, porque usan los mismos pasos; separarlas exigiría pasar el motivo por toda la cadena sin aportar nada para afinar el límite.

**D6 — RF-4 (no enviar peticiones inválidas):** hoy la app no reutiliza tokens de página entre sesiones porque aún no pagina. Esta spec lo deja como regla, y la 009 lo cumple guardando los tokens en la sesión de cada región. Aquí solo hay un test que fija que la puerta registra cualquier `400`/`401` para detectarlo si ocurre.

## 4. Regresiones previstas

| Tests | Por qué | Corrección |
|---|---|---|
| Integración (specs 001–008) | el límite corto baja a 10 y una primera búsqueda ya son ~10 peticiones; el espaciado añadiría 0,5–1 s por petición | `integration_env` fija `ALCAMPO_MIN_INTERVAL_MS=0`, `ALCAMPO_RATE_LIMIT=0` y `ALCAMPO_RATE_LIMIT_LONG=0` (desactivados); los tests de límite ya fijan los suyos |
| `test_alcampo_search.py`, `test_alcampo_session.py` | reciben la puerta | una puerta desactivada en el helper |
| `test_env_example.py` | 4 variables nuevas | el **usuario** las añade a `.env.example` |

## 5. Estrategia de test

| RF | Test |
|---|---|
| RF-1 | ventana larga agotada con la corta libre → `OutboundRateLimitedError`; rechazo de la corta → el hueco de la larga se libera; ambas con Redis caído usan su respaldo |
| RF-2 | valores por defecto en `Settings` |
| RF-3 | con reloj y espera falsos: 3 peticiones seguidas esperan ~500–1000 ms entre sí; `0` desactiva; espera no consume cupo |
| RF-5 | `401` de Alcampo → `WARNING` con código, endpoint y tipo; `404` y `429` no |
| RF-6 | tras 3 búsquedas, 2 pasos de cadena y un `400`, un challenge registra el desglose 1/5/15 min por tipo y los `4xx` |

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Los límites siguen sin estar demostrados | Desglose en cada challenge (RF-6); configurables |
| R2 | El espaciado alarga una búsqueda de un CP nuevo (~7 s) | Por debajo del tiempo máximo (15 s); solo en la primera búsqueda de cada CP |
| R3 | La verificación manual exige tráfico real tras el bloqueo del 01-10 | Hacerla otro día o tras ≥ 1 h, con 1 resolución como mucho |

## 7. Secuencia y entrega

| # | Tarea |
|---|---|
| 1 | `Settings` (+ `.env.example` a mano) |
| 2 | `acquire` → id y `release` en el limitador |
| 3 | `OutboundPacer` |
| 4 | `TrafficLog` |
| 5 | `OutboundGate` y cableado (scraper, cadena, `lifespan`), con el `WARNING` de `4xx` |
| 6 | Desglose en el `ERROR` del challenge |
| 7 | Docs y verificación manual |

~450 líneas: **2 PRs** (T1–T4 piezas; T5–T7 cableado).
