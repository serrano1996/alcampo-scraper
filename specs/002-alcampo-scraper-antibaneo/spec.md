# Spec 002 — Medidas antibaneo

- **Estado:** aprobada (2026-09-25)
- **Fecha:** 2026-09-25
- **Referencia:** misma secuencia que `mercadona-scraper/specs/002-mercadona-scraper-antibaneo`, más un requisito propio contra el WAF (D5)
- **Evidencia:** [Fase 0 §5](../../docs/investigacion/fase-0-alcampo.md)

## 1. Contexto y objetivo

La 001 ya sale a Alcampo con un fingerprint razonable: un User-Agent de Chrome real **fijo**, `Accept: application/json` y `Accept-Language: es-ES` (plan-D12 de la 001). También reintenta `5xx`, `429` y errores de transporte con backoff exponencial **exacto** (`0.5 s`, `1 s`, …), y trata el challenge del WAF como `502` sin reintento (spec-D5 de la 001).

Quedan tres huecos frente a la API de Mercadona:

1. **User-Agent fijo:** todas las instancias del servicio salen con la misma cadena.
2. **Esperas perfectamente regulares:** un patrón `0.5 s, 1 s, 2 s` exacto es fácil de identificar por análisis de tráfico.
3. **`429` sin `Retry-After`:** si el servidor dice cuánto esperar, lo ignoramos, y si dice "espera una hora", no hay tope.

Y un cuarto, propio de Alcampo:

4. **Insistir con la IP marcada por el WAF.** Tras un challenge, la IP queda bloqueada 2–4 minutos (Fase 0 §5), pero la 001 sigue enviando a Alcampo cada búsqueda no cacheada, que vuelve a recibir el challenge.

**Objetivo:** cerrar los tres primeros huecos con el mismo contrato que Mercadona, y el cuarto con un enfriamiento tras challenge, sin cambiar el stack (constitución #1) ni usar navegador (constitución #2).

**Lo que dice la Fase 0 y conviene no perder de vista.** En Alcampo **nunca se observó un `429` ni una cabecera `Retry-After`**. El bloqueo real es el **AWS WAF**. Los requisitos de `429` y jitter son **defensa en profundidad y paridad con Mercadona**; el enfriamiento (RF-15 a RF-19) es el único dirigido al comportamiento observado.

## 2. Usuarios y actores

- **Sistema:** `create_http_client`, `send_with_retry` y `ProductService` aplican las medidas en cada petición saliente hacia `www.compraonline.alcampo.es`.
- **Consumidor de la API:** no nota nada, salvo algo más de latencia ante un `429` o un `5xx`, y `502` inmediatos mientras dure un enfriamiento.
- **Operador:** configura `RETRY_JITTER_MAX_S` y `WAF_COOLDOWN_SECONDS`.

## 3. Historias de usuario

- **H1.** Como responsable del scraper en producción, quiero que las peticiones salientes se parezcan a las de un navegador real y no sigan un patrón regular, para reducir la probabilidad de que Alcampo marque la IP del servicio.
- **H2.** Como responsable del scraper, quiero respetar lo que Alcampo pida cuando limite el tráfico (`429` + `Retry-After`), sin que un valor desproporcionado deje colgado el proceso.
- **H3.** Como responsable del scraper, quiero que, cuando el WAF de Alcampo nos bloquee, el servicio deje de insistir durante un tiempo, para no prolongar el bloqueo y seguir sirviendo lo que ya esté en cache.

## 4. Requisitos funcionales (EARS)

### Fingerprint

- **RF-1.** CUANDO el sistema construya el `httpx.AsyncClient` (`create_http_client`), EL sistema DEBERÁ fijar un `User-Agent` elegido al azar del pool de RF-2, **una sola vez por instancia de cliente**, no por petición. Rotar el User-Agent dentro de la misma sesión (que comparte las cookies `VISITORID` y `AWSALB` de Alcampo) sería una señal de bot más fuerte que mantenerlo fijo.
- **RF-2.** El pool DEBERÁ ser exactamente este. Son las versiones estables vigentes, verificadas el 2026-09-25 en las fuentes oficiales (D1), y siguen el formato "reducido" real de cada navegador (versión menor a `0.0.0` en los Chromium; sistema operativo congelado):

  ```python
  USER_AGENTS: tuple[str, ...] = (
      # Chrome 155 / Windows
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
      # Chrome 155 / macOS
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
      # Chrome 154 / Linux (la estable de Linux va una versión por detrás)
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
      # Firefox 156 / Windows
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
      # Edge 154 / Windows (el UA real de Edge incluye el token Chrome/)
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
      # Safari 27 / macOS
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/27.0 Safari/605.1.15",
  )
  ```

- **RF-3.** EL cliente DEBERÁ enviar en todas las peticiones:
  - `Accept: application/json` y `Accept-Language: es-ES,es;q=0.9` (sin cambios respecto a la 001);
  - `Referer: https://www.compraonline.alcampo.es/` (D3);
  - `ecom-request-source: web` (D2).
- **RF-4.** EL cliente NO DEBERÁ enviar `Origin` (D3): un navegador no lo envía en un `GET` a su propio origen, y enviarlo sería atípico.

### `429` y `Retry-After`

- **RF-5.** SI Alcampo responde `429` Y quedan intentos, ENTONCES EL sistema DEBERÁ esperar lo que indique `Retry-After` antes de reintentar. DEBERÁ soportar los dos formatos del estándar HTTP: segundos enteros no negativos (`Retry-After: 120`) y fecha HTTP (`Retry-After: Wed, 24 Sep 2026 10:00:00 GMT`).
- **RF-6.** SI `Retry-After` falta o no se puede interpretar en ninguno de los dos formatos, ENTONCES EL sistema DEBERÁ usar el backoff exponencial de la 001 (`RETRY_BASE_DELAY × 2^(n-1)`).
- **RF-7.** SI `Retry-After` es una fecha HTTP ya pasada, ENTONCES EL sistema DEBERÁ tratarla como espera `0` (más el jitter de RF-9).
- **RF-8.** EL sistema DEBERÁ limitar **toda** espera derivada de un `429` a un máximo de **60 s**, incluido el jitter.
- `Retry-After` **solo** se respeta en `429`. En un `503` u otro `5xx` se ignora y se usa el backoff (D4).

### Jitter

- **RF-9.** CUANDO el sistema vaya a esperar antes de un reintento (por `5xx`, `429` o error de transporte), EL sistema DEBERÁ sumar a la espera un jitter aleatorio uniforme en `[0, RETRY_JITTER_MAX_S]`.
- **RF-10.** EL sistema DEBERÁ leer `RETRY_JITTER_MAX_S` de `Settings` (por defecto `0.3`). SI el valor es negativo, ENTONCES la aplicación DEBERÁ fallar al arrancar.

### Comportamiento que no cambia

- **RF-11.** SI se agotan los intentos con `429` persistente, ENTONCES EL sistema DEBERÁ lanzar `UpstreamUnavailableError` → `502`, igual que con un `5xx` agotado. Ya es así en la 001; esta spec lo fija con un test de regresión.
- **RF-12.** EL challenge del WAF DEBERÁ seguir dando `502` **sin reintento y sin espera** dentro de la petición (spec-D5 de la 001).
- **RF-13.** Los `4xx` distintos de `429` DEBERÁN seguir sin reintentarse (RF-16 de la 001).
- **RF-14.** EL contrato de error NO DEBERÁ cambiar: todo `502` de esta spec, incluido el del enfriamiento, responde `{"detail": "Upstream service unavailable"}`.

### Enfriamiento tras challenge del WAF (D5)

- **RF-15.** CUANDO Alcampo responda un challenge del WAF (cabecera `x-amzn-waf-action`), EL sistema DEBERÁ guardar en Redis una marca de enfriamiento con TTL `WAF_COOLDOWN_SECONDS`, además de responder `502` (RF-12).
- **RF-16.** MIENTRAS exista la marca, CUANDO llegue una búsqueda **no cacheada**, EL sistema DEBERÁ responder `502` **sin hacer ninguna petición a Alcampo**.
- **RF-17.** MIENTRAS exista la marca, las búsquedas **cacheadas** DEBERÁN seguir respondiendo `200` desde cache: se consulta la cache antes que la marca.
- **RF-18.** CUANDO expire la marca, EL sistema DEBERÁ volver a consultar Alcampo con normalidad, sin intervención manual.
- **RF-19.** EL sistema DEBERÁ leer `WAF_COOLDOWN_SECONDS` de `Settings` (por defecto `180`). `0` desactiva el enfriamiento (no se guarda marca). SI el valor es negativo, ENTONCES la aplicación DEBERÁ fallar al arrancar.

## 5. Requisitos no funcionales

- **RNF-1. Extensión, no mecanismo paralelo:** el fingerprint vive en `create_http_client`, las esperas en `send_with_retry`, y el enfriamiento en la capa de servicio y cache. Se reutilizan `RETRY_MAX_ATTEMPTS` y `RETRY_BASE_DELAY`. La configuración nueva se limita a `RETRY_JITTER_MAX_S` y `WAF_COOLDOWN_SECONDS`.
- **RNF-2. Async:** las esperas usan el `sleep` inyectable de la 001 (`asyncio.sleep` por defecto); `time.sleep` está prohibido. Redis sigue siendo `redis.asyncio`.
- **RNF-3. Tests deterministas:** el azar (User-Agent y jitter) y el reloj (para fechas de `Retry-After`) DEBERÁN ser inyectables, para que los tests fijen el resultado y nunca esperen de verdad.
- **RNF-4. Marca global:** la marca es **una sola clave** para todo el servicio, no una por término ni por región, porque el WAF bloquea por IP de salida. Varias instancias detrás de la misma IP comparten Redis y, por tanto, el enfriamiento.
- **RNF-5. Tipado estricto:** sin `Any` (constitución #4).
- **RNF-6. Docs vivas:** `README.md` y `.env.example` incluyen `RETRY_JITTER_MAX_S` y `WAF_COOLDOWN_SECONDS`, y el README explica el enfriamiento.

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `Retry-After: 3600` | espera recortada a 60 s (RF-8) |
| `Retry-After: 59` y jitter 0.3 | 59 + jitter, pero nunca más de 60 s (RF-8) |
| `Retry-After` con fecha pasada | 0 + jitter (RF-7) |
| `Retry-After: -5`, `abc` o vacío | backoff exponencial (RF-6) |
| `503` con `Retry-After: 30` | se ignora la cabecera; backoff (D4) |
| `RETRY_JITTER_MAX_S=0` | sin jitter: esperas exactas, como en la 001 |
| `RETRY_JITTER_MAX_S=-1` o `WAF_COOLDOWN_SECONDS=-1` | la app no arranca (RF-10, RF-19) |
| `429` en el último intento | `502` sin esperar (RF-11) |
| Challenge del WAF | `502` inmediato, sin espera ni jitter, y se guarda la marca (RF-12, RF-15) |
| Búsqueda no cacheada durante el enfriamiento | `502` sin petición a Alcampo (RF-16) |
| Búsqueda cacheada durante el enfriamiento | `200` desde cache (RF-17) |
| `WAF_COOLDOWN_SECONDS=0` | se comporta como la 001: sin marca, cada búsqueda va a Alcampo |
| Bloqueo real más largo que el enfriamiento (Fase 0: hasta ~4 min frente a 180 s) | la siguiente búsqueda recibe otro challenge y se abre un nuevo enfriamiento. Es aceptable: son 1 o 2 peticiones extra, no una por búsqueda |
| Redis caído al leer o escribir la marca | fuera de alcance, como toda caída de Redis hasta la 007 (`500`) |
| Cliente inyectado en tests | el User-Agent aleatorio y las cabeceras solo se fijan en `create_http_client`; los tests que construyen su propio `httpx.AsyncClient` no se ven afectados |

## 7. Fuera de alcance

- **Rotación de IP y proxies.** Fuera del proyecto, no solo de esta spec (misma decisión que Mercadona). Ninguna medida cambia la IP de salida.
- **Superar el challenge del WAF o resolver CAPTCHAs.** Exige ejecutar JavaScript (navegador), prohibido por la constitución #2. El enfriamiento **evita** insistir; no supera el bloqueo.
- **Rotar el User-Agent por petición:** es contraproducente (RF-1).
- **`ecom-request-source-version` y `client-route-id`** (D2): la versión cambia con cada despliegue de Alcampo, y un valor desfasado podría delatar más que no enviarlo.
- **Imitar huellas TLS o HTTP/2 de un navegador** (JA3 y similares): `httpx` no lo permite sin librerías fuera del stack.
- **Actualización automática del pool de User-Agents.** Se revisa a mano (ver §9, deuda).
- **Cambios en la estrategia de búsqueda o en la región por defecto** (spec 007).

## 8. Criterios de finalización

- [ ] RF-1 a RF-19 cubiertos por tests unitarios y de integración, todos en verde.
- [ ] Ningún test espera tiempo real: la suite no se ralentiza por el jitter, `Retry-After` ni el enfriamiento.
- [ ] Un test de integración demuestra que, tras un challenge, una segunda búsqueda no cacheada **no genera ninguna petición** a Alcampo, y una cacheada responde `200`.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] `README.md` y `.env.example` documentan `RETRY_JITTER_MAX_S` y `WAF_COOLDOWN_SECONDS`.
- [ ] **Sin verificación manual contra un `429` ni contra el WAF reales:** en la Fase 0 nunca se observó un `429`, y provocar el WAF a propósito bloquearía la IP. Ambos se verifican con `respx`, y el PR lo dirá explícitamente. Sí se hará **1 búsqueda real** para confirmar que el nuevo fingerprint (User-Agent del pool, `Referer`, `ecom-request-source`) no rompe nada.

## 9. Decisiones (dudas resueltas el 2026-09-25)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Versiones del pool de User-Agents | Versiones estables más nuevas, verificadas en fuentes oficiales el 2026-09-25 (ver abajo) | RF-2. **Deuda:** revisar el pool en cada spec nueva o cada ~3 meses |
| D2 | Cabeceras propias del cliente web | Solo `ecom-request-source: web` | RF-3 |
| D3 | `Referer` y `Origin` | Solo `Referer`, sin `Origin` | RF-3, RF-4 |
| D4 | `Retry-After` en `503` | Solo en `429` | RF-5, §6 |
| D5 | Medida contra el WAF | Enfriamiento tras challenge, dentro de esta spec | RF-15 a RF-19, `WAF_COOLDOWN_SECONDS=180` |

### Fuentes de D1 (consultadas el 2026-09-25)

| Navegador | Versión estable | Fuente |
|---|---|---|
| Chrome (Windows, macOS) | 155.0.8059.12 | `versionhistory.googleapis.com/v1/chrome/platforms/{win,mac}/channels/stable/versions` |
| Chrome (Linux) | 154.0.8037.57 | `versionhistory.googleapis.com/v1/chrome/platforms/linux/channels/stable/versions` |
| Firefox | 156.0.1 | `product-details.mozilla.org/1.0/firefox_versions.json` |
| Edge (Windows) | 154.0.4258.37 | `edgeupdates.microsoft.com/api/products` |
| Safari | 27 (publicado el 2026-09-14; macOS 27, 26 y Sequoia) | [Safari 27 Release Notes](https://developer.apple.com/documentation/safari-release-notes/safari-27-release-notes) |

**Corrección respecto a Mercadona:** su UA de Edge (`… (KHTML, like Gecko) Edg/130.0.0.0 Safari/537.36`) no incluye el token `Chrome/`, que el Edge real sí envía. El nuestro lo incluye.
