# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: implementadas `specs/001-alcampo-scraper-mvp` (MVP de búsqueda), `specs/002-alcampo-scraper-antibaneo` (medidas antibaneo) y `specs/003-alcampo-scraper-logging` (logging). Ver [limitaciones conocidas](#limitaciones-conocidas).

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

## Logging

Todo va a `stderr` en texto plano (sin dependencias nuevas); quien despliegue lo captura. Detalle en [`specs/003-alcampo-scraper-logging`](specs/003-alcampo-scraper-logging/spec.md).

```
2026-09-25 10:00:00,123 INFO [3f2a9c0e1b7d4e6f8a1b2c3d4e5f6a7b] app.middleware.request_context: request finished status=200 duration_ms=41.7
```

Formato: fecha y hora, nivel, `[request id]`, logger y mensaje. Fuera de una petición (arranque, parada) el request id es `-`.

- **`LOG_LEVEL`** (`INFO` por defecto): `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL`, sin distinguir mayúsculas. Cualquier otro valor impide arrancar.
- **Request id por petición.** Cada petición recibe un id propio que aparece en **todas** sus líneas de log y se devuelve en la cabecera **`X-Request-ID`** (también en `422`, `500` y `502`). Si un consumidor reporta un error, con ese id se encuentran todas las líneas de su petición. Un `X-Request-ID` enviado por el cliente se ignora, para que nadie pueda meter texto propio en los logs.
- **Cada petición** deja una línea al empezar (método, ruta y parámetros) y otra al terminar (estado y duración).

Qué nivel tiene cada evento:

| Nivel | Eventos |
|---|---|
| `ERROR` | error no controlado (con traceback, responde `500`); `502` por Alcampo caído, reintentos agotados o `4xx` no reintentable; challenge del WAF (con la duración del enfriamiento); respuesta de Alcampo con JSON inválido o formato inesperado; **todos** los productos de una respuesta descartados (probable cambio de formato en Alcampo) |
| `WARNING` | cada reintento; búsqueda rechazada durante el enfriamiento; algunos productos descartados; entrada de cache corrupta |
| `INFO` | inicio y fin de cada petición |

**Nunca se registran** cookies de Alcampo, cabeceras completas, el cuerpo de las respuestas de Alcampo ni (a partir de la spec 004) la `X-API-Key`. Los valores que envía el cliente se registran escapados (`%r`), así que un salto de línea no puede fabricar líneas falsas.

uvicorn sigue emitiendo su propio access log, sin request id. Si molesta, se desactiva al desplegar (`--no-access-log`).

## Limitaciones conocidas

- **`postal_code` no influye todavía en el resultado.** La Fase 0 demostró que Alcampo cambia precio y catálogo según la región (tienda/zona), pero resolverla en vivo cuesta ~8 peticiones y roza el rate-limit de su WAF. Esta primera feature busca siempre en la región por defecto de una sesión anónima ("Vaguada", Madrid, `warehouse: "5"`). La resolución real de `postal_code` → región llega en `specs/007-...` (pendiente).
- **`price_format` solo está verificado para `PER_LITRE`.** Las unidades `PER_KG`, `PER_EACH` y `PER_METER` se infieren del bundle web de Alcampo, no de una respuesta real observada.
- Sin autenticación todavía: llega en `specs/004`.
- Logs solo en texto plano: sin JSON ni integración con plataformas de observabilidad (fuera de alcance en la spec 003).
- El enfriamiento no supera el bloqueo del WAF, solo evita insistir. Si el bloqueo dura más que `WAF_COOLDOWN_SECONDS` (se observaron hasta ~4 min), la siguiente búsqueda recibe otro challenge y abre un nuevo enfriamiento.

Detalle completo de lo verificado en vivo: [Fase 0](docs/investigacion/fase-0-alcampo.md).

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
