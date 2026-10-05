# Plan 014 — Dependencias fijadas (lockfile) y CI al día

- **Estado:** aprobado (2026-10-05)
- **Fecha:** 2026-10-05
- **Spec:** [spec.md](spec.md) (aprobada; decisiones citadas como **spec-D1…spec-D5**; las de este plan, **D1…**)
- **Entrega:** 1 PR (~150 líneas, sin contar los lockfiles generados)

## 1. Cambios

| Fichero | Cambio | RF |
|---|---|---|
| `scripts/lock.sh` | **nuevo**: genera los dos lockfiles en un contenedor `python:3.11-slim` | RF-1, RF-2, RF-5 |
| `requirements.lock`, `requirements-dev.lock` | **nuevos**, generados (no se editan a mano) | RF-1, RF-2 |
| `Dockerfile` | `pip install --require-hashes -r requirements.lock` y luego `pip install --no-deps .` | RF-3 |
| `.dockerignore` | readmitir `requirements.lock` (lista de permitidos, spec 005) | RF-3 |
| `.github/workflows/ci.yml` | instalar desde `requirements-dev.lock` + `--no-deps -e .` + `pip check`; `@v7`; `ubuntu-24.04` | RF-4, RF-6 |
| `tests/infra/…` | lockfiles, Dockerfile, CI | todos |
| `README.md` | cómo actualizar; se quita la limitación | RF-5 |

## 2. Decisiones de diseño

**D1 — Un script, no un comando largo en el README.** `scripts/lock.sh` ejecuta, en `docker run --rm python:3.11-slim`, `pip install pip-tools==7.6.1` y dos `pip-compile --generate-hashes --allow-unsafe --strip-extras pyproject.toml` (sin y con `--extra dev`). `pip-tools` con versión fija: el propio generador también es reproducible. Monta el repo como volumen; en Git Bash de Windows necesita `MSYS_NO_PATHCONV=1` y `pwd -W` (documentado en el script). `--upgrade` como argumento opcional para subir versiones a propósito (RF-5).

**D2 — Instalar en dos pasos.** `pip install --require-hashes -r <lock>` instala exactamente las versiones con hashes; luego `pip install --no-deps .` (o `-e .` en la CI) instala el paquete sin volver a resolver nada. Limitación asumida: el `setuptools` del aislamiento de build del segundo paso se descarga sin hash (`[build-system]`); es herramienta de build, no entra en la imagen final (el venv solo lleva el wheel construido).

**D3 — `pip check` en la CI** detecta un `pyproject.toml` con una dependencia que el lock no tiene (el paquete instalado con `--no-deps` declara un requisito no satisfecho). Es el aviso de "regenera el lock" (RF-4).

**D4 — Test del lock sin red.** `tests/infra/test_lockfiles.py` lee `pyproject.toml` (`tomllib`) y cada lock: cada dependencia directa aparece con `==versión`, esa versión cumple el rango de `pyproject.toml` (`packaging`, ya presente con pytest) y lleva al menos un `--hash=sha256:`; el lock de producción **no** contiene ninguna dependencia de `dev` (pytest, ruff…).

## 3. Regresiones previstas

| Test | Por qué | Corrección |
|---|---|---|
| `test_dockerfile.py::test_installs_the_project_without_dev_dependencies` | ahora hay dos `pip install`, uno con `-r requirements.lock` | el proyecto se instala con `--no-deps .`; ninguno con `[dev]` ni `requirements-dev.lock` |
| `test_ci_workflow.py::test_installs_the_dev_extra` | la CI instala desde el lock | instala `requirements-dev.lock` con `--require-hashes`, el paquete con `--no-deps` y ejecuta `pip check` |
| `test_dockerignore.py` (lista de permitidos) | entra `requirements.lock` | se añade a la lista esperada |

## 4. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | `pip-compile` sobre `pyproject.toml` necesita construir metadatos | `setuptools` del `[build-system]`, dentro del contenedor |
| R2 | Las acciones v7 cambian algún parámetro | se usan solo `python-version` y `cache: pip`; la CI en verde lo confirma |
| R3 | Regenerar el lock sube versiones sin querer | sin `--upgrade`, `pip-compile` conserva las fijadas |
