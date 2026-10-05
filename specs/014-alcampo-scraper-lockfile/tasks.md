# Tasks 014 — Dependencias fijadas (lockfile) y CI al día

- **Estado:** aprobado (2026-10-05)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 1 PR: T1–T2.

## Reglas

1. RED → GREEN → refactor; sin RED posible, mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` (**`pytest` a secas**, como la CI). Marcar `[x]`, proponer el commit y **parar**.
3. Regresiones: se corrigen en la tarea que las provoca; cada aserción cambiada se anota con su motivo.

---

### [ ] T1 — Lockfiles
- **RED:** `tests/infra/test_lockfiles.py` (plan-D4): existen los dos; cada dependencia directa fijada con `==`, dentro de su rango y con hash; el de producción sin dependencias de `dev`; el de desarrollo con todas.
- **GREEN:** `scripts/lock.sh` (plan-D1) y los dos lockfiles generados con él (contenedor `python:3.11-slim`, sin tocar Alcampo; sí PyPI).
- **RF:** RF-1, RF-2, RF-5

### [ ] T2 — Imagen y CI desde los lockfiles; CI al día
- **RED:** `test_dockerfile.py`: instala `--require-hashes -r requirements.lock` y el proyecto con `--no-deps .`. `test_ci_workflow.py`: instala `requirements-dev.lock` con `--require-hashes`, el paquete con `--no-deps -e .`, ejecuta `pip check`, usa `actions/checkout@v7`, `actions/setup-python@v7` y `runs-on: ubuntu-24.04`. `test_dockerignore.py`: `requirements.lock` admitido.
- **GREEN:** `Dockerfile`, `.dockerignore`, `ci.yml` (plan-D2, plan-D3).
- **Regresión:** plan §3; se anotan.
- README: sección de dependencias (`scripts/lock.sh`, `--upgrade`), se quita la limitación "Imagen Docker no reproducible".
- **Verificación:** `docker build` local con el lock; tras el push, CI en verde sin el aviso de Node 20.
- **RF:** RF-3, RF-4, RF-6
