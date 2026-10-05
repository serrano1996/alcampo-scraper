# Tasks 012 — Robustez operativa

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 1 PR: T1–T3.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`. Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.

---

### [x] T1 — `/ready`
> **Nota (2026-10-05):** RED real (`404` en los 3 tests de `tests/integration/test_ready.py`). GREEN: `GET /ready` en `create_app`, `PING` por `resources(app).redis_circuit` (plan-D1); un `RedisError` (también `RedisCircuitOpenError`) → `redis_unavailable(…, "ready.ping", …)` y `503 {"status": "unavailable", "redis": "unreachable"}`; si no, `200 {"status": "ready"}`. Comprobado: sin `X-API-Key`, sin rutas de respx (no toca Alcampo), el cuerpo del `503` sin la URL ni el texto del error, `/health` `200` con Redis caído, y con el circuito abierto el segundo `/ready` no intenta Redis ni añade `WARNING`. Sin regresiones.
- **RED** (`tests/integration/test_ready.py`, app real + fakeredis):
  - Redis bien → `200 {"status": "ready"}`, sin `X-API-Key`.
  - Redis caído (doble que falla) → `503 {"status": "unavailable", "redis": "unreachable"}`; el cuerpo no contiene la URL de Redis ni el mensaje del error; un `WARNING` `redis unavailable`.
  - Circuito abierto → `503` sin intentar el `PING` (contador de intentos a 0) y sin `WARNING` nuevo (plan-D1).
  - `/ready` no toca Alcampo (respx sin rutas) y `/health` sigue igual con Redis caído (RF-3).
- **GREEN:** la ruta en `create_app` (plan-D1).
- **RF:** RF-1, RF-2, RF-3

### [ ] T2 — `HTTP_TIMEOUT_SECONDS` y rama del middleware
- **RED:**
  - `Settings`: por defecto 10; `0` y negativo → error al arrancar.
  - `create_http_client(settings)` con `http_timeout_seconds=3` → `client.timeout` con 3 en conexión, lectura, escritura y pool.
  - Middleware (sin RED posible: comportamiento actual, RF-6; mutación anotada): ruta de streaming que lanza tras el primer trozo → la excepción llega al cliente de test, `ERROR` con traceback y request id, ningún `500` (plan-D4). Mutación: quitar el `raise` → el test falla.
- **GREEN:** ajuste y cliente (plan-D3).
- **Regresión:** plan §3, si aparece.
- **RF:** RF-4, RF-5, RF-6

### [ ] T3 — Tokens fuera de los logs, docs y verificación manual
- **RED:** con `configure_logging` y una petición real de httpx a una URL con `pageToken=tok-secreto` (respx): la línea `HTTP Request` contiene `pageToken=<redacted>`, no `tok-secreto`, y conserva método, ruta, `q` y estado; dos llamadas a `configure_logging` → un solo filtro (plan-D2).
- **GREEN:** `PageTokenRedactor` en `app/core/logging.py`.
- README: `/ready` (contrato, circuito, aviso de spec-D1), `HTTP_TIMEOUT_SECONDS` en la tabla de variables, token redactado. `.env.example`: pedir al usuario que añada `HTTP_TIMEOUT_SECONDS=10`.
- **Verificación manual** (`docker compose`, **sin tocar Alcampo**): `/ready` → `200`; `docker compose stop redis` → `/ready` → `503`, `/health` → `200`; `docker compose start redis` → `/ready` → `200` tras el periodo del circuito.
- **RF:** RF-7; criterios de finalización
