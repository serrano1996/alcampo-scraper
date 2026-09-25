# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: esqueleto. La primera feature (`specs/001-alcampo-scraper-mvp`) está en fase de spec.

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

## Desarrollo

```bash
pytest                          # los tests nunca llaman a Alcampo real (respx + fakeredis)
ruff check . && ruff format .   # obligatorio antes de cada commit
```

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
