# Tasks 015 — Fallos encontrados por las revisiones de dia-scraper

- **Estado:** aprobado (2026-10-09)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**: es la reproducción del caso de la spec §1.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(015-alcampo-scraper-sibling-review-fixes): <descripción en inglés> (Tn)`.

---

### [x] T1 — Errores de httpx que no son de transporte (F1)
- **RED:** `test_retry.py`: `DecodingError` y `TooManyRedirects` se reintentan y, agotados, dan `UpstreamUnavailableError` (plan-D1).
- **GREEN:** `retry.py`.
- **RF:** RF-1

### [x] T2 — `Retry-After` desbordado (F2)
- **RED:** `test_retry.py`: la fecha con año `99999999999999999999` y un valor infinito → `None`; un `429` con esa cabecera espera el backoff (plan-D2).
- **GREEN:** `parse_retry_after`.
- **RF:** RF-2

### [x] T3 — `API_KEYS` fuera de los volcados (F3)
- **RED:** `test_config.py`: el token no está en `model_dump()` ni en `model_dump_json()`.
- **GREEN:** `exclude=True`.
- **RF:** RF-3

### [x] T4 — Redacción y parámetros del log (F4, F5)
- **RED:** `test_request_context.py`: los nombres de la spec §5 se redactan y los de la API no; los valores repetidos aparecen todos; la query se corta a 500 caracteres (plan-D3).
- **GREEN:** `request_context.py`.
- **Nota:** la línea `request started` pasa de `params={'term': 'leche'}` a `params=[('term', 'leche')]` (una lista de pares, para que los parámetros repetidos no se pierdan). Se han actualizado las dos aserciones de integración que dependían del formato anterior (`test_auth.py`, `test_logging_integration.py`).
- **RF:** RF-4, RF-5

### [x] T5 — El `500` sin el mensaje (F10)
- **RED:** `test_request_context.py`: un error no controlado con un marcador en el mensaje: el log tiene el tipo y los frames, no el marcador ni `exc_info` (plan-D4).
- **GREEN:** `request_context.py`.
- **RF:** RF-10

### [x] T6 — `setuptools` con hash (F6)
- **RED:** `tests/infra`: el lock de build existe y fija con hash `[build-system].requires`; el `Dockerfile` y la CI lo instalan con `--require-hashes` y construyen con `--no-build-isolation`; `.dockerignore` lo readmite; `lock.sh` rechaza opciones desconocidas (plan-D5).
- **GREEN:** `lock.sh` y ejecutarlo (Docker, PyPI), `Dockerfile`, `ci.yml`, `.dockerignore`. Comprobar `docker build` con el daemon.
- **Incidente en el RED:**
  - La primera versión del test ejecutaba el `lock.sh` vulnerable con `--upgrade; touch /src/pwned`. Llegó a Docker y lanzó `pip-compile --upgrade`, que dejó un `requirements.txt` sin seguimiento en la raíz. Lo borré; los locks no cambiaron y el `touch` no llegó a crear nada.
  - Eso **confirma la inyección** del `lock.sh` anterior.
  - El test definitivo usa un payload inofensivo (`--upgrade; false`) y un `PATH` vacío: un script que no rechace la opción no llega a Docker y falla con 127. Por eso el `case` va antes de cualquier comando externo, también antes de `dirname`.
- **Comprobado:**
  - `scripts/lock.sh` genera `requirements-build.lock` (`setuptools 84.0.0` con hashes) y no cambia los otros dos locks.
  - `docker build` funciona: instala `setuptools` del lock y construye `alcampo-scraper 0.1.0` sin aislamiento.
  - README actualizado.
- **RF:** RF-6

### [x] T7 — Circuito: una sola prueba (F7)
- **RED:** `test_redis_circuit.py`: 20 llamadas concurrentes → 1 prueba; mientras prueba, las demás lo ven abierto; un error ajeno deja probar a la siguiente (plan-D6).
- **GREEN:** `redis_circuit.py`.
- **RF:** RF-7

### [x] T8 — El rechazo del limitador se mantiene (F8)
- **RED:** `test_rate_limiter.py`: con el `ZREM` fallando, la segunda `acquire` se rechaza; un rechazo durante una prueba cierra el circuito (plan-D7).
- **GREEN:** `rate_limiter.py`.
- **RF:** RF-8

### [ ] T9 — El enfriamiento del WAF sobrevive a Redis (F9)
- **RED:** `test_waf_cooldown.py`: uno iniciado con Redis sano sigue activo con Redis caído; con varios desafíos y Redis sano, la duración local sigue a la de Redis (plan-D8).
- **GREEN:** `waf_cooldown.py`.
- **RF:** RF-9

### [ ] T10 — Docs
- **Hacer:** README: la redacción por subcadena, los tres locks y `lock.sh --upgrade`, y en "sin Redis" que solo una petición por periodo prueba Redis y que el enfriamiento iniciado se respeta aunque Redis caiga.
- **RF:** criterios de finalización
