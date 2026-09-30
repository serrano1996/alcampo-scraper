# Plan 007 — Resolución de región por código postal

- **Estado:** aprobado (2026-09-28). Entrega: 5 PRs encadenados (D12; PR 3 partido el 2026-09-30)
- **Fecha:** 2026-09-28
- **Spec:** [spec.md](spec.md) (aprobada y enmendada tras §2). Sus decisiones se citan como **spec-D1…spec-D9**; las de specs anteriores como **008-plan-Dn**, etc. Las decisiones de este plan son **D1…**

## 1. Visión general

```
GET /api/v1/products?postal_code=35001&term=agua
  └─ ProductQuery: postal_code = 5 dígitos, si no 422                                   RF-1
     └─ ProductService.search
          region = RegionService.region_for(cp)                                         RF-2…RF-8
          │   ├─ L1 en memoria → Redis `postal-code-region:{cp}` (7 d) / negativo (1 h)
          │   ├─ NOT_SERVED → PostalCodeNotServedError → 404                            RF-4
          │   └─ sin resolver → InFlight por CP → AlcampoRegionResolver (pasos 0–5)
          │        └─ antes del paso 4: region_limiter.acquire() (2 / 600 s)            RF-8
          │        └─ RegionSessions.confirm(región, destino) → retailerRegionId        RF-9
          ├─ cache `search:{retailerRegionId}:{término}` → hit                          RF-12
          └─ miss → InFlight `{retailerRegionId}:{término}` →
                 session = RegionSessions.get(región)  (renueva si > 50 min)            RF-9…RF-11
                 scraper.search(term, client=session.client)
                 search.warehouse = retailerRegionId                                    RF-13

Toda petición a Alcampo (cadena, renovación, búsqueda): retry 002 + WAF/enfriamiento 002/008
+ límite global 008 + tiempo máximo 008                                                 RF-6

Redis: timeouts 2 s; si falla → WARNING y respaldo en memoria (cache: se salta;
límites y enfriamiento: locales al proceso; regiones: L1)                               RF-14, RF-15 (D10)
```

## 2. Verificación en vivo previa (spec-D1), 2026-09-28

Cuatro rondas contra Alcampo real con el cliente HTTP de la app (mismo fingerprint), 30 s entre peticiones y 10 min entre rondas. **28 peticiones, 1 creación de destino, ningún challenge.** Nunca se imprimieron el token CSRF, el `visitorId` ni valores de cookies; el estado de sesión se borró del scratchpad al terminar.

| Ronda | Qué | Resultado |
|---|---|---|
| 1 (10:58–11:02Z) | Cadena completa para `35001` (pasos 0–7) + `GET /` + búsqueda `agua` | Todo `200`. Paso 4 devuelve un **string** (el `deliveryDestinationId`). Paso 6: `originCartProposition.cartPropositionId` y `destinationCartProposition.cartPropositionId`. Paso 7: `{cartId, regionId, type, deliveryDestinationId}` **sin `retailerRegionId`**. El HTML de `GET /` tras confirmar: `regionName: Telde`, `retailerRegionId: 32`, `regionId` = el resuelto |
| 2 (11:13–11:17Z) | Sesión **nueva** + `proposition`/`active` **reutilizando el destino** de la ronda 1 | Confirma Telde (`32`) **sin crear destino**; `agua`: 49/49 precios idénticos a la ronda 1. `VISITORID` (`Max-Age=3600`) **se renueva en cada respuesta**. El bundle no tiene rutas de sesión reconocibles; la búsqueda no informa de la región (ni cuerpo ni cabeceras) |
| 2 | `07001` (Palma), pasos 1–3 | `DELIVERABLE`: Baleares **sí** tiene servicio |
| 3 (11:27–11:30Z) | `51001` (Ceuta) y `52001` (Melilla), pasos 1–3 | `200 {"deliverability":"NOT_DELIVERABLE"}`: la cadena se corta en el paso 3, **antes** de crear destino |
| 4 (12:08Z) | Sesión de la ronda 1, **65 min inactiva** | Sigue en Telde; `agua` con los mismos 49 precios. Matiz: se enviaron las cookies sin su caducidad (incluido `VISITORID` vencido); no se probó qué pasa si el cliente la descarta |

**Consecuencias** (ya reflejadas en la enmienda de la spec):

| Duda | Consecuencia |
|---|---|
| (a) sesión inactiva | Conserva la región al menos 65 min, pero no se confía a ciegas: renovación periódica (spec-D6) |
| (b) comprobar la región | Solo con el HTML de `GET /`: se usa al confirmar o renovar, no en cada búsqueda |
| (c) `retailerRegionId` | Del HTML de `GET /` tras `active`: forma parte de la confirmación |
| (d) reconfirmar sin destino | **Sí**: renovar = `GET /` + `proposition` + `active` + `GET /` (4 peticiones, ninguna crea destino). El límite de resoluciones solo cuenta creaciones (RF-11) |
| (e) sin servicio | `NOT_DELIVERABLE` en el paso 3 → `404` tras 3 peticiones |

**Queda sin verificar:** cuánto vive un destino temporal (se reutilizó a los 13 min). Si caduca, `proposition` fallará: el plan olvida la región y la vuelve a resolver (D6, R2).

## 3. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `redis_timeout_seconds`, `region_cache_ttl_seconds`, `region_negative_cache_ttl_seconds`, `region_resolution_limit`, `region_resolution_window_seconds`, `session_max_age_seconds` | varios |
| `app/main.py` | timeouts en `create_redis`; respaldos locales y `RegionSessions` en el `lifespan`; handler `404` | RF-4, RF-14, RF-15 |
| `app/models/product.py` | `PostalCode` = 5 dígitos | RF-1 |
| `app/exceptions.py` | `PostalCodeNotServedError` | RF-4 |
| `app/scrapers/alcampo_session.py` | **nuevo**: `AlcampoSessionClient` — `GET /` y extracción del estado SSR (CSRF, `visitorId`, región, `retailerRegionId`); pasos 0–7 como métodos | RF-2, RF-9 |
| `app/scrapers/alcampo_search.py` | `search(term, *, client)` recibe el cliente de la sesión de la región | RF-9 |
| `app/scrapers/http_client.py` | `create_http_client` sigue igual; se usa también para cada sesión de región | RF-9 |
| `app/services/region_repository.py` | **nuevo**: `postal-code-region:{cp}` y `region:{regionId}` en Redis + L1 en memoria | RF-3, RF-4 |
| `app/services/region_sessions.py` | **nuevo**: sesiones por región en memoria, confirmación y renovación | RF-9…RF-11 |
| `app/services/region_service.py` | **nuevo**: CP → región (cache, agrupación, límite, resolución) | RF-2…RF-8 |
| `app/services/rate_limiter.py` | clave configurable (hoy fija) para reutilizarlo como límite de resoluciones; respaldo local | RF-8, RF-15 |
| `app/services/waf_cooldown.py`, `search_cache.py` | respaldo ante `RedisError` | RF-15 |
| `app/services/product_service.py` | región real en vez de `DEFAULT_WAREHOUSE`; clave de cache y de agrupación por `retailerRegionId` | RF-12, RF-13 |
| `README.md`, `.env.example` | variables y comportamiento | RNF-5 |

## 4. Modelo de datos

| Clave Redis | Contenido | TTL |
|---|---|---|
| `postal-code-region:{cp}` | `regionId` (uuid) **o** `NOT_SERVED` | 7 d / 1 h |
| `region:{regionId}` | JSON `{"retailer_region_id": "32", "delivery_destination_id": "<uuid>"}` | 7 d (se renueva al resolver otro CP de la región) |
| `search:{retailerRegionId}:{término}` | como hoy (antes siempre `5`) | `CACHE_TTL_SECONDS` |
| `ratelimit:alcampo:region-resolutions` | sorted set, como el límite global | la ventana |

**En memoria (por proceso, nunca en Redis ni en logs):** `RegionSessions` = `{regionId: (httpx.AsyncClient con sus cookies, retailerRegionId, confirmada_en)}`.

El `deliveryDestinationId` y el `regionId` son identificadores efímeros o públicos de Alcampo, no credenciales (Fase 0, apartado de fixtures): pueden ir a Redis. El token CSRF y el `visitorId` solo viven dentro de una confirmación.

## 5. Decisiones de diseño

**D1 — `AlcampoSessionClient` encapsula la cadena**, uno por sesión (un `httpx.AsyncClient` de `create_http_client`, con su propio tarro de cookies). `open()` hace `GET /` y extrae del HTML CSRF, `visitorId`, `regionId` y `retailerRegionId` con expresiones regulares acotadas (sin BeautifulSoup, constitución #2). Si no aparece exactamente un valor de cada, `UpstreamUnavailableError` (RF-5). Los pasos 1–7 son métodos tipados con modelos Pydantic de respuesta (`extra="ignore"`), como `AlcampoSearchResponse`.
- *Descartada:* reutilizar el cliente HTTP global de la app. Sus cookies son las de la sesión anónima (Vaguada) y mezclarlas con una confirmación cambiaría la región de **todas** las búsquedas.

**D2 — Toda petición de la cadena pasa por `send_with_retry` y el límite global** (008-plan-D4), igual que la búsqueda: mismo `acquire()` antes de cada intento y misma detección del challenge. El challenge se propaga como `UpstreamBlockedError` y el servicio activa el enfriamiento (RF-6).

**D3 — Límite de resoluciones = `OutboundRateLimiter` con otra clave**, adquirido **justo antes del paso 4** (crear destino) y solo ahí (RF-8, RF-11 enmendado). Un CP inexistente o sin servicio no consume cupo (se corta antes). Agotado → `RegionResolutionLimitedError(UpstreamThrottledError)`: `502` con `WARNING` (008-plan-D5), sin cambios en el handler.

**D4 — `RegionService.region_for(cp) -> Region`** (`region_id`, `retailer_region_id`, `delivery_destination_id`):
1. L1 en memoria → Redis. `NOT_SERVED` → `PostalCodeNotServedError`.
2. Si no hay nada, resolución agrupada por CP (`InFlightSearches` de la 008, reutilizado tal cual).
3. Pasos 0–3. `[]` en el paso 1 o `deliverability != "DELIVERABLE"` → cache negativa + `PostalCodeNotServedError` (RF-4).
4. Si la región resultante (paso 5) ya tiene registro `region:{regionId}` → se reutiliza su `retailerRegionId` y **no** se confirma otra sesión. Si no, `RegionSessions.confirm(region_id, destino)` (pasos 6–7 + `GET /`) da el `retailerRegionId` y deja la sesión lista.
5. Guarda `postal-code-region:{cp}` y `region:{regionId}`.

**D5 — `RegionSessions`**, un registro por proceso creado en el `lifespan` (como `InFlightSearches`):
- `get(region)`: si hay sesión con menos de `SESSION_MAX_AGE_SECONDS`, la devuelve; si no, la **renueva** (sesión nueva + `proposition`/`active` con el destino guardado + `GET /`), agrupando renovaciones simultáneas de la misma región.
- La confirmación **comprueba** que el HTML devuelve el `regionId` esperado; si no, cierra el cliente y lanza `UpstreamUnavailableError("region not confirmed")` → `502` (RF-10). Nunca se busca con una sesión sin confirmar.
- Al renovar, el cliente anterior se cierra cuando termina la búsqueda en curso (se sustituye la referencia; se cierra en la siguiente renovación o en el `lifespan`).

**D6 — Destino caducado.** Si `proposition` o `active` responden `4xx` con un destino guardado, se borra `region:{regionId}` (los `postal-code-region` que apuntan a ella no se pueden enumerar, y no hace falta): el siguiente CP de esa región encuentra el registro ausente y se re-resuelve (paso 4, con el límite de RF-8). La petición actual → `502`.

**D7 — `PostalCode` con patrón `^[0-9]{5}$`** (tras recortar espacios, como hoy). `404` con un handler para `PostalCodeNotServedError` y `detail` fijo (spec-D8).

**D8 — `search.warehouse` = `retailerRegionId`** (`"32"`, `"11"`…), coherente con el `"5"` actual (spec 001 D1). Desaparece `DEFAULT_WAREHOUSE` del flujo de búsqueda.

**D9 — Tiempo máximo:** la resolución agrupada y la renovación de sesión corren bajo su propio `asyncio.timeout(SEARCH_TIMEOUT_SECONDS)`, y la búsqueda bajo otro, como hoy. Una búsqueda de un CP nuevo puede tardar hasta 2× el tiempo máximo en el peor caso; lo normal son ~10 peticiones de ~0,3 s.

**D10 — Degradación sin Redis (spec-D7):**
- `create_redis(url, timeout)` con `socket_connect_timeout` y `socket_timeout` (RF-14).
- Cada repositorio captura `redis.exceptions.RedisError` (incluye conexión y timeout) y registra `WARNING "redis unavailable op=%s, degraded"`:
  - cache de búsqueda: `get` → `None`, `set` → no hace nada;
  - límites (global y de resoluciones): pasan a un **limitador local** de ventana deslizante (`collections.deque`) con los mismos parámetros;
  - enfriamiento: pasa a un **marcador local** (instante de fin + última duración) con la misma lógica creciente;
  - regiones: solo L1.
- Los respaldos locales viven en `app.state` (creados en el `lifespan`) para que duren entre peticiones.
- *Descartada:* un "Redis de respaldo" en memoria genérico. `fakeredis` es solo de desarrollo y un doble completo sería mucho código para cuatro operaciones.

**D11 — Logs** (RF-17): `INFO "region resolved postal_code=%r region=%s source=%s"` (`cache`, `resolved`, `shared`), `INFO "region session confirmed region=%s retailer=%s reason=%s"` (`new`, `renewal`), `WARNING` para límite agotado y Redis degradado, `ERROR` con el paso para formas inesperadas. Nunca CSRF, `visitorId`, cookies, coordenadas ni direcciones.

**D12 — Entrega en 4 PRs encadenados** (spec-D9, con el PR de resolución partido en dos al aprobar el plan): **PR 1** Redis (bloque E), **PR 2a** validación, `404` y cliente de la cadena (T5–T7), **PR 2b** repositorio y servicio de regiones (T8–T9), **PR 3** sesiones, búsqueda por región, docs y verificación manual (C+D). **Enmienda (2026-09-30):** el PR 3 superó las 400 líneas al cerrar T11 (~1.000, sobre todo tests), así que se parte en **PR 3a** (T10–T11: sesiones y búsqueda por región, una unidad funcional completa) y **PR 3b** (T12–T13: integración transversal, docs y verificación manual).

## 6. Regresiones previstas

| Tests afectados | Por qué | Corrección |
|---|---|---|
| `tests/core/test_env_example.py` | 6 variables nuevas | el **usuario** las añade a `.env.example` |
| Tests con `postal_code` no numérico o de otra longitud | RF-1 | se buscan y se ajustan; las aserciones de `422` existentes siguen valiendo |
| `tests/services/test_product_service.py` (todo el fichero) | el servicio recibe `RegionService` y `RegionSessions`; la clave de cache pasa de `5` a la región | un `RegionService` falso que devuelve una región fija `"5"` mantiene las claves `search:5:…` de los tests existentes |
| `tests/scrapers/test_alcampo_search.py` | `search(term, client=…)` | se pasa el cliente del test |
| Integración (specs 001–008) | cada búsqueda necesita resolver la región | `mock_alcampo_search` se complementa con un `mock_region_chain` que simula la cadena para `28001` → región `5`; los tests que cuentan llamadas a la búsqueda no cambian |
| Tests de `create_redis` | nueva firma con timeout | ajuste de llamada |

## 7. Estrategia de test

**U** = unitario, **I** = integración (app real + `lifespan` + fakeredis + respx). Fixtures sintéticas nuevas para el HTML de `GET /` (con CSRF y `visitorId` sintéticos), el paso 4, el 6, el 7 y `NOT_DELIVERABLE`; las de los pasos 1, 2, 3 y 5 ya existen.

| RF | Test | Tipo |
|---|---|---|
| RF-1 | `2800`, `abcde`, `280011`, ` 28001 ` (recortado → válido) | U + I |
| RF-2 | cadena completa con respx → `Region` correcta; cada paso envía CSRF y `visitorId` donde toca | U |
| RF-3 | segunda búsqueda del mismo CP → 0 peticiones de cadena; TTL 7 d; registro `region:` guardado | U + I |
| RF-4 | `[]` en el paso 1 y `NOT_DELIVERABLE` → `404` con el `detail` fijo; cache negativa 1 h; 0 creaciones de destino | U + I |
| RF-5 | HTML sin CSRF, paso 4 sin string, paso 7 sin `regionId`, `GET /` con otra región → `502`, `ERROR` con el paso, nada cacheado | U |
| RF-6 | challenge en el paso 4 → enfriamiento activado, `502`; cada paso cuenta en el límite global | I |
| RF-7 | 10 búsquedas simultáneas del mismo CP nuevo → 1 cadena | U |
| RF-8 | límite de resoluciones agotado → `502` + `WARNING` sin llegar al paso 4; CPs ya resueltos siguen `200`; CP inexistente no consume cupo | U + I |
| RF-9 | dos CPs de la misma región → una sola confirmación de sesión; búsquedas con el cliente de esa sesión | U |
| RF-10 | `GET /` tras confirmar con otra región → `502`, sesión descartada, ninguna búsqueda sale | U |
| RF-11 | sesión con más de 50 min → renovación con el destino guardado (0 creaciones); destino que da `4xx` → región olvidada, `502` | U |
| RF-12, RF-13 | `28001` (región 5) y `35001` (región 32) → claves `search:5:agua` y `search:32:agua`, `warehouse` distinto | I |
| RF-14 | `create_redis` configura ambos timeouts | U |
| RF-15 | Redis que lanza `ConnectionError`/`TimeoutError` en todo → búsqueda `200` sin cache con `WARNING`; límites y enfriamiento siguen actuando con su respaldo local | U + I |
| RF-16 | `/health` sin cambios (test de la 005) | I |
| RF-17 | ningún log contiene los valores sintéticos de CSRF, `visitorId` ni cookies, en ningún nivel | I |

## 8. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | El HTML de `GET /` cambia de forma | No se puede confirmar ninguna sesión: todas las búsquedas `502` | Extracción acotada con error claro (RF-5), `ERROR` con el paso; fixture con el HTML real mínimo; es el mismo riesgo que ya asumía la Fase 0 |
| R2 | El destino temporal caduca antes que el registro de región (7 d) | Renovaciones fallidas | D6: se olvida la región y se re-resuelve; se anota como hallazgo la primera vez que ocurra |
| R3 | La hipótesis del umbral del WAF para crear destinos es errónea | Bloqueos al resolver | Límite estricto de 2 / 10 min (configurable); CPs de regiones conocidas no crean sesión nueva |
| R4 | Muchos CPs nuevos a la vez (arranque en frío) | `502` hasta que se resuelven | Aceptado (spec-D4). Tras unas horas la mayoría de CPs reales están en cache 7 días |
| R5 | Coste de `GET /` (HTML grande) en cada confirmación | Latencia en renovaciones | Solo cada 50 min por región |
| R6 | Tamaño del plan | Revisión difícil | 3 PRs (D12) |

## 9. Secuencia de implementación

| # | Tarea | PR |
|---|---|---|
| 1 | `Settings`: 6 variables (+ `.env.example` a mano) | 1 |
| 2 | Timeouts de Redis en `create_redis` | 1 |
| 3 | Limitador y enfriamiento con respaldo local ante `RedisError` | 1 |
| 4 | Cache de búsqueda que se salta sin Redis + integración "Redis caído → `200`" | 1 |
| 5 | `PostalCode` de 5 dígitos | 2a |
| 6 | `PostalCodeNotServedError` y handler `404` | 2a |
| 7 | `AlcampoSessionClient`: `GET /`, extracción SSR y pasos 1–7 con modelos | 2a |
| 8 | `RegionRepository` (Redis + L1, negativos) | 2b |
| 9 | `RegionService` (cache, agrupación, límite antes del paso 4, cadena) | 2b |
| 10 | `RegionSessions` (confirmar, comprobar región, renovar, destino caducado) | 3a |
| 11 | Búsqueda con la sesión de la región; cache y agrupación por `retailerRegionId`; `warehouse` real | 3a |
| 12 | Integración transversal (dos regiones, límite, challenge en la cadena, logs sin secretos) | 3b |
| 13 | Docs y verificación manual con `docker compose` | 3b |

| Bloque | `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| PR 1 (T1–T4) | ~120 | ~200 | — | ~320 |
| PR 2a (T5–T7) | ~140 | ~200 | — | ~340 |
| PR 2b (T8–T9) | ~120 | ~180 | — | ~300 |
| PR 3 (T10–T13) | ~180 | ~300 | ~60 | ~540 |
| **Total** | ~560 | ~880 | ~60 | **~1.500** |

Partido el PR 2 al aprobar el plan. El PR 3 sigue rozando el límite (~540) por los tests de integración; si al llegar a T12 supera las 400 líneas, se avisa antes de seguir.

## 10. Qué no cambia

El contrato de la respuesta (solo cambian los valores de `warehouse` y aparece el `404`), la autenticación, el formato de los logs, las protecciones de la 002 y la 008 (se reutilizan) y `/health`.
