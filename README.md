# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: implementadas `specs/001-alcampo-scraper-mvp` (MVP de búsqueda), `specs/002-alcampo-scraper-antibaneo` (medidas antibaneo), `specs/003-alcampo-scraper-logging` (logging), `specs/004-alcampo-scraper-authentication` (autenticación) y `specs/005-alcampo-scraper-dockerization` (Docker). Ver [limitaciones conocidas](#limitaciones-conocidas).

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env              # y rellena API_KEYS (ver Autenticación)
uvicorn app.main:app --reload
```

Documentación interactiva en `http://127.0.0.1:8000/docs` (botón **Authorize** para probar con tu `X-API-Key`).

## Docker

Levanta la API y un Redis local sin instalar Python ni Redis. Solo hace falta Docker. Detalle en [`specs/005-alcampo-scraper-dockerization`](specs/005-alcampo-scraper-dockerization/spec.md).

```bash
cp .env.example .env              # rellena ALCAMPO_BASE_URL y API_KEYS
docker compose up --build         # API en http://127.0.0.1:8000
docker compose logs -f api        # logs en tiempo real
docker compose down               # parar y borrar los contenedores
```

- **Configuración:** la API lee el `.env` (si falta, no arranca y muestra el `ValidationError` de las variables obligatorias). `REDIS_URL` lo fija el compose para apuntar a su Redis, aunque el `.env` diga otra cosa. El puerto del host se cambia con `API_PORT` (por defecto `8000`): `API_PORT=9000 docker compose up`.
- **Arranque ordenado:** la API no arranca hasta que Redis responde. Hasta la spec 007, una búsqueda sin Redis es un `500`.
- **Imagen:** `python:3.11-slim`, solo dependencias de producción, usuario sin privilegios (uid `10001`). Solo entran en ella `pyproject.toml` y `app/`: el `.dockerignore` es una lista de permitidos, así que ni el `.env` ni ningún fichero nuevo llegan a la imagen.
- **Sonda de vida:** `HEALTHCHECK` contra `/health` cada 30 s (`docker compose ps` muestra `healthy`). `/health` no pide token ni toca Redis ni Alcampo. **Docker solo marca el contenedor como `unhealthy`, no lo reinicia:** eso lo hace un orquestador. Cada sonda deja 2 líneas `INFO` en los logs; si molestan, `LOG_LEVEL=WARNING`.
- **Logs:** en `docker compose logs`, con hora **UTC** (la imagen no define zona horaria). Sin el access log de uvicorn, que duplicaría cada petición sin request id; para reactivarlo, sobrescribe `command` en el compose sin `--no-access-log`.
- **Redis efímero:** sin volumen. Al recrearlo se pierden la cache **y el [enfriamiento](#medidas-antibaneo) del WAF**. En producción, apunta `REDIS_URL` a otro Redis y no uses el servicio `redis` del compose.

Solo la imagen: `docker build -t alcampo-scraper .` y `docker run -p 8000:8000 --env-file .env alcampo-scraper` (con un `REDIS_URL` alcanzable desde el contenedor).

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
curl -H "X-API-Key: $ALCAMPO_API_KEY" \
  "http://127.0.0.1:8000/api/v1/products?postal_code=28001&term=leche"
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

## Autenticación

Todo lo que cuelga de `/api/v1/` exige la cabecera **`X-API-Key`** con un token válido. `/health`, `/docs`, `/redoc` y `/openapi.json` son públicos. Detalle en [`specs/004-alcampo-scraper-authentication`](specs/004-alcampo-scraper-authentication/spec.md).

- **Tokens válidos:** variable `API_KEYS`, separados por comas (se ignoran espacios y entradas vacías). Admite varios a la vez, así que se puede **rotar sin cortes**: añade el nuevo, actualiza los clientes y quita el antiguo.
- **Sin tokens configurados, nadie entra** (falla cerrado): toda petición a `/api/v1/` recibe `401` y al arrancar se registra un `WARNING` que lo avisa.
- **Genera tokens largos y aleatorios.** La longitud no se valida, así que la fortaleza depende de ti:

  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(32))"
  ```

- **Un `401` es igual** tanto si falta la cabecera como si el token es inválido, para no dar pistas:

  ```
  HTTP/1.1 401 Unauthorized
  WWW-Authenticate: ApiKey

  {"detail": "Invalid or missing API key"}
  ```

- El rechazo ocurre **antes** de tocar la cache o Alcampo: una petición sin token no puede provocar tráfico hacia Alcampo ni un bloqueo de su WAF.
- El token solo se acepta en la cabecera. Nunca se registra en los logs; si se envía por error en la URL (`?api_key=…`, `?token=…`, `?key=…`), aparece como `'***'`.

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

**Nunca se registran** cookies de Alcampo, cabeceras completas, el cuerpo de las respuestas de Alcampo ni la `X-API-Key` (ni los tokens de `API_KEYS`). Los valores que envía el cliente se registran escapados (`%r`), así que un salto de línea no puede fabricar líneas falsas.

En local, uvicorn sigue emitiendo su propio access log, sin request id (se desactiva con `--no-access-log`). La imagen Docker ya lo arranca desactivado.

## Limitaciones conocidas

- **`postal_code` no influye todavía en el resultado.** La Fase 0 demostró que Alcampo cambia precio y catálogo según la región (tienda/zona), pero resolverla en vivo cuesta ~8 peticiones y roza el rate-limit de su WAF. Esta primera feature busca siempre en la región por defecto de una sesión anónima ("Vaguada", Madrid, `warehouse: "5"`). La resolución real de `postal_code` → región llega en `specs/007-...` (pendiente).
- **`price_format` solo está verificado para `PER_LITRE`.** Las unidades `PER_KG`, `PER_EACH` y `PER_METER` se infieren del bundle web de Alcampo, no de una respuesta real observada.
- Autenticación de servicio a servicio con un secreto compartido: sin cuentas de usuario, OAuth2/JWT ni cuotas por token (fuera de alcance en la spec 004).
- Logs solo en texto plano: sin JSON ni integración con plataformas de observabilidad (fuera de alcance en la spec 003).
- **Imagen Docker no reproducible al 100 %:** `pyproject.toml` no fija versiones (no hay lockfile), así que dos builds en fechas distintas pueden instalar versiones distintas de las dependencias.
- Docker sin orquestador: `Dockerfile` y `docker-compose.yml` locales, sin Kubernetes, CI/CD ni publicación en un registry (fuera de alcance en la spec 005).
- El enfriamiento no supera el bloqueo del WAF, solo evita insistir. Si el bloqueo dura más que `WAF_COOLDOWN_SECONDS` (se observaron hasta ~4 min), la siguiente búsqueda recibe otro challenge y abre un nuevo enfriamiento.

Detalle completo de lo verificado en vivo: [Fase 0](docs/investigacion/fase-0-alcampo.md).

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
