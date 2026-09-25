# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: implementadas `specs/001-alcampo-scraper-mvp` (MVP de búsqueda) y `specs/002-alcampo-scraper-antibaneo` (medidas antibaneo). Ver [limitaciones conocidas](#limitaciones-conocidas).

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
- Alcampo no responde (agotados los reintentos, un `4xx` no reintentable, el WAF de Alcampo bloqueando con un challenge, o un [enfriamiento](#medidas-antibaneo) en curso) → `502 {"detail": "Upstream service unavailable"}`.
- Parámetros inválidos (`term` vacío o de más de 50 caracteres) → `422`, sin llamar a Alcampo ni a Redis.

## Medidas antibaneo

Invisibles para el consumidor, salvo algo más de latencia en los reintentos. Detalle en [`specs/002-alcampo-scraper-antibaneo`](specs/002-alcampo-scraper-antibaneo/spec.md).

- **Fingerprint de navegador real.** Cada proceso elige al arrancar un User-Agent de un pool de 6 navegadores (Chrome, Firefox, Edge y Safari en Windows, macOS y Linux) y lo mantiene: cambiarlo en cada petición sería más sospechoso. También envía `Referer` y `ecom-request-source: web`, como la web de Alcampo.
- **Reintentos irregulares.** Cada espera entre reintentos suma un jitter aleatorio de hasta `RETRY_JITTER_MAX_S` segundos.
- **`429` respetuoso.** Si Alcampo responde `429` con `Retry-After` (en segundos o como fecha), se espera lo que pide, con un tope de 60 s. En la Fase 0 nunca se vio un `429`: es defensa en profundidad.
- **Enfriamiento tras el WAF.** El bloqueo real de Alcampo es su AWS WAF, que bloquea la IP de salida durante 2–4 minutos. Tras un challenge, el servicio deja de llamar a Alcampo durante `WAF_COOLDOWN_SECONDS` (180 s por defecto): las búsquedas **no cacheadas** responden `502` al instante, y las **cacheadas** siguen respondiendo `200`. La marca es global (una clave en Redis), así que la comparten todas las instancias. `WAF_COOLDOWN_SECONDS=0` lo desactiva.

| Variable | Default | Descripción |
|---|---|---|
| `RETRY_JITTER_MAX_S` | `0.3` | Jitter máximo (s) por espera. `0` = sin jitter. Negativo: la app no arranca |
| `WAF_COOLDOWN_SECONDS` | `180` | Duración del enfriamiento tras un challenge. `0` = desactivado. Negativo: la app no arranca |

**Mantenimiento:** el pool de User-Agents se verificó el 2026-09-25 contra las fuentes oficiales de cada navegador. Revisarlo cada ~3 meses: un User-Agent desfasado delata al bot. Está en `app/scrapers/http_client.py` y hay que actualizar también su copia en `tests/scrapers/test_http_client.py`.

## Limitaciones conocidas

- **`postal_code` no influye todavía en el resultado.** La Fase 0 demostró que Alcampo cambia precio y catálogo según la región (tienda/zona), pero resolverla en vivo cuesta ~8 peticiones y roza el rate-limit de su WAF. Esta primera feature busca siempre en la región por defecto de una sesión anónima ("Vaguada", Madrid, `warehouse: "5"`). La resolución real de `postal_code` → región llega en `specs/007-...` (pendiente).
- **`price_format` solo está verificado para `PER_LITRE`.** Las unidades `PER_KG`, `PER_EACH` y `PER_METER` se infieren del bundle web de Alcampo, no de una respuesta real observada.
- Sin logging estructurado ni autenticación todavía: llegan en `specs/003` y `specs/004`.
- El enfriamiento no supera el bloqueo del WAF, solo evita insistir. Si el bloqueo dura más que `WAF_COOLDOWN_SECONDS` (se observaron hasta ~4 min), la siguiente búsqueda recibe otro challenge y abre un nuevo enfriamiento.

Detalle completo de lo verificado en vivo: [Fase 0](docs/investigacion/fase-0-alcampo.md).

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
