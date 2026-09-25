# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: `specs/001-alcampo-scraper-mvp` implementada (MVP de búsqueda). Ver [limitaciones conocidas](#limitaciones-conocidas).

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

Documentación interactiva en `http://127.0.0.1:8000/docs`.

## Desarrollo

```bash
pytest                          # los tests nunca llaman a Alcampo real (respx + fakeredis)
ruff check . && ruff format .   # obligatorio antes de cada commit
```

## Uso del endpoint

```
GET /api/v1/products?postal_code=<5 dígitos>&term=<texto, 1-50 caracteres>
```

```bash
curl "http://127.0.0.1:8000/api/v1/products?postal_code=28001&term=leche"
```

```json
{
  "search": {
    "postal_code": "28001",
    "term": "leche",
    "warehouse": "5",
    "strategy_used": "api",
    "scraped_at": "2026-09-24T10:00:00Z",
    "total_results": 1
  },
  "products": [
    {
      "id": "54180",
      "name": "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo.",
      "price": 5.28,
      "price_format": "0.88 €/L",
      "image_url": "https://www.compraonline.alcampo.es/images-v3/.../300x300.jpg",
      "category": "Leche semidesnatada"
    }
  ]
}
```

- `term` sin resultados → `200` con `products: []`, nunca un error.
- Alcampo no responde (agotados los reintentos, un `4xx` no reintentable, o el WAF de Alcampo bloqueando con un challenge) → `502 {"detail": "Upstream service unavailable"}`.
- Parámetros inválidos (`term` vacío o de más de 50 caracteres) → `422`, sin llamar a Alcampo ni a Redis.

## Limitaciones conocidas

- **`postal_code` no influye todavía en el resultado.** La Fase 0 demostró que Alcampo cambia precio y catálogo según la región (tienda/zona), pero resolverla en vivo cuesta ~8 peticiones y roza el rate-limit de su WAF. Esta primera feature busca siempre en la región por defecto de una sesión anónima ("Vaguada", Madrid, `warehouse: "5"`). La resolución real de `postal_code` → región llega en `specs/007-...` (pendiente).
- **`price_format` solo está verificado para `PER_LITRE`.** Las unidades `PER_KG`, `PER_EACH` y `PER_METER` se infieren del bundle web de Alcampo, no de una respuesta real observada.
- Sin autenticación, logging estructurado ni rotación de User-Agent todavía: llegan en `specs/002-004`.

Detalle completo de lo verificado en vivo: [Fase 0](docs/investigacion/fase-0-alcampo.md).

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
