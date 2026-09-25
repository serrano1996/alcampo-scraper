# AGENTS.md — alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve productos de Alcampo
(`https://www.compraonline.alcampo.es`, plataforma Ocado). Hermana de `mercadona-scraper`:
mismo contrato de respuesta.

**Reglas del proyecto:** [docs/constitution.md](docs/constitution.md). Léelas antes de tocar nada.

## Comandos

```bash
pip install -e ".[dev]"          # instalar
uvicorn app.main:app --reload    # arrancar en local (http://127.0.0.1:8000/docs)
pytest                           # tests (nunca llaman a Alcampo real)
ruff check . && ruff format .    # lint + formato (obligatorio antes de cada commit)
```

## Proceso

- SDD estricto: `specs/NNN-alcampo-scraper-<nombre>/{spec,plan,tasks}.md`, aprobados antes de codificar.
- TDD estricto (RED → GREEN → refactor). Una tarea = un commit; al cerrar cada tarea:
  `ruff check .`, `ruff format --check .`, `pytest -q`, marcar la tarea y proponer el commit. Parar.
- Investigación en vivo de Alcampo: [docs/investigacion/fase-0-alcampo.md](docs/investigacion/fase-0-alcampo.md).
  **Cuidado:** AWS WAF bloquea la IP tras ráfagas de peticiones; sondea despacio.
