# Tasks 006 — Refactor y calidad

- **Estado:** aprobado (2026-09-30) por instrucción del usuario ("termina lo que queda ya"): se implementa seguido, un commit local por tarea (sin push)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Entrega:** un solo PR.

## Reglas de cada tarea

1. **RED → GREEN → refactor**; si no hay RED posible, verificación por mutación anotada.
2. Cierre: `ruff check .`, `ruff format --check .`, `mypy` (desde T1) y `pytest -q`, todo limpio. Marcar `[x]` y commit.

---

### [x] T1 — `mypy` estricto
> **Nota:** RED real (6 errores). Corregidos con tipos, sin `# type: ignore`: `StreamHandler[TextIO]` solo bajo `TYPE_CHECKING` (su subscripción en tiempo de ejecución no está garantizada en 3.11); `2.0 ** n` en vez de `2 ** n` (que `typeshed` tipa como `Any`); en el cliente de sesión, un `TypeAdapter` por respuesta como constante del módulo y `_parse(adapter: TypeAdapter[T])`; `params: dict[str, str | int]` en la búsqueda. `mypy`: 0 errores en 33 ficheros.
- **RED:** `mypy` con la configuración nueva → los 6 errores del plan §1.
- **GREEN:** `[tool.mypy]` y `[tool.pydantic-mypy]` en `pyproject.toml`; `mypy` en `dev`; corregir los 6 errores con tipos (sin `# type: ignore`).
- **RF:** RF-3

### [x] T2 — `mypy` en el cierre de cada tarea
> **Nota:** sin RED posible (solo documentación). `AGENTS.md`: comando `mypy` y cierre de tarea; constitución, principio 8 ("Lint y tipos").
- **GREEN:** `AGENTS.md` (comandos y proceso) y `docs/constitution.md` (principio 8 y proceso).
- **RF:** RF-4

### [x] T3 — `AppResources` tipado
> **Nota:** RED real (`ModuleNotFoundError: app.core.state`). `lifespan` guarda un `AppResources` (dataclass congelada) en `app.state.resources`; `resources(app)` es el único lector de `app.state` y falla con `RuntimeError` fuera del `lifespan`. `security.py` y `dependencies.py` lo usan; docstring de `dependencies.py` corregido (RF-7). 12 usos en tests pasan a `resources(client.app).*` sin cambiar aserciones. **Mutación:** `res.redsi` → `mypy`: `"AppResources" has no attribute "redsi"; maybe "redis"?` (antes solo fallaba en ejecución). **Hallazgo de `mypy` al tipar el cableado:** `RegionService` declaraba entregar a `adopt` una `ChainSession` (solo pasos 0–5), pero `RegionSessions.adopt` usa `propose`, `activate` y `client`; funcionaba porque el objeto real los tiene. `ChainSession` extiende ahora `RegionSession`.
- **RED:** `tests/core/test_state.py`: `resources(app)` devuelve el `AppResources` del `lifespan` con todos sus campos; sin `lifespan`, error claro.
- **GREEN:** `app/core/state.py`; `lifespan` y `get_product_service` lo usan; docstring de `dependencies.py` actualizado (RF-7).
- **Regresión:** 12 usos de `client.app.state.*` en tests → `resources(client.app).*`.
- **RF:** RF-1, RF-2, RF-7

### [x] T4 — Middleware ASGI puro
> **Nota:** el RED previsto (cabecera en streaming) no era posible: `BaseHTTPMiddleware` ya la añadía. El defecto real era otro, y es el RED que se usó: registraba `request finished` al **empezar** la respuesta, antes de enviar un cuerpo en streaming (`["request started", "request finished", "streaming the second chunk"]`), con una duración que no incluía el cuerpo. Con el middleware ASGI puro, la línea de fin sale cuando la respuesta ha terminado, y los logs del cuerpo llevan el mismo request id. Si una excepción llega con la respuesta ya empezada, se relanza (no se puede convertir en `500`). **Red de seguridad:** 397 tests en verde; `git diff` de `tests/`: 38 líneas añadidas y **0 eliminadas**, ninguna aserción existente cambió.
- **RED:** test nuevo: una respuesta en streaming lleva `X-Request-ID` y sus dos líneas de log con el mismo id.
- **GREEN:** `RequestContextMiddleware` como ASGI puro (plan-D2).
- **Red de seguridad:** toda la suite de las specs 003/004 **sin cambiar aserciones**.
- **RF:** RF-5, RF-6

### [x] T5 — Dependencias y warnings
> **Nota:** RED real (15 fallos: 13 dependencias sin límite superior, `filterwarnings` ausente y `httpx2` no declarado). Límites según plan-D4 sobre las versiones instaladas; `pip install -e ".[dev]"` resuelve sin conflictos. Con `httpx2` desaparece el `StarletteDeprecationWarning`, y con `filterwarnings = ["error"]` la suite pasa (413) **sin ninguna excepción documentada**, porque no queda ningún warning.
- **RED:** `tests/test_package.py`: toda dependencia (y extra `dev`) tiene límite superior; `pytest` está configurado con `filterwarnings = ["error"]`.
- **GREEN:** límites (plan-D4), `httpx2` en `dev`, `filterwarnings`.
- **RF:** RF-10, RF-11, RF-12

### [x] T6 — Workflow de CI
> **Nota:** RED real (`FileNotFoundError` en 8 tests). `.github/workflows/ci.yml`: `push` y `pull_request`, Python 3.11, `pip install -e ".[dev]"`, `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` y `docker build`; permisos de solo lectura. El test cubre que PyYAML lee `on:` como `True`. **Sin remoto, el workflow no se ha ejecutado nunca en GitHub** (riesgo R1): se validaron en local los mismos comandos, incluido `docker build` (imagen en Python 3.11.16, borrada después). Observación: la imagen instaló FastAPI 0.142.2 frente a la 0.141.1 del venv local; está dentro del límite, pero muestra la falta de lockfile ya documentada.
- **RED:** `tests/infra/test_ci_workflow.py`: el workflow corre en `push` y `pull_request`, con Python 3.11, y ejecuta `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` y `docker build`.
- **GREEN:** `.github/workflows/ci.yml` (plan-D5).
- **RF:** RF-9

### [ ] T7 — Docs y comprobaciones finales
- **GREEN:** README (sección Desarrollo: `mypy` y CI); comprobar RF-7 (sin docstrings desfasados: `grep`) y RF-8 (README de `postal_code`, ya correcto desde la 007).
- **RF:** RF-7, RF-8, RNF-3

## Trazabilidad

| RF | Tareas |
|---|---|
| RF-1, RF-2 | T3 |
| RF-3 | T1 |
| RF-4 | T2 |
| RF-5, RF-6 | T4 |
| RF-7 | T3, T7 |
| RF-8 | T7 |
| RF-9 | T6 |
| RF-10, RF-11, RF-12 | T5 |
