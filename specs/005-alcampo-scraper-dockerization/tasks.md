# Tasks 005 — Dockerización

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** un solo PR (~270 líneas).

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio (**suite completa**). Marcar `[x]`, proponer el commit y **parar**.
5. **Tareas sin RED posible:** se valida rompiendo a propósito el comportamiento y comprobando que el test falla. Se anota aquí.
6. **Tareas de verificación manual (T5, T6):** necesitan el daemon de Docker arrancado; **no valen sustitutos** (fakeredis, `uvicorn` local). Si Docker no está disponible, se para y se avisa. Se usan solo tokens sintéticos generados para la prueba; el `.env` real no se lee ni se imprime. El resultado se anota aquí y se deja todo parado (`docker compose down`, sin contenedores huérfanos).
7. **Si un supuesto del plan falla** (plan §2, pendientes), se para y se avisa con evidencia, sin parchear.

Formato de commit: `<tipo>(005-alcampo-scraper-dockerization): <descripción en inglés> (Tn)`.

---

### [x] T1 — `/health` no toca Redis ni Alcampo
> **Verificación por mutación (2026-09-28):** con `/health` haciendo `await request.app.state.redis.ping()` → el test falla (`ConnectionError: redis is down (ping)`, `500`); con `/health` llamando a `request.app.state.http_client.get("/")` → falla (`AllMockedAssertionError`, `500`). Restaurado con `git checkout`: `app/` sin cambios. El Redis caído es un `DownRedis` propio (solo `aclose()` funciona, para que el `lifespan` cierre limpio). El `StarletteDeprecationWarning` del test ya aparecía en toda la suite.
- **RED:** ninguno real: `/health` ya cumple (plan §2). Test nuevo en `tests/integration/test_health.py`, con la app real (`lifespan` + fakeredis + respx) y **sin** `X-API-Key`:
  - `app.state.redis` sustituido por un objeto cuyo cualquier método lanza una excepción (Redis caído);
  - ninguna ruta respx registrada (cualquier salida a Alcampo fallaría);
  - `GET /health` → `200` y `{"status": "ok"}`.
- **Verificación por mutación:** hacer que `/health` llame a `await request.app.state.redis.ping()` → el test debe fallar; restaurar y anotarlo aquí.
- **GREEN:** — (sin cambios en `app/`).
- **Depende:** —
- **RF:** RF-12
- **Hecho cuando:** el test pasa, la mutación queda anotada y `app/` sigue sin cambios.

### [x] T2 — `.dockerignore`
> **Nota (2026-09-28):** RED real (los 3 tests fallaban con `FileNotFoundError`). Los tests comprueban el **texto** de las reglas, no cómo las interpreta Docker: que `*` + `!app/` deje pasar el contenido de `app/` y nada más se confirma con el build real de T5 (paso 2).
- **RED:** `tests/infra/test_dockerignore.py` (sin `__init__.py`, como el resto de carpetas de `tests/`; los nombres de fichero no chocan con ninguno existente). Lee `.dockerignore`, ignora comentarios y líneas vacías, y comprueba:
  - la primera regla es `*` (lista de permitidos, plan-D5);
  - las readmisiones (`!…`) son exactamente `{pyproject.toml, app/}`;
  - después de readmitir `app/` se vuelven a excluir `**/__pycache__/` y `**/*.py[cod]`.
  - Falla porque el fichero no existe.
- **GREEN:** `.dockerignore` con esas reglas y un comentario que explique por qué es una lista de permitidos (RNF-3).
- **Depende:** —
- **RF:** RF-5, RNF-3
- **Hecho cuando:** los casos pasan.

### [x] T3 — `Dockerfile`
> **Nota (2026-09-28):** RED real (6 tests con `FileNotFoundError`). En GREEN, la comprobación de `[dev]` sobre el texto completo saltó por un **comentario** del `Dockerfile`; se corrigió el test para que mire solo las instrucciones `RUN` con `pip install` (un comentario no instala nada). La de secretos sigue mirando el texto completo, comentarios incluidos. **Mutaciones:** `.[dev]` en el install, `USER root`, `--reload` en el `CMD`, sonda contra `localhost` y runtime en `python:3.13-slim` → cada una hace fallar su test; restaurado. El build real queda para T5.
- **RED:** `tests/infra/test_dockerfile.py`. Lee el `Dockerfile` (uniendo las líneas continuadas con `\`) y comprueba:
  - hay exactamente dos `FROM`, ambos `python:3.11-slim`, el primero `AS builder` (spec-D1, plan-D1);
  - la instalación es `pip install … .` sin `[dev]` (RF-2);
  - hay un `USER` después del último `FROM`, distinto de `root` y de `0` (RF-3, plan-D2);
  - el `CMD` está en forma exec (`[...]`), incluye `app.main:app`, `0.0.0.0`, `8000` y `--no-access-log`, y no `--reload` (RF-4, spec-D3);
  - hay un `HEALTHCHECK` cuyo comando contiene `127.0.0.1:8000/health` (RF-13, plan-D4);
  - el fichero no contiene `API_KEYS` ni `.env` (RNF-3).
  - Falla porque el fichero no existe.
- **GREEN:** `Dockerfile` según plan-D1…D4 y plan-D8: `builder` con venv en `/opt/venv` y `pip install --no-cache-dir .`; `runtime` con `COPY --from=builder /opt/venv`, `PATH` al venv, `ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1`, `useradd --system --uid 10001 --no-create-home app`, `USER 10001`, `EXPOSE 8000`, `HEALTHCHECK` y `CMD`. Comentarios breves con el porqué de cada decisión no obvia.
- **Depende:** T2 (el build depende del contexto que deja pasar `.dockerignore`)
- **RF:** RF-1…RF-4, RF-13, RF-14, RNF-2…RNF-4
- **Hecho cuando:** los casos pasan. El build real se comprueba en T5.

### [ ] T4 — `docker-compose.yml`
- **RED:** `tests/infra/test_compose.py`, con `pytest.mark.skipif(shutil.which("docker") is None, reason=...)`. Copia `docker-compose.yml` a `tmp_path` (sin `.env` al lado, así nunca lee el real) y ejecuta `docker compose -f <copia> config --format json` (no necesita el daemon, plan-D7). Comprueba:
  - el comando termina con código 0 sin `.env` (RF-11);
  - `services.api.environment.REDIS_URL == "redis://redis:6379/0"` (RF-8);
  - `services.api.depends_on.redis.condition == "service_healthy"` (RF-9, spec-D2);
  - `services.redis.healthcheck.test` contiene `ping` (RF-9);
  - `services.api.ports` publica el `8000` del contenedor en el `8000` del host por defecto (RF-10);
  - `services.api.environment` no contiene `API_KEYS` (RNF-3);
  - `services.redis` no publica puertos (plan-D6).
  - Falla porque el fichero no existe.
- **GREEN:** `docker-compose.yml` según plan-D6.
- **Depende:** T3 (`build: .` apunta al `Dockerfile`)
- **RF:** RF-6, RF-8…RF-11, RNF-3
- **Hecho cuando:** los casos pasan en local (el CLI de Docker está instalado aunque el daemon esté apagado).

### [ ] T5 — Verificación manual: la imagen
- **Requisito:** daemon de Docker arrancado (regla 6).
- **Pasos:**
  1. `docker build -t alcampo-scraper:dev .` → termina sin error **y sin compilar nada** (supuesto pendiente del plan §2: `uvicorn[standard]` en `slim`). Anotar el tamaño (`docker image ls`).
  2. Contenido: `docker run --rm --entrypoint sh alcampo-scraper:dev -c "id; ls -A /app; pip list"` → uid `10001`; `/app` vacío (sin `tests/`, `.env`, `specs/`); ni `pytest`, ni `ruff`, ni `respx`, ni `fakeredis` en `pip list` (RF-2, RF-3, RF-5).
  3. Fallo rápido: `docker run --rm alcampo-scraper:dev` sin variables → termina con código distinto de 0 y el `ValidationError` de `ALCAMPO_BASE_URL`/`REDIS_URL` en la salida (RF-7).
  4. Standalone sin Redis: `docker run -d -p 8000:8000 -e ALCAMPO_BASE_URL=… -e REDIS_URL=redis://127.0.0.1:6379/0 alcampo-scraper:dev` (Redis inexistente a propósito) → `/health` y `/docs` responden `200` desde el host; tras el `start-period`, `docker ps` lo muestra `healthy` (RF-12, RF-13, supuesto pendiente del `HEALTHCHECK`). Logs visibles en `docker logs` con request id y **sin** access log de uvicorn (RF-4, RF-14).
  5. Parar y borrar el contenedor.
- **Si falla un supuesto:** parar y avisar con la evidencia (regla 7).
- **Depende:** T3
- **RF:** RF-1…RF-4, RF-7, RF-12…RF-14
- **Hecho cuando:** los resultados están anotados aquí y no queda ningún contenedor corriendo. Commit: solo la anotación en `tasks.md` (`docs`).

### [ ] T6 — Verificación manual: `docker compose`
- **Requisito:** daemon de Docker arrancado (regla 6); **≥10 min desde la última petición a Alcampo** (spec-D5).
- **Preparación:** un fichero de override **en el scratchpad** (fuera del repo) con `ALCAMPO_BASE_URL` y un `API_KEYS` sintético generado para la prueba; se usa con `docker compose -f docker-compose.yml -f <override> …`. No se lee ni se modifica el `.env` real.
- **Pasos:**
  1. `docker compose … up -d --build` → `docker compose ps`: `redis` `healthy` antes de que arranque `api` (RF-9), y luego `api` `healthy` (RF-13).
  2. `GET /api/v1/products?postal_code=28001&term=leche` sin cabecera → `401` (fallo cerrado, spec 004).
  3. La misma petición con el token sintético → `200` con productos: **1 búsqueda real** a Alcampo desde el contenedor (DNS, TLS y fingerprint; supuesto pendiente de TLS del plan §2).
  4. Repetirla → `200` desde la cache: `docker compose logs api` no muestra una segunda petición a Alcampo y `docker compose exec redis redis-cli --scan --pattern 'search:*'` lista la clave (RF-8).
  5. `docker compose logs api`: líneas con request id, sin access log de uvicorn, y **0 coincidencias** del token sintético (RF-14, spec 004 RF-12).
  6. `docker compose down` y borrar el override del scratchpad.
- **Si la búsqueda real recibe un challenge del WAF:** no reintentar; anotarlo, esperar el enfriamiento y avisar.
- **Depende:** T4, T5
- **RF:** RF-6, RF-8, RF-9, RF-13, RF-14
- **Hecho cuando:** los resultados están anotados aquí, el compose está parado y el override borrado. Commit: solo la anotación en `tasks.md` (`docs`).

### [ ] T7 — Docs
- **RED:** ninguno (no hay variables nuevas en `Settings`; el test de `.env.example` no cambia).
- **GREEN:**
  - README: sección **Docker** con requisitos (Docker + `.env` a partir de `.env.example`), `docker compose up --build`, `API_PORT`, que `REDIS_URL` lo fija el compose, `docker compose logs -f api`, cómo reactivar el access log de uvicorn sobrescribiendo el `command`, y que la sonda de vida es `/health`. Avisos: Docker marca `unhealthy` pero **no reinicia** (plan-D4); el Redis del compose es efímero (se pierden cache y enfriamiento al recrearlo); cada sonda deja 2 líneas `INFO` (spec-D4).
  - README: línea de "Estado" y limitaciones (sin lockfile: builds en fechas distintas pueden instalar versiones distintas).
  - `AGENTS.md`: comandos `docker compose up --build` y `docker compose down`.
- **Depende:** T6
- **RF:** RNF-5
- **Hecho cuando:** README y `AGENTS.md` revisados y la suite completa en verde.

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T3, T5 |
| RF-2 | T3, T5 |
| RF-3 | T3, T5 |
| RF-4 | T3, T5 |
| RF-5 | T2, T5 |
| RF-6 | T4, T6 |
| RF-7 | T5 |
| RF-8 | T4, T6 |
| RF-9 | T4, T6 |
| RF-10 | T4 |
| RF-11 | T4 |
| RF-12 | T1, T5 |
| RF-13 | T3, T5, T6 |
| RF-14 | T3, T5, T6 |

Ningún RF queda huérfano. RNF: RNF-1 (`app/` sin cambios, en todas; T1 lo comprueba explícitamente), RNF-2 (T3: sin `curl` ni dependencias nuevas), RNF-3 (T2, T3, T4 y T6), RNF-4 (T3 y el tamaño anotado en T5), RNF-5 (T7).
