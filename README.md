# alcampo-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Alcampo online](https://www.compraonline.alcampo.es). Ofrece el mismo contrato que
`mercadona-scraper` para poder comparar ambos supermercados sin adaptar el consumidor.

> Estado: implementadas `specs/001-alcampo-scraper-mvp` (MVP de búsqueda), `specs/002-alcampo-scraper-antibaneo` (medidas antibaneo), `specs/003-alcampo-scraper-logging` (logging), `specs/004-alcampo-scraper-authentication` (autenticación), `specs/005-alcampo-scraper-dockerization` (Docker), `specs/006-alcampo-scraper-refactor` (refactor y calidad), `specs/007-alcampo-scraper-warehouse-resolution` (región por código postal y Redis degradado) y `specs/008-alcampo-scraper-outbound-protection` (protección de salida hacia Alcampo). Ver [limitaciones conocidas](#limitaciones-conocidas).

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
- **Arranque ordenado:** la API no arranca hasta que Redis responde. Si Redis cae después, la API sigue respondiendo sin cache (ver [Redis](#redis)).
- **Imagen:** `python:3.11-slim`, solo dependencias de producción, usuario sin privilegios (uid `10001`). Solo entran en ella `pyproject.toml` y `app/`: el `.dockerignore` es una lista de permitidos, así que ni el `.env` ni ningún fichero nuevo llegan a la imagen.
- **Sonda de vida:** `HEALTHCHECK` contra `/health` cada 30 s (`docker compose ps` muestra `healthy`). `/health` no pide token ni toca Redis ni Alcampo. **Docker solo marca el contenedor como `unhealthy`, no lo reinicia:** eso lo hace un orquestador. Cada sonda deja 2 líneas `INFO` en los logs; si molestan, `LOG_LEVEL=WARNING`.
- **Logs:** en `docker compose logs`, con hora **UTC** (la imagen no define zona horaria). Sin el access log de uvicorn, que duplicaría cada petición sin request id; para reactivarlo, sobrescribe `command` en el compose sin `--no-access-log`.
- **Redis efímero:** sin volumen. Al recrearlo se pierden la cache **y el [enfriamiento](#medidas-antibaneo) del WAF**. En producción, apunta `REDIS_URL` a otro Redis y no uses el servicio `redis` del compose.

Solo la imagen: `docker build -t alcampo-scraper .` y `docker run -p 8000:8000 --env-file .env alcampo-scraper` (con un `REDIS_URL` alcanzable desde el contenedor).

## Desarrollo

```bash
pytest                          # los tests nunca llaman a Alcampo real (respx + fakeredis)
ruff check . && ruff format .   # obligatorio antes de cada commit
mypy                            # tipos, estricto, sobre app/ (obligatorio antes de cada commit)
```

- **Warnings = errores:** la suite falla ante cualquier warning (`filterwarnings = error`), así una deprecación se ve el día que aparece. Hoy no hay ninguna excepción.
- **Versiones acotadas:** cada dependencia tiene límite superior de versión mayor (`<1` para las `0.x`); subir una versión mayor es una decisión explícita.
- **CI:** `.github/workflows/ci.yml` ejecuta en cada push y pull request, con Python 3.11, `ruff check`, `ruff format --check`, `mypy`, `pytest -q` y `docker build`. **Aún no se ha ejecutado en GitHub** (el repositorio no tiene remoto): sus comandos se validan en local.

## Uso del endpoint

```
GET /api/v1/products?postal_code=<5 dígitos>&term=<texto, 1-100 caracteres>[&page=1][&page_size=50]
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
    "warehouse": "11",
    "strategy_used": "api",
    "scraped_at": "2026-09-24T10:00:00Z",
    "total_results": 1,
    "page": 1,
    "page_size": 50,
    "total_pages": 1
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
- Alcampo no responde (agotados los reintentos, un `4xx` no reintentable, el WAF de Alcampo bloqueando con un challenge, un [enfriamiento](#medidas-antibaneo) en curso, el [límite de peticiones](#medidas-antibaneo) agotado o una búsqueda que supera `SEARCH_TIMEOUT_SECONDS`) → `502 {"detail": "Upstream service unavailable"}`.
- Parámetros inválidos (`postal_code` que no sean exactamente 5 dígitos, `term` vacío o de más de 100 caracteres, `page` fuera de 1–20, `page_size` fuera de 1–100) → `422`, sin llamar a Alcampo ni a Redis.
- Página más allá de la última que tiene Alcampo para ese término → `404 {"detail": "Page out of range"}`.
- Código postal que Alcampo no conoce o donde no reparte (p. ej. `99999`, Ceuta `51001`, Melilla `52001`) → `404 {"detail": "Postal code not served by Alcampo"}`.

La respuesta tiene **exactamente la forma de la de Mercadona** (mismos campos, tipos y obligatoriedad); un test lo comprueba contra una copia de su esquema ([spec 009](specs/009-alcampo-scraper-contract-parity/spec.md)). En concreto:

- `search.term` es el **término que se buscó**: normalizado (minúsculas, espacios repetidos colapsados) y recortado a 50 caracteres, como hace la propia web de Alcampo. `Leche` devuelve `"term": "leche"`.
- `image_url` y `category` **nunca son `null`**: un producto sin imagen o sin categoría se descarta (en 100 productos reales no apareció ninguno).

## Paginación

`page` (1–20, por defecto 1) y `page_size` (1–100, por defecto 50), como en Mercadona. Sin parámetros, la respuesta de siempre: la primera página de 50.

- **Alcampo pagina con un cursor**, no por número: cada página trae el token de la siguiente, y ese token **solo vale en la sesión que lo recibió**. El servicio guarda los tokens en la sesión de cada región, así que pedir las páginas en orden cuesta **1 petición por página**. Saltar a una página lejana en frío recorre las anteriores (la página 5 son 5 peticiones), y todas quedan cacheadas.
- **Recorridos profundos y límites:** cada página del recorrido pasa por los [límites de salida](#medidas-antibaneo). Llegar en frío a la página 20 son 20 peticiones y la ventana corta admite 10 por minuto, así que el recorrido **se corta con `502`**. No se pierde lo hecho: el siguiente intento sigue desde la última página conocida.
- **`total_results` es una estimación** (la suma de los recuentos por categoría que da Alcampo; en vivo, 669 frente a 670 reales) y es **exacto en la última página**. `total_pages` se calcula a partir de él, con un tope de 20.
- Al renovarse la sesión de una región (cada 50 minutos), sus tokens se pierden: la siguiente página no cacheada vuelve a recorrer desde la primera.

## Región por código postal

Alcampo cambia precio y catálogo según la región (la tienda que sirve): por ejemplo, Las Palmas frente a Madrid. Cada búsqueda usa la región real de su `postal_code`, y `search.warehouse` es su `retailerRegionId` (`"5"` Vaguada, `"32"` Telde…). Detalle en [`specs/007-alcampo-scraper-warehouse-resolution`](specs/007-alcampo-scraper-warehouse-resolution/spec.md).

- **Resolver un código postal nuevo es caro:** ~8 peticiones a Alcampo, una de ellas (crear un destino de entrega temporal) la que su WAF castiga. Por eso:
  - la región de cada código postal se recuerda **7 días** (`REGION_CACHE_TTL_SECONDS`), y un código sin servicio, 1 hora;
  - como mucho **2 resoluciones nuevas cada 10 minutos** (`REGION_RESOLUTION_LIMIT` / `REGION_RESOLUTION_WINDOW_SECONDS`). Por encima, los códigos postales **nuevos** reciben `502` al momento; los ya conocidos no se ven afectados;
  - varias búsquedas simultáneas del mismo código postal nuevo comparten una sola resolución;
  - un código postal inexistente o sin servicio se detecta antes de crear el destino: no consume cupo.
- **Alcampo guarda la región en la sesión, no en la petición.** El servicio mantiene **una sesión por región** (sus cookies, en memoria del proceso) y la comprueba al confirmarla: si la página no muestra la región esperada, responde `502` antes que devolver precios de otra región.
- **La sesión se renueva cada 50 minutos** (`SESSION_MAX_AGE_SECONDS`) reutilizando su destino: 4 peticiones y ningún destino nuevo.
- Los códigos postales de una misma región comparten sesión y cache (`search:{región}:{término}:{página}:{tamaño}`).

## Redis

- **Timeouts:** conexión y operaciones cortan a los `REDIS_TIMEOUT_SECONDS` (2 s). Un Redis colgado ya no deja peticiones esperando para siempre.
- **Redis caído o colgado → el servicio degrada, no se cae:** las búsquedas van a Alcampo sin cache (`200`) con un `WARNING "redis unavailable op=…"`. El límite de peticiones, el de resoluciones y el enfriamiento siguen actuando con un respaldo **local a cada proceso** (se pierde la coordinación entre instancias), y las regiones ya conocidas siguen en la memoria del proceso. `/health` no toca Redis.
- **Circuit breaker:** tras el primer fallo, el servicio deja de intentar Redis durante `REDIS_CIRCUIT_OPEN_SECONDS` (10 s) y usa directamente los respaldos, sin esperar ningún timeout; luego lo vuelve a probar. Sin Redis, una búsqueda pasó de ~9 s a ~1 s. En los logs: un `WARNING "redis circuit open"` al abrirse y un `INFO "redis circuit closed"` al volver.

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

Invisibles para el consumidor, salvo algo más de latencia en los reintentos y `502` rápidos en los picos. Detalle en [`specs/002-alcampo-scraper-antibaneo`](specs/002-alcampo-scraper-antibaneo/spec.md) y [`specs/008-alcampo-scraper-outbound-protection`](specs/008-alcampo-scraper-outbound-protection/spec.md).

- **Fingerprint de navegador real.** Cada proceso elige al arrancar un User-Agent de un pool de 6 navegadores (Chrome, Firefox, Edge y Safari en Windows, macOS y Linux) y lo mantiene: cambiarlo en cada petición sería más sospechoso. También envía `Referer` y `ecom-request-source: web`, como la web de Alcampo.
- **Reintentos irregulares.** Cada espera entre reintentos suma un jitter aleatorio de hasta `RETRY_JITTER_MAX_S` segundos.
- **`429` respetuoso.** Si Alcampo responde `429` con `Retry-After` (en segundos o como fecha), se espera lo que pide, con un tope de 60 s. En la Fase 0 nunca se vio un `429`: es defensa en profundidad.
- **Enfriamiento tras el WAF.** El bloqueo real de Alcampo es su AWS WAF, que bloquea la IP de salida durante 2–4 minutos. Tras un challenge, el servicio deja de llamar a Alcampo durante `WAF_COOLDOWN_SECONDS` (180 s por defecto): las búsquedas **no cacheadas** responden `502` al instante, y las **cacheadas** siguen respondiendo `200`. La marca es global (una clave en Redis), así que la comparten todas las instancias. `WAF_COOLDOWN_SECONDS=0` lo desactiva.
- **Enfriamiento creciente.** Si llega otro challenge antes de `WAF_COOLDOWN_MAX_SECONDS` (900 s) desde el anterior, la duración se duplica hasta ese tope: 180 → 360 → 720 → 900 s. Sin challenges recientes, vuelve a 180 s. Así no se repite el ciclo "challenge → espera corta → challenge".
- **Búsquedas iguales simultáneas → 1 petición.** Si varias búsquedas del mismo término llegan mientras una ya está consultando Alcampo, esperan su resultado en lugar de repetir la petición (verificado con 10 simultáneas: 1 petición a Alcampo). Agrupa dentro de cada proceso; entre instancias protege el límite global.
- **Término normalizado.** La cache y la petición a Alcampo no distinguen mayúsculas ni espacios repetidos: `Leche`, `LECHE` y `leche  entera` comparten entrada con `leche` y `leche entera`. Verificado en vivo que Alcampo devuelve exactamente lo mismo. Desde la spec 009 la respuesta devuelve el término normalizado (el que se buscó), como Mercadona.
- **Límite global de peticiones, en dos ventanas.** Como mucho **10 peticiones por minuto** (`ALCAMPO_RATE_LIMIT` / `ALCAMPO_RATE_WINDOW_SECONDS`) y **30 cada 15 minutos** (`ALCAMPO_RATE_LIMIT_LONG` / `ALCAMPO_RATE_WINDOW_LONG_SECONDS`), contadas en Redis entre todas las instancias y reintentos incluidos (ventanas deslizantes). Agotado cualquiera de los dos, las búsquedas **no cacheadas** responden `502` al instante sin salir a Alcampo; las **cacheadas** siguen respondiendo `200`. El límite corto bajó de 20 a 10 y se añadió el largo porque el 2026-10-01 el WAF bloqueó la IP con **10 búsquedas en 15 minutos**, un tráfico que una ventana de un minuto nunca habría frenado ([spec 010](specs/010-alcampo-scraper-outbound-pacing/spec.md)). **Ningún valor está demostrado seguro.** `0` desactiva cada ventana.
- **Peticiones espaciadas.** Entre dos peticiones del mismo proceso pasan al menos `ALCAMPO_MIN_INTERVAL_MS` (500 ms) más un jitter de hasta `ALCAMPO_INTERVAL_JITTER_MS` (500 ms). Una búsqueda normal (1 petición) no lo nota; la primera búsqueda de un código postal nuevo (~10 peticiones de resolución) pasa de 1–3 s a ~7 s. `ALCAMPO_MIN_INTERVAL_MS=0` lo desactiva.
- **Errores provocados, a la vista.** Cualquier `4xx` de Alcampo que no sea `404` ni `429` deja un `WARNING` con el código, el endpoint y el tipo de petición: una petición inválida puede ser una señal de bot para el WAF (el bloqueo del 2026-10-01 llegó justo después de dos).
- **Tiempo máximo por búsqueda.** Una búsqueda que no termina en `SEARCH_TIMEOUT_SECONDS` (15 s), intentos y esperas incluidos, se cancela y responde `502`. Antes el peor caso rondaba los 30 s.

| Variable | Default | Descripción |
|---|---|---|
| `RETRY_JITTER_MAX_S` | `0.3` | Jitter máximo (s) por espera. `0` = sin jitter. Negativo: la app no arranca |
| `WAF_COOLDOWN_SECONDS` | `180` | Duración del enfriamiento tras un challenge. `0` = desactivado. Negativo: la app no arranca |
| `WAF_COOLDOWN_MAX_SECONDS` | `900` | Tope del enfriamiento creciente, y ventana en la que un challenge cuenta como reciente. Menor que `WAF_COOLDOWN_SECONDS`: la app no arranca |
| `ALCAMPO_RATE_LIMIT` | `10` | Peticiones máximas a Alcampo por ventana, entre todas las instancias. `0` = sin límite. Negativo: la app no arranca |
| `ALCAMPO_RATE_WINDOW_SECONDS` | `60` | Ventana del límite anterior. Menor que `1`: la app no arranca |
| `ALCAMPO_RATE_LIMIT_LONG` | `30` | Peticiones máximas en la ventana larga, entre todas las instancias. `0` = sin límite |
| `ALCAMPO_RATE_WINDOW_LONG_SECONDS` | `900` | Ventana larga (15 min). Menor que `1`: la app no arranca |
| `ALCAMPO_MIN_INTERVAL_MS` | `500` | Espaciado mínimo entre peticiones del mismo proceso. `0` = sin espaciado |
| `ALCAMPO_INTERVAL_JITTER_MS` | `500` | Jitter máximo añadido al espaciado |
| `SEARCH_TIMEOUT_SECONDS` | `15` | Tiempo máximo total de una búsqueda en Alcampo (y de una resolución de región). `≤ 0`: la app no arranca |
| `REDIS_TIMEOUT_SECONDS` | `2` | Timeout de conexión y de cada operación con Redis. `≤ 0`: la app no arranca |
| `REDIS_CIRCUIT_OPEN_SECONDS` | `10` | Tras un fallo de Redis, cuánto tiempo se deja de intentar (se usan los respaldos locales). `0` = desactivado |
| `REGION_CACHE_TTL_SECONDS` | `604800` | Cuánto se recuerda la región de un código postal (7 días) |
| `REGION_NEGATIVE_CACHE_TTL_SECONDS` | `3600` | Cuánto se recuerda que un código postal no tiene servicio |
| `REGION_RESOLUTION_LIMIT` | `2` | Resoluciones nuevas (creación de destinos) por ventana. `0` = sin límite |
| `REGION_RESOLUTION_WINDOW_SECONDS` | `600` | Ventana del límite anterior |
| `SESSION_MAX_AGE_SECONDS` | `3000` | Edad a partir de la cual se renueva la sesión de una región (por debajo de la hora de su cookie) |

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
| `ERROR` | error no controlado (con traceback, responde `500`); `502` por Alcampo caído, reintentos agotados, `4xx` no reintentable o búsqueda que supera el tiempo máximo (`reason='search timeout'`); challenge del WAF (con la duración del enfriamiento y el tráfico reciente, `recent_traffic=…`); respuesta de Alcampo con JSON inválido o formato inesperado; **todos** los productos de una respuesta descartados (probable cambio de formato en Alcampo) |
| `WARNING` | cada reintento; búsqueda rechazada sin llamar a Alcampo (`search throttled reason='WAF cooldown active'`, `'outbound rate limit reached'` o `'region resolution limit reached'`); Redis no disponible (`redis unavailable op=…`); algunos productos descartados; entrada de cache corrupta |
| `INFO` | inicio y fin de cada petición; región de cada código postal (`region resolved … source=cache|resolved|shared`); sesión de región confirmada (`reason=new|renewal`); código postal sin servicio (`404`); origen de cada búsqueda: `search served source=hit` (cache), `miss` (Alcampo) o `shared` (resultado de otra búsqueda simultánea igual) |

**Nunca se registran** cookies de Alcampo, cabeceras completas, el cuerpo de las respuestas de Alcampo ni la `X-API-Key` (ni los tokens de `API_KEYS`). Los valores que envía el cliente se registran escapados (`%r`), así que un salto de línea no puede fabricar líneas falsas.

En local, uvicorn sigue emitiendo su propio access log, sin request id (se desactiva con `--no-access-log`). La imagen Docker ya lo arranca desactivado.

## Limitaciones conocidas

- **El umbral del WAF para crear destinos es una hipótesis** (3–4 por ventana e IP, Fase 0). El límite de 2 cada 10 minutos es prudente, no medido. En un arranque en frío con muchos códigos postales nuevos, parte de ellos reciben `502` hasta que se van resolviendo.
- **No se sabe cuánto vive un destino temporal** en Alcampo (se reutilizó a los 13 minutos). Si caduca, la región se olvida y se vuelve a resolver (con su coste).
- **La región se lee del HTML de la portada de Alcampo.** Si cambia su forma, ninguna sesión se podrá confirmar y las búsquedas no cacheadas darán `502` (con un `ERROR` que indica el paso).
- Las sesiones de región viven en memoria de cada proceso: con varias instancias, cada una confirma las suyas.
- **`price_format` solo está verificado para `PER_LITRE`.** Las unidades `PER_KG`, `PER_EACH` y `PER_METER` se infieren del bundle web de Alcampo, no de una respuesta real observada.
- Autenticación de servicio a servicio con un secreto compartido: sin cuentas de usuario, OAuth2/JWT ni cuotas por token (fuera de alcance en la spec 004).
- Logs solo en texto plano: sin JSON ni integración con plataformas de observabilidad (fuera de alcance en la spec 003).
- **Imagen Docker no reproducible al 100 %:** `pyproject.toml` no fija versiones (no hay lockfile), así que dos builds en fechas distintas pueden instalar versiones distintas de las dependencias.
- Docker sin orquestador: `Dockerfile` y `docker-compose.yml` locales, sin Kubernetes ni publicación en un registry. Hay CI (spec 006), pero no despliegue continuo.
- El enfriamiento no supera el bloqueo del WAF, solo evita insistir. Si el bloqueo dura más que el enfriamiento aplicado, la siguiente búsqueda recibe otro challenge y el enfriamiento se duplica (hasta `WAF_COOLDOWN_MAX_SECONDS`).
- **Los límites de salida no conocen el umbral real del WAF.** El 2026-10-01 hubo un bloqueo con menos tráfico que en otras pruebas sin bloqueo, así que el recuento de peticiones no lo explica todo (la hipótesis es que el WAF también puntúa respuestas de error). Para ajustarlos con datos, **cada challenge registra el tráfico de los 1, 5 y 15 minutos anteriores** (`recent_traffic=…`), por tipo (`search`, `resolution`, `session`) y con el número de `4xx`.
- **Las búsquedas iguales solo se agrupan dentro de cada proceso.** Con varias instancias, cada una puede hacer su propia petición; las protege el límite global. Las líneas de log de una búsqueda compartida (reintentos, challenge) llevan el request id de la **primera** petición del grupo.
- **Con varias instancias, sus relojes deben estar sincronizados (NTP):** el límite global usa la hora de cada instancia.
- **Paginación:** `total_results` es una estimación salvo en la última página; los tokens de página viven en la sesión de cada proceso, así que con varias instancias cada una recorre las suyas; y una página profunda en frío puede cortarse con `502` por la ventana corta (ver [Paginación](#paginación)).

Detalle completo de lo verificado en vivo: [Fase 0](docs/investigacion/fase-0-alcampo.md).

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Alcampo](docs/investigacion/fase-0-alcampo.md)
- Specs: [`specs/`](specs/)
