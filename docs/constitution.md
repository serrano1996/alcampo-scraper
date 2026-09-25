# Constitución del proyecto `alcampo-scraper`

Reglas no negociables. Cualquier excepción se discute y se documenta **antes** de implementarla.

## Principios

1. **Stack fijo:** Python 3.11+, FastAPI, Pydantic v2, `httpx` async, Redis (`redis.asyncio`). Nada fuera de esto sin discutirlo.
2. **Scraping:** solo endpoints JSON internos de Alcampo. Prohibido Playwright, Selenium y BeautifulSoup salvo que la investigación (Fase 0) demuestre que no hay alternativa, y en ese caso se discute antes.
3. **Async obligatorio:** todo endpoint y llamada externa usa `async def` + `httpx.AsyncClient`.
4. **Tipado estricto:** type hints en toda función pública. `Any` prohibido en anotaciones.
5. **Validación:** toda entrada y salida de la API pasa por un schema Pydantic v2.
6. **Cache:** las llamadas repetidas a Alcampo pasan por Redis antes de ir a la red.
7. **Tests:** `pytest` obligatorio para cada endpoint y cada scraper. Los tests **nunca** llaman a Alcampo real: `respx` para HTTP, `fakeredis` para Redis.
8. **Lint:** `ruff check . && ruff format .` limpio antes de cada commit (line-length 100).
9. **Docs vivas:** Swagger (`/docs`) refleja los schemas reales; `README.md` y `.env.example` se actualizan en la misma feature que los cambia.
10. **Idioma:** código, docstrings y commits en inglés; documentación técnica (specs, README) en español.
11. **Límite de responsabilidad:** el scraper no persiste datos propios más allá del cache. Es un proxy inteligente sobre Alcampo, no la fuente de verdad.
12. **Secretos:** nunca se commitean credenciales reales (ni de Alcampo ni de terceros que use su web). En tests se usan valores sintéticos con la misma forma.

## Convenciones

- `snake_case` para funciones, variables y ficheros; `PascalCase` para clases y modelos; `UPPER_SNAKE_CASE` para constantes y variables de entorno.
- Commits en Conventional Commits con el scope de la spec y la tarea:
  `feat(003-alcampo-scraper-logging): add request id middleware (T4)`.

## Proceso (SDD estricto)

- Cada feature vive en `specs/NNN-alcampo-scraper-<nombre>/` con `spec.md` → `plan.md` → `tasks.md`, en ese orden.
- No se escribe código hasta que los tres artefactos estén aprobados.
- Las dudas abiertas de la spec se resuelven antes del plan; si dependen de Alcampo, se verifican en vivo.
- TDD estricto (RED → GREEN → refactor). Una tarea = un commit.
- Si el plan resulta incorrecto al implementar, se para y se avisa con evidencia.

## Estructura

```
app/
├── api/v1/          # rutas
├── core/            # config (pydantic-settings), dependencias, seguridad, logging, state tipado
├── models/          # schemas Pydantic (API y payloads crudos de Alcampo)
├── mappers/         # crudo de Alcampo -> modelo de la API
├── scrapers/        # clientes de endpoints de Alcampo + factoría del httpx.AsyncClient
├── services/        # lógica de negocio y repositorios de cache
├── middleware/
├── exceptions.py    # excepciones de dominio (sin tipos de httpx)
└── main.py          # app + lifespan
tests/               # espejo de app/ + tests/integration/ (app real + lifespan + fakeredis + respx)
specs/
docs/constitution.md
```
