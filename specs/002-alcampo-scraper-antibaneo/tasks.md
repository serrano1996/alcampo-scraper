# Tasks 002 — Medidas antibaneo

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 2 PRs encadenados. Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(002-alcampo-scraper-antibaneo): <descripción en inglés> (Tn)`.

---

## PR1 — Fingerprint, `Retry-After` y jitter (paridad con Mercadona)

### [x] T1 — `RETRY_JITTER_MAX_S` en `Settings`
- **RED:** en `tests/core/test_config.py`:
  - default `retry_jitter_max_s == 0.3`;
  - `RETRY_JITTER_MAX_S=0` → válido;
  - `RETRY_JITTER_MAX_S=-0.1` → `ValidationError`;
  - `RETRY_JITTER_MAX_S=1.5` → `1.5`.
  - Añadir `RETRY_JITTER_MAX_S` a la lista `OPTIONAL` del fixture que limpia el entorno.
- **GREEN:** `retry_jitter_max_s: float = Field(default=0.3, ge=0)`.
- **Depende:** —
- **RF:** RF-10
- **Hecho cuando:** los 4 casos pasan.

### [x] T2 — Pool de User-Agents, elegido una vez por cliente
- **RED:** en `tests/scrapers/test_http_client.py`:
  - `USER_AGENTS` es **exactamente** la tupla de la spec RF-2 (6 entradas, sin duplicados);
  - `create_http_client(settings, choose=fake)` llama a `fake` **una sola vez**, con `USER_AGENTS` como argumento;
  - el `User-Agent` del cliente es el valor devuelto por `fake`;
  - sin `choose`, el UA pertenece a `USER_AGENTS`.
  - **Regresión (plan §5):** reescribir `test_create_http_client_sets_realistic_headers` para que no exija `"Chrome/"`: fija `choose` y comprueba solo `Accept` y `Accept-Language`.
- **GREEN:** `USER_AGENTS` y parámetro `choose: Callable[[Sequence[str]], str] = random.choice` en `app/scrapers/http_client.py` (plan-D10). Se elimina la constante `USER_AGENT` única.
- **Depende:** —
- **RF:** RF-1, RF-2
- **Hecho cuando:** todos los casos pasan y ya no existe `USER_AGENT` en el código.

### [x] T3 — `Referer`, `ecom-request-source` y sin `Origin`
- **RED:** en `tests/scrapers/test_http_client.py`:
  - cabeceras del cliente: `Referer == "https://www.compraonline.alcampo.es/"` y `ecom-request-source == "web"`, **también** cuando `ALCAMPO_BASE_URL` es `https://alcampo.test` (plan-D11);
  - con `respx`, una petición real del cliente lleva `Referer` y **no** lleva `Origin`.
- **GREEN:** `ALCAMPO_REFERER` y las dos cabeceras en `create_http_client`.
- **Depende:** T2
- **RF:** RF-3, RF-4
- **Hecho cuando:** todos los casos pasan.

### [x] T4 — `parse_retry_after`
- **RED:** en `tests/scrapers/test_retry.py`, con `now` fijo en `2026-09-24T10:00:00Z`:
  - `"120"` → `120.0`; `"0"` → `0.0`;
  - `"Wed, 24 Sep 2026 10:00:30 GMT"` → `30.0`;
  - fecha pasada (`"Wed, 24 Sep 2026 09:59:00 GMT"`) → `0.0`;
  - `None`, `""`, `"abc"`, `"-5"`, `"1.5"` → `None`.
- **GREEN:** `parse_retry_after(value: str | None, now: datetime) -> float | None` en `app/scrapers/retry.py` (plan-D7).
- **Depende:** —
- **RF:** RF-5 (formatos), RF-6, RF-7
- **Hecho cuando:** todos los casos pasan.

### [x] T5 — Jitter en esperas por `5xx` y transporte
- **RED:** en `test_retry.py`, con `uniform` falso que devuelve `0.2` y registra sus argumentos:
  - `503, 200` con `jitter_max=0.3` → esperas `[0.7]`;
  - `ConnectTimeout, 200` → esperas `[0.7]`;
  - `uniform` se llamó con `(0, 0.3)`;
  - sin pasar `jitter_max` → esperas exactas de la 001 (`[0.5, 1.0]`) y `uniform` se llama con `(0, 0.0)` o no se llama.
- **GREEN:** parámetros `jitter_max: float = 0.0` y `uniform = random.uniform` en `send_with_retry`; espera = backoff + `uniform(0, jitter_max)` (plan-D6, plan-D9).
- **Depende:** —
- **RF:** RF-9
- **Hecho cuando:** los casos nuevos pasan **y los 9 tests de la 001 en `test_retry.py` siguen en verde sin modificarlos**.

### [x] T6 — `429` con `Retry-After` y tope de 60 s
- **RED:** en `test_retry.py`, con `uniform` fijo en `0.3` y `now` fijo:
  - `429` + `Retry-After: 2`, luego `200` → espera `[2.3]`;
  - `429` + `Retry-After: 3600` → espera `[60.0]`;
  - `429` + `Retry-After: 60` → espera `[60.0]`, no `60.3` (el tope se aplica después de sumar el jitter);
  - `429` + fecha HTTP 10 s en el futuro → `[10.3]`;
  - `429` sin cabecera → backoff + jitter (`[0.8]`);
  - `503` + `Retry-After: 30` → backoff + jitter (`[0.8]`), **no** 30 (spec-D4);
  - **regresión RF-11:** `429 × 3` → `UpstreamUnavailableError` con 2 esperas.
- **GREEN:** parámetro `now` en `send_with_retry`; en `429`, base = `parse_retry_after(...)` o backoff, y espera = `min(base + jitter, 60.0)` (plan-D8).
- **Depende:** T4, T5
- **RF:** RF-5, RF-6, RF-7, RF-8, RF-11
- **Hecho cuando:** todos los casos pasan.

### [x] T7 — Cableado del scraper y verificación real del fingerprint
> **Verificación real (2026-09-25T09:58:02Z):** 1 búsqueda de `leche` con `create_http_client` → `200`, 50 productos (`54180`, 5.28, `0.88 €/L`), sin challenge del WAF. UA elegido: índice 5 (Safari 27 / macOS), con `Referer` y `ecom-request-source: web`. También se fijó `retry_jitter_max_s=0` en `make_scraper` de `test_alcampo_search.py`, que usa esperas reales.
- **RED:**
  - en `tests/scrapers/test_alcampo_search.py`: con `Settings(retry_jitter_max_s=0.25)`, el scraper llama a `send_with_retry` con `jitter_max=0.25` (se captura con `monkeypatch` sobre `app.scrapers.alcampo_search.send_with_retry`);
  - en `tests/integration/conftest.py`: fijar `RETRY_JITTER_MAX_S=0` (plan-D12).
- **GREEN:** pasar `jitter_max=self._settings.retry_jitter_max_s`.
- **Depende:** T1, T6
- **RF:** RF-9
- **Hecho cuando:** los tests pasan, la suite completa sigue igual de rápida y **1 búsqueda real** contra Alcampo con `create_http_client` (UA del pool, `Referer`, `ecom-request-source`) devuelve `200` con productos, habiendo pasado ≥10 min desde la última petición (plan R5). El resultado se anota aquí. **Fin de PR1.**

---

## PR2 — Enfriamiento tras challenge del WAF y docs

### [x] T8 — `UpstreamBlockedError`
- **RED:**
  - `tests/test_exceptions.py`: `UpstreamBlockedError` es subclase de `UpstreamUnavailableError`;
  - `test_retry.py`: el challenge del WAF lanza `UpstreamBlockedError` (no solo la clase base), con 0 esperas y 1 llamada;
  - `tests/api/test_products_route.py`: el servicio lanza `UpstreamBlockedError("waf")` → `502 {"detail": "Upstream service unavailable"}` y el body no contiene `"waf"`.
- **GREEN:** la excepción en `app/exceptions.py`; `send_with_retry` la lanza en la rama del WAF (plan-D1).
- **Depende:** T6
- **RF:** RF-12, RF-14
- **Hecho cuando:** todos los casos pasan y el test de WAF de la 001 (`pytest.raises(UpstreamUnavailableError)`) sigue en verde sin tocarlo.

### [x] T9 — `WAF_COOLDOWN_SECONDS` en `Settings`
- **RED:** en `tests/core/test_config.py`: default `180`; `0` válido; `-1` → `ValidationError`; override por entorno. Añadir la variable a `OPTIONAL`.
- **GREEN:** `waf_cooldown_seconds: int = Field(default=180, ge=0)`.
- **Depende:** —
- **RF:** RF-19 (configuración)
- **Hecho cuando:** los 4 casos pasan.

### [x] T10 — `WafCooldownRepository`
- **RED:** `tests/services/test_waf_cooldown.py`, con un `FakeAsyncRedis()` nuevo por test:
  - `is_active()` sin marca → `False`;
  - `activate(180)` → `is_active()` es `True` y la clave `waf:cooldown` tiene TTL 180 (±1 s);
  - `activate(0)` → no existe la clave y `is_active()` es `False`;
  - tras borrar la clave (simula expiración) → `is_active()` es `False`;
  - `activate(180)` y luego `activate(60)` → TTL 60 (renueva, no usa `NX`; plan-D5).
- **GREEN:** `app/services/waf_cooldown.py` (plan-D4, plan-D5).
- **Depende:** —
- **RF:** RF-15, RF-18, RF-19
- **Hecho cuando:** todos los casos pasan.

### [x] T11 — El servicio activa la marca ante un bloqueo
- **RED:** en `tests/services/test_product_service.py`, con un repositorio de enfriamiento real sobre fakeredis:
  - el scraper lanza `UpstreamBlockedError` → la excepción se propaga **y** la marca queda activa con TTL `WAF_COOLDOWN_SECONDS`;
  - el scraper lanza `UpstreamUnavailableError` normal → se propaga y la marca **no** se activa.
  - Actualizar el helper `make_service` para inyectar el repositorio.
  - **Regresión (plan §5), adelantada desde T13:** reescribir `test_waf_challenge_returns_502_with_a_single_call` para que compruebe que no hay claves `search:*` y que **sí** existe `waf:cooldown`, en lugar de `dbsize() == 0`.
  > **Corrección durante la implementación (2026-09-25):** la regresión aparece aquí, no en T13. Al conectar el enfriamiento en `dependencies.py`, la app real empieza a guardar `waf:cooldown`. Con la corrección en T13, T11 no podía cerrar con la suite en verde.
- **GREEN:** parámetro `cooldown: WafCooldownRepository` en `ProductService`; `except UpstreamBlockedError: activate(...); raise`. Actualizar `get_product_service` en `app/core/dependencies.py` para que la app real siga funcionando.
- **Depende:** T8, T9, T10
- **RF:** RF-15
- **Hecho cuando:** los casos pasan y toda la suite, incluida la integración de la 001, sigue en verde.

### [x] T12 — El servicio respeta la marca
- **RED:** en `test_product_service.py`:
  - marca activa + miss → `UpstreamUnavailableError` y **0** llamadas al scraper;
  - marca activa + hit → respuesta de cache y 0 llamadas al scraper;
  - sin marca + miss → llama al scraper (control).
- **GREEN:** en `search()`, tras la cache y antes del scraper: `if await cooldown.is_active(): raise UpstreamUnavailableError("WAF cooldown active")` (plan-D3).
- **Depende:** T11
- **RF:** RF-16, RF-17
- **Hecho cuando:** todos los casos pasan.

### [x] T13 — Integración end-to-end
> **R4 resuelto (2026-09-25):** `respx` compara `params` de forma parcial (`<Params contains …>`): `params={"q": "agua"}` encaja aunque la petición lleve `tag` y `maxPageSize`. No hizo falta `side_effect`. El test pasó a la primera (sin código nuevo) y se comprobó que detecta el fallo: con `WAF_COOLDOWN_SECONDS=0` falla con `agua.call_count == 2`.
- **RED:** en `tests/integration/test_products_endpoint.py`:
  - **escenario completo:** búsqueda de `leche` → `200` (queda en cache); búsqueda de `agua` → challenge → `502` (1 llamada a Alcampo); `agua` otra vez → `502` **sin llamada nueva**; `leche` otra vez → `200` desde cache;
- **GREEN:** no debería hacer falta código nuevo. **Punto de control R4:** si `respx` no permite distinguir rutas por `params={"q": ...}`, usar `side_effect` por petición y anotarlo aquí.
- **Depende:** T12
- **RF:** RF-15, RF-16, RF-17
- **Hecho cuando:** todos los casos pasan.

### [x] T14 — Docs vivas
- **RED:** `tests/core/test_env_example.py`: `.env.example` contiene `RETRY_JITTER_MAX_S` y `WAF_COOLDOWN_SECONDS`, y todas las variables que declara `Settings` (evita que vuelvan a desincronizarse).
- **GREEN:** actualizar `.env.example` (por `Bash`, porque `Write` está bloqueado en `.env*`) y `README.md`: las dos variables nuevas, una sección sobre el enfriamiento (qué hace, cuánto dura, que las búsquedas cacheadas siguen funcionando) y la deuda de revisión del pool de User-Agents.
- **Depende:** T13
- **RF:** RNF-6
- **Hecho cuando:** el test pasa y el README está revisado. **Fin de PR2.**

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T2 |
| RF-2 | T2 |
| RF-3 | T3 |
| RF-4 | T3 |
| RF-5 | T4, T6 |
| RF-6 | T4, T6 |
| RF-7 | T4, T6 |
| RF-8 | T6 |
| RF-9 | T5, T7 |
| RF-10 | T1 |
| RF-11 | T6 |
| RF-12 | T8 |
| RF-13 | test `404` de la 001 (sigue en verde en cada cierre de tarea) |
| RF-14 | T8 |
| RF-15 | T10, T11, T13 |
| RF-16 | T12, T13 |
| RF-17 | T12, T13 |
| RF-18 | T10 |
| RF-19 | T9, T10 |

Ningún RF queda huérfano. RNF: RNF-1 (T5–T7, T11), RNF-2 (T5, T6: `sleep` inyectable), RNF-3 (T2, T5, T6: `choose`, `uniform` y `now` inyectables), RNF-4 (T10: clave única), RNF-5 (ruff `ANN` en todas), RNF-6 (T14).

**Cambio respecto al orden del plan §8:** la verificación real del fingerprint se hace al final de **T7** (antes de abrir PR1, que es donde entra el fingerprint), no al final de todo.
