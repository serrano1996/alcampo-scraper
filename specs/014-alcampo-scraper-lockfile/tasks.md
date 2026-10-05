# Tasks 014 — Dependencias fijadas (lockfile) y CI al día

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 1 PR: T1–T2.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` (**`pytest` a secas**, como la CI). Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.

---

### [x] T1 — Lockfiles
> **Nota (2026-10-05):** RED real (`FileNotFoundError` de los dos lockfiles). GREEN: `scripts/lock.sh` (plan-D1: `python:3.11-slim`, `pip-tools==7.6.1`, `--generate-hashes --allow-unsafe --strip-extras --no-emit-index-url`, `--upgrade` opcional, `pwd -W` + `MSYS_NO_PATHCONV` en Git Bash; borra `*.egg-info`/`build` al acabar). Generados en 2 min 53 s: `requirements.lock` 25 paquetes (incluido `uvloop`, Linux), `requirements-dev.lock` 45; todos con hashes; ninguna herramienta de `dev` en el de producción. `tests/infra/test_lockfiles.py` (5 tests, sin red): directas fijadas, en rango y con hash; producción sin `dev`; toda línea del lock fijada con hash. **Mutación:** `redis==7.0.0` en el lock → `redis==7.0.0 out of range`; restaurado. Nota: la cabecera que escribe `pip-compile` muestra `--no-index` (lo traduce de `--no-emit-index-url`); el comando que vale es `scripts/lock.sh`.
- **RED:** `tests/infra/test_lockfiles.py` (plan-D4): existen los dos; cada dependencia directa fijada con `==`, dentro de su rango y con hash; el de producción sin dependencias de `dev`; el de desarrollo con todas.
- **GREEN:** `scripts/lock.sh` (plan-D1) y los dos lockfiles generados con él (contenedor `python:3.11-slim`, sin tocar Alcampo; sí PyPI).
- **RF:** RF-1, RF-2, RF-5

### [x] T2 — Imagen y CI desde los lockfiles; CI al día
> **Nota (2026-10-05):** RED real en 4 tests de `tests/infra/`. GREEN: `Dockerfile` instala `--require-hashes -r requirements.lock` en su propia capa (se reconstruye solo si cambia el lock) y luego `--no-deps .`; `.dockerignore` readmite `requirements.lock`; `ci.yml` en `ubuntu-24.04`, `actions/checkout@v7`, `actions/setup-python@v7` con `cache-dependency-path: requirements-dev.lock` (el caché por defecto mira `requirements*.txt`), instala `--require-hashes -r requirements-dev.lock` + `--no-deps -e .` + `pip check`. **Regresiones, una a una:** `test_installs_the_project_without_dev_dependencies` ya no exige que **todo** `pip install` acabe en `.` (ahora hay uno con `-r requirements.lock`); solo prohíbe `[dev]` y el lock de desarrollo, y el orden lock → `--no-deps .` pasa a `test_installs_the_pinned_dependencies_then_the_project_alone`; `test_installs_the_dev_extra` → `test_installs_the_pinned_dev_dependencies`; `READMITTED` de `test_dockerignore.py` con `requirements.lock`; docstring de `test_ci_workflow.py` ("sin remoto") corregido. README: "Dependencias fijadas" (`scripts/lock.sh`, `--upgrade`), CI real con enlace y la lección de `pytest` a secas; fuera la limitación "Imagen Docker no reproducible". **Verificación local:** `docker build` en 12 s; dentro, fastapi 0.142.2, pydantic 2.13.5, httpx 0.28.1, redis 8.1.0, uvicorn 0.54.0 (las del lock) y 0 herramientas de `dev`. **CI (run 37303237853, 2026-10-05):** en verde en 52 s (instalación desde el lock con hashes, `pip check`, lint, formato, tipos, tests, imagen), sin los avisos de Node 20 ni de `ubuntu-latest`. **Fin de la spec 014.**
- **RED:** `test_dockerfile.py`: instala `--require-hashes -r requirements.lock` y el proyecto con `--no-deps .`. `test_ci_workflow.py`: instala `requirements-dev.lock` con `--require-hashes`, el paquete con `--no-deps -e .`, ejecuta `pip check`, usa `actions/checkout@v7`, `actions/setup-python@v7` y `runs-on: ubuntu-24.04`. `test_dockerignore.py`: `requirements.lock` admitido.
- **GREEN:** `Dockerfile`, `.dockerignore`, `ci.yml` (plan-D2, plan-D3).
- **Regresión:** plan §3; se anotan.
- README: sección de dependencias (`scripts/lock.sh`, `--upgrade`), se quita la limitación "Imagen Docker no reproducible".
- **Verificación:** `docker build` local con el lock; tras el push, CI en verde sin el aviso de Node 20.
- **RF:** RF-3, RF-4, RF-6
