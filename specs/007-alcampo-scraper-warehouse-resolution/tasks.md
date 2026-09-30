# Tasks 007 — Resolución de región por código postal

- **Estado:** completada (2026-09-30): T1–T14 (T14 por enmienda, RF-18)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones citadas como plan-Dn)
- **Entrega:** 5 PRs encadenados (plan-D12): **PR 1** = T1–T4 · **PR 2a** = T5–T7 · **PR 2b** = T8–T9 · **PR 3a** = T10–T11 · **PR 3b** = T12–T13 (PR 3 partido el 2026-09-30 al superar las 400 líneas).

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**. Si el código ausente cuelga el test (como en la 008 T6), se añade antes una red de seguridad con `asyncio.timeout`.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .` y `pytest -q`, todo limpio (**suite completa**). Marcar `[x]`, proponer el commit y **parar**.
5. **Regresiones:** se corrigen en la tarea que las provoca (plan §6). Una aserción existente solo cambia si se justifica aquí, una a una.
6. **Sin red ni tiempo real:** `respx`, `fakeredis`, relojes inyectables y `asyncio.Event`. **Fixtures sintéticas** para todo valor que en Alcampo sea un token (CSRF, `visitorId`, cookies): constitución #12.
7. **`.env.example`:** lo edita el usuario a mano. Cuando una tarea lo necesite, se para y se le pide.
8. **Secretos en logs:** toda tarea que registre algo de la cadena comprueba que los valores sintéticos de CSRF, `visitorId` y cookies no aparecen en `caplog.text`.

Formato de commit: `<tipo>(007-alcampo-scraper-warehouse-resolution): <descripción en inglés> (Tn)`.

---

## PR 1 — Redis: timeouts y degradación

### [x] T1 — Variables nuevas en `Settings`
> **Nota (2026-09-28):** RED real (9 tests: `AttributeError` por los campos inexistentes y `DID NOT RAISE` en las validaciones). `redis_timeout_seconds` es `float` (admite fracciones de segundo, como `search_timeout_seconds`); el resto, enteros. **`.env.example`:** el usuario añadió las 6 variables a mano (regla global de permisos); el test de sincronización lo confirma.
- **RED:** `tests/core/test_config.py`: valores por defecto (`redis_timeout_seconds == 2`, `region_cache_ttl_seconds == 604800`, `region_negative_cache_ttl_seconds == 3600`, `region_resolution_limit == 2`, `region_resolution_window_seconds == 600`, `session_max_age_seconds == 3000`); `REDIS_TIMEOUT_SECONDS=0` → `ValidationError`; `REGION_RESOLUTION_LIMIT=0` válido (desactiva), `-1` no; ventanas, TTLs y edad máxima `< 1` → `ValidationError`. Añadir las 6 a `OPTIONAL`.
- **GREEN:** los 6 campos con `Field`.
- **Regresión:** `test_env_example.py` → **parar y pedir al usuario** las 6 variables en `.env.example`.
- **Depende:** —
- **RF:** RF-3, RF-4, RF-8, RF-11, RF-14 (configuración)

### [x] T2 — Timeouts del cliente Redis
> **Nota (2026-09-28):** RED real (`TypeError` por `timeout_seconds` en `create_redis` y en el espía del `lifespan`). No existía ningún test de `create_redis`: se crean `tests/test_main.py` (unitario, sin conectar) y `tests/integration/test_redis_client.py` (el `lifespan` pasa `REDIS_TIMEOUT_SECONDS`). Regresión prevista: los dos parches de `create_redis` (`conftest.py` y `test_health.py`) aceptan ahora `**_`.
- **RED:** `tests/test_main.py` (o donde se pruebe `create_redis`): `create_redis(url, timeout_seconds=2)` deja `socket_timeout == 2` y `socket_connect_timeout == 2` en `connection_pool.connection_kwargs`. Integración: el `lifespan` pasa `REDIS_TIMEOUT_SECONDS` (se comprueba con un `create_redis` espía).
- **GREEN:** firma nueva de `create_redis` y llamada desde el `lifespan` (plan-D10).
- **Regresión:** el `create_redis` parcheado de `tests/integration/conftest.py` y el de `test_health.py` aceptan el nuevo argumento.
- **Depende:** T1
- **RF:** RF-14

### [x] T3 — Límites y enfriamiento con respaldo local
> **Nota (2026-09-28):** RED real (`ImportError` de `LocalRateLimiter` y `LocalCooldown`). Doble compartido `tests/services/redis_doubles.py` (`BrokenRedis` con `ConnectionError` "caído" y `TimeoutError` "colgado"). `LocalRateLimiter` usa el mismo borde que Redis (una entrada con antigüedad exactamente `window` sale); un rechazo no consume cupo. `LocalCooldown` reutiliza la función `_grow` de Redis, así que la secuencia 180 → 360 → 720 → 900 es la misma; "reciente" = menos de `max_seconds` desde el último, igual que el TTL de `waf:cooldown:last`. Los respaldos se crean en el `lifespan` y `get_product_service` los pasa; si no se pasan, cada objeto crea uno privado (útil en tests). **Límite conocido:** si Redis cae con un enfriamiento ya activo **en Redis**, el respaldo local no lo conoce; ese proceso puede volver a llamar a Alcampo hasta el siguiente challenge, que activa el enfriamiento local. Aceptado como parte de la degradación. El `StarletteDeprecationWarning` sigue siendo el único warning de la suite. Fallo de herramienta durante GREEN: un heredoc de bash muy largo no se aplicó (error de sintaxis, ningún fichero tocado); se reescribió como script en el scratchpad.
- **RED:**
  - `tests/services/test_rate_limiter.py`: con un Redis que lanza `redis.exceptions.ConnectionError` (y otro con `TimeoutError`), `acquire()` usa el limitador local: límite 2 → dos pasan y el tercero lanza `OutboundRateLimitedError`; `WARNING "redis unavailable op=…"`. El respaldo local es un objeto compartido: dos `OutboundRateLimiter` con el mismo respaldo comparten cupo.
  - Clave configurable: dos limitadores con claves distintas no comparten cupo (lo necesita T9).
  - `tests/services/test_waf_cooldown.py`: con Redis caído, `activate` y `is_active` usan el marcador local con la misma secuencia 180 → 360 → …; `WARNING`.
- **GREEN:** `LocalRateLimiter` (ventana deslizante con `deque` y reloj inyectable) y `LocalCooldown` (reloj monotónico inyectable); los repositorios reciben su respaldo y capturan `RedisError` (plan-D10). Respaldos creados en el `lifespan` (`app.state`) y pasados por `get_product_service`.
- **Depende:** T1
- **RF:** RF-15

### [x] T4 — Cache que se salta sin Redis
> **Nota (2026-09-28):** RED real: con Redis caído o colgado la búsqueda daba `500` y el repositorio dejaba pasar `ConnectionError`/`TimeoutError`. GREEN: `get` y `set` capturan `RedisError` con un `WARNING` cada uno. La integración comprueba `200` con productos, 1 petición a Alcampo, `/health` `200`, el `WARNING` y **ningún** `ERROR`. El caso "colgado" se simula con un `TimeoutError` inmediato: que el cliente real corte a los `REDIS_TIMEOUT_SECONDS` lo garantiza T2 y se comprueba de verdad en la verificación manual de T13. **Fin del PR 1.**
- **RED:**
  - `tests/services/test_search_cache.py`: con Redis caído, `get` → `None` y `set` no lanza; un `WARNING` por operación.
  - Integración (`tests/integration/test_redis_degradation.py`): con un Redis que falla en todo, `GET /api/v1/products` → `200` con productos (Alcampo con respx), `WARNING` en el log y **ningún `500`**; con un Redis que tarda más que `REDIS_TIMEOUT_SECONDS`, simulado con un cliente que lanza `TimeoutError`, lo mismo. `/health` → `200`.
- **GREEN:** `SearchCacheRepository` captura `RedisError` (plan-D10).
- **Depende:** T3
- **RF:** RF-15, RF-16. **Fin del PR 1.**

---

## PR 2a — Validación, `404` y cliente de la cadena

### [x] T5 — `postal_code` de 5 dígitos
> **Nota (2026-09-28):** RED real (7 fallos: los 6 formatos inválidos pasaban la validación y la integración daba `200`). Caso añadido: **dígitos de ancho completo** (U+FF10..U+FF19), que `\d` aceptaría por ser dígitos Unicode; el patrón usa `[0-9]`. Como ruff (`RUF001`/`RUF003`) marca esos caracteres como ambiguos, el test los construye con `chr()` y el código fuente queda en ASCII. Sin regresiones: todos los códigos postales de los tests existentes (`28001`, `08001`) ya eran válidos.
- **RED:** `tests/models/…` y `tests/api/test_products_route.py`: `2800`, `abcde`, `280011`, `28 01` → `422`; ` 28001 ` → válido (`"28001"`). Integración: `422` sin tocar Redis ni Alcampo.
- **GREEN:** `PostalCode` con `pattern=r"^[0-9]{5}$"` (plan-D7).
- **Regresión:** buscar tests que usen códigos postales no válidos y ajustarlos, anotándolo.
- **Depende:** —
- **RF:** RF-1

### [x] T6 — `404` para código postal sin servicio
> **Nota (2026-09-30):** RED real (`ImportError` de `PostalCodeNotServedError`). La excepción guarda `postal_code` para el log; el handler registra `INFO "postal code not served postal_code=%r"` (no es un fallo) y responde `404 {"detail": "Postal code not served by Alcampo"}` con `X-Request-ID` (lo pone el middleware). Todavía nadie la lanza: llega en T9 con `RegionService`.
- **RED:** `tests/test_exceptions.py`: `PostalCodeNotServedError` es `AlcampoScraperError` y **no** `UpstreamUnavailableError`. `tests/api/test_products_route.py`: un servicio que la lanza → `404 {"detail": "Postal code not served by Alcampo"}`, con `X-Request-ID` y un `INFO` (no `ERROR`).
- **GREEN:** excepción y handler en `main.py` (plan-D7).
- **Depende:** —
- **RF:** RF-4 (respuesta)

### [x] T7 — `AlcampoSessionClient`
> **Nota (2026-09-30):** RED real (`ModuleNotFoundError`). 20 tests con fixtures **sintéticas** nuevas (`alcampo_home_vaguada.html`, `alcampo_home_telde.html`, `alcampo_session_proposition.json`, `alcampo_session_active.json`, `alcampo_deliverability_not_deliverable.json`); en Telde el `retailerRegionId` va sin comillas para cubrir las dos formas. **Concreción de plan-D2:** solo se reintentan los `GET`; los `PUT`/`POST` van con un único intento, porque repetir "crear destino" tras un `5xx` podría crear dos (el paso sensible al WAF). Siguen pasando por el límite global y la detección del challenge. `HomeState` oculta CSRF y `visitorId` del `repr`; los errores de forma registran el paso (`step='…'`) y el recuento de coincidencias, nunca valores ni cuerpos. **Mutaciones:** reintentar también escrituras → falla `test_writes_are_never_retried`; `repr=True` en el CSRF → falla el test de `open`. Restaurado. Corrección durante GREEN: `_send` tenía `**kwargs: object` con un `# type: ignore` y un parámetro sin usar; se sustituyó por parámetros explícitos (constitución #4). **Fin del PR 2a.**
- **RED:** `tests/scrapers/test_alcampo_session.py` con respx y fixtures **sintéticas** nuevas (`alcampo_home_vaguada.html`, `alcampo_home_telde.html` mínimos con el fragmento SSR; paso 4 como string; pasos 6 y 7 con la forma observada en plan §2; `alcampo_deliverability_not_deliverable.json`):
  - `open()` extrae CSRF, `visitorId`, `regionId` y `retailerRegionId`; HTML sin alguno de ellos, o con dos valores distintos → `UpstreamUnavailableError` con el paso en el motivo;
  - pasos 1–7: método, ruta, cuerpo y cabeceras (`X-CSRF-Token`, `visitorid`, `visitor-id`, `customer-id: ""` en el 7) exactos; respuestas parseadas con modelos Pydantic;
  - paso 1 con `[]` → `None` (no existe); paso 3 `NOT_DELIVERABLE` → valor devuelto tal cual;
  - cada petición pasa por el limitador global (límite agotado → `OutboundRateLimitedError` sin petición) y un challenge → `UpstreamBlockedError`;
  - ningún log contiene los valores sintéticos de CSRF ni `visitorId` (regla 8).
- **GREEN:** `app/scrapers/alcampo_session.py` y modelos en `app/models/alcampo.py` (plan-D1, D2).
- **Depende:** —
- **RF:** RF-2, RF-5, RF-6, RF-17. **Fin del PR 2a.**

---

## PR 2b — Regiones

### [x] T8 — `RegionRepository`
> **Nota (2026-09-30):** RED real (`ModuleNotFoundError`). La L1 es un objeto aparte, `RegionMemory`, que se creará en el `lifespan` en T9 (el repositorio es por petición: una L1 propia no recordaría nada). Al rellenarse desde Redis usa el TTL **restante** de la clave, así que la memoria nunca dura más que el registro. `NOT_SERVED` es una constante: no choca con los ids de región, que son uuid. Un registro de región corrupto se trata como ausente, con `WARNING`. 11 tests, incluidos Redis caído y colgado y la caducidad de la L1 con reloj falso.
- **RED:** `tests/services/test_region_repository.py`: guardar y leer `postal-code-region:{cp}` (TTL 7 d) y `region:{regionId}` (JSON, TTL 7 d); negativo `NOT_SERVED` (TTL 1 h); L1 en memoria: una segunda lectura no toca Redis; con Redis caído, solo L1 y `WARNING`; `forget_region` borra el registro de región de Redis y de L1.
- **GREEN:** `app/services/region_repository.py`.
- **Depende:** T3
- **RF:** RF-3, RF-4 (cache), RF-15

### [x] T9 — `RegionService`
> **Nota (2026-09-30):** RED real (`ImportError` de `RegionResolutionLimitedError` y del módulo). **Concreción de plan-D4/D5:** la sesión que resuelve una región **nueva** se entrega a `RegionSessions.adopt(region, destino, sesión)`, que la confirma (pasos 6–7 + comprobación) y se queda con ella; así se ahorra un `GET /`. Si la región ya era conocida, la sesión se cierra. El servicio depende de protocolos (`ChainSession`, `SessionRegistry`, `ResolutionLimiter`); `adopt` real llega en T10. Casos añadidos al plan: una región **olvidada** (destino caducado, plan-D6) con el CP aún en cache se vuelve a resolver; la sesión se cierra en todos los caminos de error. El límite de resoluciones se convierte en `RegionResolutionLimitedError` (familia `UpstreamThrottledError`: `WARNING` y `502`). Test de tiempo máximo con red de seguridad de 2 s. Corregido antes de GREEN: dos tests parcheaban el atributo privado `_new_session`; ahora el doble tiene una "puerta" (`Harness.gate`). **Fin del PR 2b.**
- **RED:** `tests/services/test_region_service.py` con un `AlcampoSessionClient` falso y un `RegionSessions` falso:
  - CP en cache → región sin ninguna petición; `INFO source=cache`;
  - CP nuevo en región nueva → pasos 0–5, límite de resoluciones adquirido **una vez, justo antes del paso 4**, confirmación de sesión, registros guardados; `source=resolved`;
  - CP nuevo en región conocida → pasos 0–5 sin confirmación nueva (usa el `retailerRegionId` guardado);
  - paso 1 `[]` y `NOT_DELIVERABLE` → `PostalCodeNotServedError`, cache negativa, **límite no consumido**, sin paso 4;
  - límite de resoluciones agotado → `RegionResolutionLimitedError` (`UpstreamThrottledError`), sin paso 4, y un CP ya resuelto sigue funcionando;
  - 10 llamadas simultáneas con el mismo CP nuevo → 1 cadena; `source=shared` para 9;
  - challenge en un paso → `UpstreamBlockedError` propagado y nada cacheado;
  - resolución que supera el tiempo máximo → `UpstreamUnavailableError("region resolution timeout")`.
- **GREEN:** `app/services/region_service.py`; `OutboundRateLimiter` con la clave `ratelimit:alcampo:region-resolutions` (plan-D3, D4, D9).
- **Depende:** T7, T8
- **RF:** RF-2…RF-8. **Fin del PR 2b.**

---

## PR 3a — Sesiones y búsqueda por región (T10–T11) · PR 3b — Integración, docs y verificación (T12–T13)

### [x] T10 — `RegionSessions`
> **Nota (2026-09-30):** RED real (`ModuleNotFoundError` y 3 × `AttributeError: status_code`). **Cambio fuera del plan, necesario para plan-D6:** `UpstreamUnavailableError` acepta un `status_code` opcional que solo rellena la rama "4xx no reintentable" de `retry.py`; así `RegionSessions` distingue "Alcampo rechaza el destino guardado" (olvidar la región) de un fallo pasajero (mantenerla), sin comparar textos. No se envía al cliente. **API:** `adopt(región, destino, sesión)` confirma una sesión ya abierta (la que resolvió la región, T9) y `get(región)` devuelve una sesión confirmada, renovándola si no existe o tiene más de 50 min (`open` → `propose` → `activate` → `open` de comprobación: ningún destino nuevo, ni el límite de resoluciones). Comprobación RF-10: `activate` **y** el HTML deben mostrar la región esperada; si no, `502 "region not confirmed"` y la sesión se cierra. Las sesiones sustituidas se retiran y se cierran en la renovación siguiente (una búsqueda podía estar usándolas) o al cerrar el `lifespan`. Corregido antes de GREEN: una primera versión cerraba las retiradas con tareas sin esperar (asyncio puede recogerlas sin terminar); ahora se esperan.
- **RED:** `tests/services/test_region_sessions.py` con reloj falso y un `AlcampoSessionClient` falso:
  - `confirm` → sesión nueva (`open`, pasos 6–7, `GET /`) y `retailerRegionId`; `INFO reason=new`;
  - `GET /` tras confirmar con **otra** región → `UpstreamUnavailableError("region not confirmed")`, cliente cerrado, ninguna sesión guardada;
  - `get` con sesión de menos de 50 min → la misma, sin peticiones;
  - `get` con sesión de más de 50 min → renovación con el destino guardado, **sin crear destino** y **sin** consumir el límite de resoluciones; `reason=renewal`; renovaciones simultáneas de la misma región → 1;
  - `proposition` o `active` con `4xx` en una renovación → `RegionRepository.forget_region` y `502`;
  - al cerrar el `lifespan`, todos los clientes de sesión se cierran.
- **GREEN:** `app/services/region_sessions.py` (plan-D5, D6).
- **Depende:** T9
- **RF:** RF-9, RF-10, RF-11

### [x] T11 — Búsqueda con la región real
> **Nota (2026-09-30):** RED real (53 fallos por las firmas nuevas). **Hueco del plan cubierto:** resolver un CP nuevo es tráfico a Alcampo, así que `RegionService` comprueba el enfriamiento antes de abrir la cadena (`CooldownActiveError`), y `ProductService` activa el enfriamiento si la resolución recibe un challenge (RF-6); test nuevo en `test_region_service.py` y en `test_product_service.py`. **Cableado:** todo lo que tiene estado se construye una vez en el `lifespan` (limitador global, enfriamiento, repositorio y servicio de regiones, `RegionSessions`); `get_product_service` solo lo junta. Desaparece el cliente HTTP global (cada sesión de región tiene el suyo) y `DEFAULT_WAREHOUSE` con su test. La renovación de sesión va bajo su propio tiempo máximo (`"region session timeout"`). **Regresiones (plan §6):** `mock_alcampo_search` registra también `mock_region_chain` (28001 → región sintética con `retailerRegionId` `"5"`, así que las claves `search:5:…` no cambian). Tests tocados, uno a uno: (1) `test_alcampo_cookies_never_reach_the_logs` **pasaba en vacío** (la búsqueda nunca salía por la cadena sin mockear): cadena añadida y `assert route.called` para que no vuelva a pasar; (2) `test_waf_cooldown_end_to_end` y (3) `test_slow_alcampo…` registran la búsqueda a mano: cadena añadida; (4) `test_slow_alcampo…`: tiempo máximo de 0,1 → 1,5 s y Alcampo lento de 2 → 5 s (crear el cliente de una sesión cuesta ~0,4 s por el contexto TLS), con `elapsed < 4`; (5) `test_exhausted_rate_limit…`: la cadena también consume cupo, así que se siembran región, cache de `leche` y el único hueco; `route.call_count` pasa de 1 a 0 porque ya no hay primera búsqueda real; la intención (límite agotado → `502`, cache → `200`) se mantiene. Corregido durante GREEN: un esqueleto de `_within_timeout` con sintaxis genérica de 3.12 (no compila en 3.11, la versión mínima y la de la imagen). **Tamaño:** el PR 3 lleva ~1.000 líneas (T10 + T11: 348 en `app/`, 549 en tests), por encima de las 400 del plan §9 → aviso al usuario, que aprobó partirlo: **fin del PR 3a**.
- **RED:**
  - `tests/scrapers/test_alcampo_search.py`: `search(term, client=…)` usa el cliente recibido (sus cookies van en la petición).
  - `tests/services/test_product_service.py`: la clave de cache y la de agrupación usan el `retailerRegionId`; `search.warehouse` es el real; dos regiones no comparten cache; un `PostalCodeNotServedError` se propaga.
- **GREEN:** servicio con `RegionService` y `RegionSessions`; `DEFAULT_WAREHOUSE` sale del flujo (plan-D8); `get_product_service` y el `lifespan` cablean todo.
- **Regresión (plan §6):** `make_service` usa un `RegionService` falso con región fija `"5"`, así que las claves `search:5:…` de los tests existentes no cambian; la integración de las specs 001–008 usa un nuevo `mock_region_chain` en `tests/integration/conftest.py` (28001 → región 5). Se anota cada test tocado.
- **Depende:** T10
- **RF:** RF-9, RF-12, RF-13

### [x] T12 — Integración transversal
> **Nota (2026-09-30):** 6 tests (8 casos) en `tests/integration/test_region_resolution.py` con un **Alcampo falso con estado** (`FakeAlcampo`): una región por cookie de sesión, `activate` la mueve, el HTML muestra la de cada sesión y cada búsqueda anota la región de la sesión con la que llegó. Así se comprueba que `28001` y `35001` se buscan **cada uno en su región** (no solo que `warehouse` sea distinto). **Hueco encontrado y corregido (GREEN):** el fake generaba el JSON con espacios (`"csrf": {"token": …}`) y la extracción del HTML exigía la forma compacta, así que todo daba `502`. El HTML real es compacto (la verificación en vivo lo extrajo), pero un cambio de serializador en Alcampo habría tumbado todas las confirmaciones de sesión (riesgo R1): las expresiones admiten ahora espacios, con un test unitario nuevo en `test_alcampo_session.py` (RED → GREEN). **Mutaciones:** sin la comprobación de región tras confirmar → falla `test_a_session_that_lands_in_another_region_is_never_used` (solo el unitario: el fake siempre deja la sesión en la región pedida); límite de resoluciones al principio de la cadena → fallan los tests de T9 de orden y de "no consume cupo". Restaurado. El límite global se desactiva en estos tests (`ALCAMPO_RATE_LIMIT=0`): lo cubre la 008 y aquí se cuentan llamadas de la cadena.
- **RED:** `tests/integration/test_region_resolution.py`:
  - `28001` (región `5`) y `35001` (región `32`) → `warehouse` distinto y claves `search:5:agua` y `search:32:agua`;
  - repetir `35001` → 0 peticiones de cadena; otro CP de la región 32 → pasos 0–5, sin confirmación nueva;
  - `99999` → `404`, repetido → sin peticiones; `51001` (`NOT_DELIVERABLE`) → `404`;
  - límite de resoluciones agotado → `502` + `WARNING`, y `28001` ya resuelto → `200`;
  - challenge en el paso 4 → `502` y enfriamiento activo;
  - con `LOG_LEVEL=DEBUG`, ningún valor sintético de CSRF, `visitorId` ni cookies aparece en `caplog.text` (RF-17).
- **Verificación por mutación:** quitar la comprobación de región tras confirmar → el test de "otra región → `502`" (T10) falla; adquirir el límite al principio de la cadena en vez de antes del paso 4 → el test de "CP inexistente no consume cupo" (T9) falla. Anotarlo.
- **Depende:** T11
- **RF:** transversal. Primera tarea del **PR 3b**.

### [x] T13 — Docs y verificación manual
> **Verificación manual (2026-09-30T09:52–10:02Z):** Docker 29.6.2, override con token sintético en el scratchpad, >40 h desde la última petición a Alcampo. **25 peticiones reales, 2 creaciones de destino, ningún challenge.**
> 1. `28001` + `agua` → `200`, **`warehouse: "11"` (Moratalaz, como en la Fase 0)**, 10 peticiones en 3,3 s, `region session confirmed … reason=new`. A los 5 min, `35001` + `agua` → `200`, **`warehouse: "32"` (Telde)**, 10 peticiones en 1,2 s. `agua`: 44 productos comunes, **41 con precio distinto** (p. ej. 2,22 € Moratalaz frente a 3,06 € Telde), 5 exclusivos en cada región.
> 2. `35001` + `leche` → `region resolved … source=cache`, solo 1 petición (la búsqueda).
> 3. `99999` → `404 "Postal code not served by Alcampo"` con 2 peticiones (portada y áreas); `2800` → `422` (`string_pattern_mismatch`) sin tocar nada.
> 4. **Redis colgado** (`docker compose pause redis`): `28001` + `pan` → `200` en **9,1 s**, 1 petición a Alcampo, `WARNING redis unavailable op=…` por cada operación. **Redis parado** (`stop`): `28001` + `arroz` → `200` en **9,0 s**. `/health` → `200` en ~8 ms en ambos casos. Redis vuelto a arrancar.
> 5. Logs: 0 apariciones del token, del CSRF, de `visitorId` y de `Set-Cookie`; 0 líneas de access log. `docker compose down`; 0 contenedores; override, token, scripts y precios borrados del scratchpad.
>
> **Hallazgos (no corregidos: fuera del plan, se avisa):**
> - **Latencia sin Redis ≈ 9 s por búsqueda:** cada una de las ~4 operaciones con Redis espera su timeout de 2 s. RF-14/RF-15 se cumplen (sin cuelgues, `200`), pero es lento. Mejora propuesta: un *circuit breaker* que deje de intentar Redis durante unos segundos tras un fallo.
> - **`ERROR asyncio: Future exception was never retrieved` (×3) con Redis parado:** el nombre `redis` deja de resolverse en la red de Docker; la consulta DNS (`getaddrinfo`, en un hilo) tarda más que el timeout; redis-py cancela la conexión, pero la consulta termina después con `gaierror` y nadie recoge su resultado. No afecta a la respuesta, pero ensucia los logs con falsos `ERROR`. El mismo *circuit breaker* lo reduciría (menos intentos).
>
> **Docs (hechas antes):** README hecho (sección "Región por código postal", sección "Redis", `422`/`404`, 6 variables, logs nuevos, 5 limitaciones nuevas, "Estado" y el ejemplo de respuesta: `28001` → `warehouse: "11"`, Moratalaz según la Fase 0, no `"5"`). **Pendiente:** la verificación manual, porque el daemon de Docker está apagado. **Observación para la verificación:** la app envía las ~8 peticiones de una resolución seguidas, en pocos segundos (en la verificación en vivo se espaciaron 30 s a mano); se documenta como limitación y las dos resoluciones reales se separarán varios minutos.
- **GREEN:** README: sección de región por código postal (qué se resuelve, `404`, `422`, cache de 7 días, límite de resoluciones, sesión por región renovada cada 50 min), sección Redis (timeouts y degradación), variables nuevas, logs nuevos, limitaciones (vida del destino temporal no verificada; umbral del WAF para crear destinos es una hipótesis; `warehouse` es el `retailerRegionId`); "Estado".
- **Verificación manual** con `docker compose` y token sintético, ≥10 min desde la última petición a Alcampo, 30 s entre peticiones reales y **como mucho 2 creaciones de destino**:
  1. `28001` y `35001` con `agua` → `warehouse` distinto y precios distintos (p. ej. Bezoya o Font Vella).
  2. Repetir `35001` → sin peticiones de cadena en los logs.
  3. `99999` → `404`; `2800` → `422`.
  4. Parar el contenedor de Redis → una búsqueda cacheada en L1 de región y ya resuelta responde `200` sin cache con `WARNING`; `/health` `200`; volver a arrancar Redis.
  5. Logs sin CSRF, `visitorId`, cookies ni token. `docker compose down`, borrar el override.
- **Depende:** T12
- **RF:** RNF-5. **Fin del PR 3b.**

---

## PR 3c — Enmienda: circuit breaker de Redis (T14)

### [x] T14 — Circuit breaker de Redis
> **Nota (2026-09-30):** RED real (`ModuleNotFoundError` y, para la integración, ejecutada con `REDIS_CIRCUIT_OPEN_SECONDS=0`: **19** intentos contra Redis en una sola búsqueda, frente a **1** con el circuito para dos búsquedas). `RedisCircuitBreaker.call(op)` y `redis_unavailable(logger, op, exc)` (`WARNING` si es un fallo real, `DEBUG` si es el circuito abierto). Los 4 repositorios pasan sus operaciones por el circuito; sin circuito usan uno desactivado (`open_seconds=0`), así que los tests anteriores no cambiaron: **ningún recuento de `WARNING` de T3, T4 y T8 tuvo que ajustarse**. `.env.example`: el usuario añadió `REDIS_CIRCUIT_OPEN_SECONDS` a mano. Simplificación aceptada: con el circuito medio abierto, varias operaciones concurrentes pueden sondear a la vez (cada una acotada por el timeout).
>
> **Verificación manual (2026-09-30T11:42–11:44Z)**, Docker 29.6.2, token sintético. **Desviación aprobada por el usuario:** el Redis del compose era nuevo, así que hubo que resolver `28001` otra vez (**1 creación de destino**, ~1,5 h después de la anterior). 13 peticiones reales, ningún challenge.
> 1. `28001` + `agua` con Redis → `200`, `warehouse: "11"`, 3,0 s.
> 2. `docker compose stop redis`; `28001` + `pan` → `200` en **1,1 s** (T13: 9,0 s), `WARNING redis circuit open for 10s after ConnectionError` + un único `WARNING redis unavailable op=cache.get`; `28001` + `arroz` 2 s después → `200` en **0,86 s**, sin ninguna línea de Redis. `/health` → `200` en 10 ms.
> 3. `docker compose start redis` y 12 s de espera: `28001` + `leche` → `200`, `INFO redis circuit closed: redis answered again`, y `search:11:leche` en Redis.
> 4. Logs: **0** `Future exception was never retrieved` (T13: 3 por búsqueda), 0 apariciones del token. Todo parado y borrado del scratchpad.
>
> **Matiz:** esta vez el primer fallo fue un `ConnectionError` inmediato, no la espera de DNS de T13 (depende de la red de Docker en cada momento). Si el DNS tarda, la primera operación sí esperará sus 2 s, y puede aparecer un `gaierror` de asyncio; pero solo **uno por ventana de 10 s**, no uno por operación.
- **RED:**
  - `tests/core/test_config.py`: `REDIS_CIRCUIT_OPEN_SECONDS` por defecto `10`; `0` válido (desactiva); `-1` → `ValidationError`. Añadirla a `OPTIONAL`.
  - `tests/services/test_redis_circuit.py` (reloj falso): cerrado → ejecuta; un `RedisError` → abre, `WARNING` una vez y relanza; abierto → `RedisCircuitOpenError` sin ejecutar la operación; pasado el periodo → prueba: éxito cierra (`INFO`), fallo reabre; `0` → nunca abre.
  - Repositorios (cache, limitador, enfriamiento, regiones): con el circuito abierto, **la operación de Redis no se llama** (un Redis espía que cuenta llamadas) y se usa el respaldo; el aviso del repositorio va a `DEBUG`, no a `WARNING`.
  - Integración (`tests/integration/test_redis_degradation.py`): con un Redis que falla, **dos** búsquedas seguidas → la segunda no toca Redis (0 llamadas nuevas al doble) y solo hay **un** `WARNING` de apertura del circuito en total.
- **GREEN:** `app/services/redis_circuit.py`; los 4 repositorios pasan sus operaciones por el circuito; circuito creado en el `lifespan`.
- **Regresión:** `.env.example` → **parar y pedir al usuario** `REDIS_CIRCUIT_OPEN_SECONDS`. Los tests de T3, T4 y T8 que cuentan `WARNING "redis unavailable"` pueden cambiar de recuento (el circuito abre al primer fallo): se ajustan uno a uno, anotándolo.
- **Verificación manual:** `docker compose` con Redis parado: 2 búsquedas de términos no cacheados de una región ya conocida → la primera en ~2 s y la segunda sin esperas de Redis; 1 `WARNING` de apertura; menos `Future exception was never retrieved` que en T13 (anotar cuántos). Como mucho 2 búsquedas reales, sin creación de destinos.
- **Depende:** T13
- **RF:** RF-18. **Fin del PR 3c.**

---

## Trazabilidad RF → tareas

| RF | Tareas |
|---|---|
| RF-1 | T5, T13 |
| RF-2 | T7, T9 |
| RF-3 | T1, T8, T9, T12 |
| RF-4 | T1, T6, T8, T9, T12 |
| RF-5 | T7 |
| RF-6 | T7, T9, T12 |
| RF-7 | T9 |
| RF-8 | T1, T9, T12 |
| RF-9 | T10, T11 |
| RF-10 | T10 |
| RF-11 | T1, T10 |
| RF-12 | T11, T12 |
| RF-13 | T11, T12, T13 |
| RF-14 | T1, T2 |
| RF-15 | T3, T4, T8, T13 |
| RF-16 | T4 |
| RF-17 | T7, T12, T13 |
| RF-18 | T14 |

Ningún RF queda huérfano. RNF: RNF-1 (contrato: T6, T11, T12), RNF-2 (sin dependencias nuevas, en todas), RNF-3 (secretos: regla 8 y T12), RNF-4 (sin red: regla 6), RNF-5 (T1, T13), RNF-6 (4 PRs).
