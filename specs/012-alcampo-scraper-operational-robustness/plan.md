# Plan 012 — Robustez operativa

- **Estado:** aprobado (2026-10-05)
- **Fecha:** 2026-10-05
- **Spec:** [spec.md](spec.md) (aprobada; decisiones citadas como **spec-D1…spec-D4**; las de este plan, **D1…**)
- **Entrega:** 1 PR (~200 líneas)

## 1. Cambios

| Fichero | Cambio | RF |
|---|---|---|
| `app/main.py` | `GET /ready` junto a `/health`: `PING` por `resources(app).redis_circuit` | RF-1…RF-3 |
| `app/core/config.py` | `http_timeout_seconds: float = Field(default=10, gt=0)` | RF-4 |
| `app/scrapers/http_client.py` | `timeout=settings.http_timeout_seconds`; fuera `REQUEST_TIMEOUT_SECONDS` | RF-4 |
| `app/core/logging.py` | `PageTokenRedactor` (filtro) sobre el logger `httpx`, instalado en `configure_logging` | RF-7 |
| `tests/…` | `/ready`, ajustes, cliente HTTP, rama del middleware, filtro | RF-1…RF-7 |
| `README.md` | `/ready`, `HTTP_TIMEOUT_SECONDS`, tokens redactados | — |

## 2. Decisiones de diseño

**D1 — `/ready` reutiliza lo que ya hay.** El cliente de Redis ya tiene `REDIS_TIMEOUT_SECONDS` (spec 007) y el circuito ya decide si intentarlo (spec-D2): `await circuit.call(redis.ping)`. Cualquier `RedisError` (incluido `RedisCircuitOpenError`) → `503`, registrado con el `redis_unavailable` de siempre (`WARNING` en un fallo real, `DEBUG` con el circuito abierto), así que un orquestador que sondee cada pocos segundos no inunda el log. Sin `X-API-Key`, como `/health`. Sin dependencias de FastAPI: una función en `create_app`.

**D2 — El filtro va en el logger `httpx`, no en el handler.** Un filtro de logger se aplica **antes** que cualquier handler (también el de `caplog` de los tests y cualquier otro que se añada), así que ningún destino ve el token. Reescribe `record.msg` con `record.getMessage()` redactado y vacía `record.args`. Regex `(pageToken=)[^&\s"]+` → `\1<redacted>`. Idempotente: `configure_logging` se llama una vez por arranque, pero no añade el filtro dos veces.

**D3 — `HTTP_TIMEOUT_SECONDS` por defecto 10** (spec-D3): la constante se sustituye por el ajuste con el mismo valor; sin cambio de comportamiento.

**D4 — Test de la rama del middleware con una ruta de streaming** que envía el primer trozo y luego lanza: `TestClient` con `raise_server_exceptions=True` debe ver la excepción (relanzada), el `ERROR` lleva traceback y request id, y no hay un `500` añadido tras el `200` ya empezado.

## 3. Regresiones previstas

| Test | Por qué | Corrección |
|---|---|---|
| Tests del cliente HTTP que usen `REQUEST_TIMEOUT_SECONDS` | la constante desaparece | leer el valor de `Settings` |

## 4. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Un orquestador saca todas las instancias con Redis caído (spec-D1) | Documentado: para vida, `/health` |
| R2 | httpx cambia el formato de su línea de log | El test del filtro usa una petición real de httpx (respx), no un mensaje inventado |
