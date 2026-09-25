# Plan 003 — Logging

- **Estado:** aprobado (2026-09-25). Entrega: 2 PRs encadenados (§10)
- **Fecha:** 2026-09-25
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D4**; las de specs anteriores como **001-plan-Dn**, **002-plan-Dn**. Las decisiones de diseño de este plan son **D1…**

## 1. Visión general

```
petición HTTP
  └─ RequestContextMiddleware  (app/middleware/request_context.py)
       ├─ request_id = uuid4().hex → ContextVar                       RF-3, RF-5, RF-5b
       ├─ INFO "request started"  method, path, query (%r)           RF-3, RF-18
       ├─ call_next ─► ExceptionMiddleware (handler 502) ─► ruta
       │     └─ excepción no controlada → ERROR + traceback → 500     RF-6
       ├─ X-Request-ID en la respuesta (también 422/500/502)          RF-5
       └─ INFO "request finished"  status, duración ms               RF-3

cualquier logger.* durante la petición
  └─ LogRecordFactory inyecta record.request_id desde el ContextVar  RF-4

handler 502 (main.py)
  ├─ CooldownActiveError   → WARNING                                  RF-12
  └─ resto de Upstream…    → ERROR reason + postal_code + term (%r)   RF-7

retry.py       WARNING por reintento · ERROR agotado · ERROR 4xx      RF-8…RF-10
service        ERROR challenge WAF + enfriamiento                     RF-11
scraper        ERROR JSON inválido / schema inesperado                RF-13
mapper         WARNING descartados · ERROR si todos                   RF-14, RF-15
search_cache   WARNING cache corrupta                                 RF-16
```

## 2. Verificaciones previas (hechas el 2026-09-25, antes de escribir este plan)

Dos supuestos del diseño de Mercadona **no valen aquí**, y lo he comprobado ejecutándolo:

| Supuesto | Experimento | Resultado |
|---|---|---|
| Un `@app.exception_handler(Exception)` basta para el `500` | App mínima con `BaseHTTPMiddleware` que fija un `ContextVar` y un handler de `Exception` | El handler corre en `ServerErrorMiddleware`, **fuera** del middleware: el request id vale `-` y el `500` sale **sin** `X-Request-ID`. Rompe RF-4 y RF-5 |
| Si el middleware captura la excepción, todo funciona | Misma app, con `try/except` en el middleware | Request id correcto, `X-Request-ID` presente, y el handler de `Exception` ni se ejecuta. El handler del `502` **sí** corre dentro del middleware |
| `logging.basicConfig(force=True)` es seguro | Test con `caplog` que luego llama a `basicConfig(force=True)` | **Borra el handler de `caplog`**: `caplog.text == ''`. Rompería todos los tests de logging |

Estas tres filas justifican D2, D3 y D4.

## 3. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | validador de `log_level`: normaliza a mayúsculas y rechaza niveles desconocidos | RF-2 |
| `app/core/logging.py` | **nuevo**: `request_id_var`, `configure_logging(level, *, stream)`, factoría de `LogRecord` | RF-1, RF-4 |
| `app/middleware/request_context.py` | **nuevo**: `RequestContextMiddleware` | RF-3, RF-5, RF-5b, RF-6, RF-18 |
| `app/main.py` | `configure_logging` en el `lifespan`; registra el middleware; el handler `502` registra según el tipo | RF-1, RF-7, RF-12 |
| `app/exceptions.py` | **nuevo** `CooldownActiveError(UpstreamUnavailableError)` | RF-7, RF-12 |
| `app/services/product_service.py` | lanza `CooldownActiveError`; registra el challenge y el enfriamiento | RF-11, RF-12 |
| `app/scrapers/retry.py` | parámetro `url`; `WARNING` por reintento; `ERROR` al agotar y en `4xx` | RF-8, RF-9, RF-10 |
| `app/scrapers/alcampo_search.py` | pasa `url` a `send_with_retry`; `ERROR` con cuerpo inválido | RF-13 |
| `app/mappers/product_mapper.py` | cuenta los descartes; `WARNING` o `ERROR` una vez por búsqueda | RF-14, RF-15 |
| `app/services/search_cache.py` | `WARNING` con cache corrupta | RF-16 |
| `README.md` | `LOG_LEVEL`, formato de línea, `X-Request-ID` | RNF-6 |

## 4. Modelo de datos

No hay modelos Pydantic nuevos. El único dato nuevo es el request id: un `str` de 32 caracteres hexadecimales que vive en un `ContextVar` y no se persiste.

```python
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
```

Ejemplo de línea:

```
2026-09-25 10:00:00,123 INFO [3f2a…c91e] app.middleware.request_context: request finished status=200 duration_ms=41.7
```

## 5. Decisiones de diseño

**D1 — `CooldownActiveError(UpstreamUnavailableError)` para distinguir el `502` del enfriamiento.** El handler de `502` elige el nivel según el tipo: `WARNING` para el enfriamiento (RF-12) y `ERROR` para el resto (RF-7). Al ser subclase, el contrato y el handler no cambian (mismo mecanismo que 002-plan-D1).
- *Descartada:* comparar `exc.reason == "WAF cooldown active"`. Es el mismo acoplamiento a un texto de log que ya se descartó en 002-plan-D1.
- *Descartada:* registrar el `WARNING` en el servicio y silenciar el handler. Repartiría la decisión "qué nivel tiene este `502`" en dos sitios.

**D2 — El `500` lo gestiona el middleware, no un `exception_handler(Exception)`.** El middleware envuelve `call_next` en `try/except Exception`: registra con `logger.exception` (traceback completo), responde `500 {"detail": "Internal server error"}` y le pone `X-Request-ID`. Así cumple RF-6 sin perder RF-4 ni RF-5 (verificación §2).
- *Descartada:* `@app.exception_handler(Exception)`, como en Mercadona. Comprobado que corre fuera del middleware, sin request id ni cabecera.
- Consecuencia: el "exception handler global" de la spec se materializa dentro del middleware. El comportamiento es el mismo que pide RF-6.

**D3 — `configure_logging` gestiona su propio handler, sin `basicConfig(force=True)`.** Añade un `StreamHandler` al logger raíz, marcado con un atributo propio. Si se vuelve a llamar (cada `lifespan` de los tests crea una app), sustituye **solo** ese handler y deja intactos los ajenos, como el de `caplog`. Fija el nivel del raíz a `LOG_LEVEL`.
- *Descartada:* `basicConfig()` sin `force`. Solo actúa la primera vez; un segundo `lifespan` con otro nivel no tendría efecto.
- *Descartada:* `basicConfig(force=True)`. Comprobado que borra el handler de `caplog` (§2).

**D4 — Request id en cada registro mediante una factoría de `LogRecord`, no un `logging.Filter`.** `configure_logging` envuelve `logging.getLogRecordFactory()` para que todo `LogRecord` nazca con `record.request_id = request_id_var.get()`. El envoltorio se instala una sola vez (marcado para no encadenarse).
- *Descartada:* un `Filter` en nuestro handler, como en Mercadona. Solo añade el atributo a los registros que pasan por **nuestro** handler; los que captura `caplog` no lo tendrían, y RF-4 no se podría testear sobre `caplog.records`.

**D5 — `BaseHTTPMiddleware` y no ASGI puro.** Con la verificación de §2, `BaseHTTPMiddleware` propaga el `ContextVar` al endpoint y a los handlers internos, captura las excepciones y permite poner la cabecera en `response.headers`. Es bastante más simple que un middleware ASGI que intercepte `send`.
- *Descartada:* ASGI puro. Más control, pero con el doble de código y sin ninguna necesidad que `BaseHTTPMiddleware` no cubra aquí (no hay streaming).

**D6 — El middleware es el más externo de la app.** Se registra el último con `add_middleware`, así que queda por fuera de cualquier middleware futuro (la autenticación de la 004 incluida). Así, un `401` también tendrá request id y líneas de inicio y fin.

**D7 — `url` explícito en `send_with_retry`.** Nuevo parámetro `url: str = "-"`, que el scraper rellena con `SEARCH_PATH` y el término. Los logs de reintentos no dependen de leer `response.request` ni `exc.request` de httpx.
- *Descartada:* sacar la URL de `exc.request`. En algunos `TransportError` de httpx acceder a `.request` lanza `RuntimeError` si no está asociado a una petición, y habría que proteger cada acceso.
- Nota: la URL incluye `q=<term>` y se registra con `%r` (RF-18).

**D8 — Qué registra cada capa, sin duplicar.**

| Evento | Quién lo registra | Nivel |
|---|---|---|
| Reintento (`5xx`, `429`, transporte) | `retry.py` | `WARNING` |
| Reintentos agotados / `4xx` no reintentable | `retry.py` | `ERROR` |
| Challenge del WAF + inicio del enfriamiento | `ProductService` (conoce `WAF_COOLDOWN_SECONDS`) | `ERROR` |
| Rechazo por enfriamiento activo | handler `502` | `WARNING` |
| Cualquier otro `502` | handler `502` (conoce `postal_code` y `term`) | `ERROR` |
| Cuerpo inválido | `alcampo_search.py` | `ERROR` |

Un challenge deja dos `ERROR`: el del servicio, que dice "IP bloqueada, enfriamiento de N s", y el del handler, que dice qué petición recibió el `502`. Es intencionado: responden a preguntas distintas y comparten request id. `retry.py` **no** registra el challenge, para no tener un tercero.

**D9 — Mapper: un solo registro por búsqueda.** `map_search` cuenta los descartes y reúne los `retailerProductId` que se puedan leer. Al terminar registra `WARNING` si hay descartes pero queda algún producto (RF-14), y `ERROR` si no queda ninguno de una respuesta que traía alguno (RF-15). Sin productos en la respuesta, no registra nada.
- *Descartada:* un `WARNING` por producto descartado. Un cambio de formato generaría 50 líneas por búsqueda.

**D10 — Valores del cliente con `%r` mediante formato diferido de `logging`.** Siempre `logger.info("… term=%r", term)`, nunca f-strings. Además de escapar (RF-18), evita formatear mensajes que el nivel activo descarta.
- *Descartada:* un `Filter` que escape todos los mensajes. Escaparía también nuestros propios textos y ocultaría el problema en lugar de resolverlo donde entra el dato.

**D11 — Validación de `LOG_LEVEL` en `Settings`.** `field_validator` que pasa a mayúsculas y comprueba que el valor esté en `{"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}`. La app falla al construir `Settings` en el `lifespan`, con un mensaje claro, en lugar de un `ValueError` confuso dentro de `logging`.

## 6. Regresiones previstas en tests existentes

| Test | Riesgo | Previsión |
|---|---|---|
| `tests/core/test_config.py::test_environment_overrides_defaults` | usa `LOG_LEVEL=DEBUG` | sigue siendo válido: sin regresión |
| Tests de integración (`with TestClient(...)`) | el `lifespan` llama ahora a `configure_logging` y añade un handler al raíz | D3 lo hace idempotente y no toca `caplog`: sin regresión esperada. Se verifica en la tarea del `lifespan` |
| `tests/services/test_product_service.py::test_active_cooldown_on_a_miss_fails_without_calling_alcampo` | ahora se lanza `CooldownActiveError` | usa `pytest.raises(UpstreamUnavailableError)` y la subclase lo satisface: sin regresión |
| `tests/scrapers/test_retry.py` | nuevo parámetro `url` con valor por defecto | sin regresión |

**No preveo regresiones reales.** Aun así, aprendida la lección de la 002, cada tarea que conecta algo a la app real (`lifespan`, middleware) exige la suite completa en verde en su "Hecho cuando".

## 7. Estrategia de test por RF

**U** = unitario (con `caplog`), **I** = integración (app real + `lifespan` + fakeredis + respx).

| RF | Test | Tipo |
|---|---|---|
| RF-1 | `configure_logging("INFO", stream=StringIO())` → una línea con timestamp, `INFO`, `[-]`, logger y mensaje, en ese orden | U |
| RF-1 | llamar dos veces no duplica líneas y no elimina el handler de `caplog` | U |
| RF-1 | el `lifespan` configura el nivel desde `LOG_LEVEL` | I |
| RF-2 | `debug` → `DEBUG`; `VERBOSE` → `ValidationError` | U |
| RF-3 | una petición deja `request started` y `request finished` con el mismo request id, método, ruta, status y duración | I |
| RF-3 | una petición `422` también deja las dos líneas | I |
| RF-4 | un log emitido en el scraper durante una petición lleva el request id; fuera de una petición vale `-` | U + I |
| RF-5 | `X-Request-ID` en `200`, `422`, `500` y `502`, igual al de los logs | I |
| RF-5b | con `X-Request-ID: abc` del cliente, respuesta y logs usan otro valor de 32 hex | I |
| RF-6 | una ruta que lanza `RuntimeError("secret")` → `500 {"detail": "Internal server error"}`, `ERROR` con traceback (`record.exc_info`), y `"secret"` fuera del body | I |
| RF-7 | `502` por `503` agotado → `ERROR` con `reason`, `postal_code` y `term` | I |
| RF-8 | `503, 200` → 1 `WARNING` con intento, status, URL y espera | U |
| RF-9 | `503 × 3` → 2 `WARNING` + 1 `ERROR` con intentos y URL | U |
| RF-10 | `404` → 1 `ERROR` con status y URL, sin `WARNING` | U |
| RF-11 | challenge → `ERROR` con "enfriamiento de 180 s"; con `WAF_COOLDOWN_SECONDS=0` → "enfriamiento desactivado" | U |
| RF-12 | enfriamiento activo + miss → un `WARNING` y **ningún** `ERROR` | I |
| RF-13 | HTML → `ERROR` "invalid JSON"; `{"foo":1}` → `ERROR` "unexpected schema"; ambos con URL | U |
| RF-14 | 3 productos, 1 roto → 1 `WARNING` con `discarded=1` y su id | U |
| RF-15 | 2 productos, los 2 rotos → 1 `ERROR`; `productGroups: []` → ningún registro | U |
| RF-16 | valor corrupto → `WARNING` con la clave | U |
| RF-17 | respuesta mockeada con `Set-Cookie: VISITORID=secret-cookie-value` → `"secret-cookie-value"` no aparece en `caplog.text` en toda la petición | I |
| RF-18 | `term` con salto de línea → el mensaje de `request started` contiene `\\n` escapado y `caplog.text` no contiene la línea inyectada | I |

## 8. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | La factoría de `LogRecord` es estado global del proceso | Tests que se contaminan entre sí | Se instala una vez y es idempotente (D4). Fuera de una petición vale `-`, así que no hay estado residual |
| R2 | `BaseHTTPMiddleware` tiene limitaciones conocidas con respuestas en streaming | Ninguno hoy | No hay endpoints de streaming. Si llegaran, se revisa D5 |
| R3 | Un challenge deja dos `ERROR` | Percepción de duplicado | Intencionado y documentado (D8) |
| R4 | El access log de uvicorn duplica la línea de fin | Ruido | Fuera de alcance (spec §7); se desactiva en la 005 |
| R5 | `caplog` no aplica nuestro formato | RF-1 no se verifica con `caplog` | RF-1 se testea con un `stream` propio (`StringIO`) |

## 9. Secuencia de implementación

1. **Config:** validador de `LOG_LEVEL`.
2. **Logging:** `request_id_var`, factoría de `LogRecord`, `configure_logging` (formato, idempotencia, `caplog` intacto).
3. **Lifespan:** `configure_logging` al arrancar.
4. **Middleware:** request id, inicio y fin, `X-Request-ID`, id del cliente ignorado.
5. **Middleware:** `500` controlado con traceback.
6. **Handler `502`:** `CooldownActiveError`; `ERROR` o `WARNING` según el tipo.
7. **Reintentos:** `url`, `WARNING` y `ERROR`.
8. **Scraper:** cuerpo inválido.
9. **Servicio:** challenge y enfriamiento.
10. **Mapper y cache:** descartes y cache corrupta.
11. **Integración transversal:** correlación de request id de punta a punta, secretos y `term` con salto de línea.
12. **Docs** y verificación manual con `uvicorn`.

## 10. Estimación y entrega

| Bloque | `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| 1–6 Infraestructura: config, logging, middleware, `500`, `502` | ~130 | ~200 | — | ~330 |
| 7–12 Eventos de dominio, integración transversal, docs | ~70 | ~180 | ~30 | ~280 |
| **Total** | **~200** | **~380** | **~30** | **~610** |

**Supera las 400 líneas**, así que propongo **2 PRs encadenados**, como en la 002:

```
main ◄── PR1 infraestructura: formato, request id, 500, 502 (~330) ◄── PR2 eventos de dominio + docs (~280)
```

- Cada PR deja la suite en verde.
- PR1 ya da valor por sí solo: toda petición queda trazada y todo `500`/`502` queda registrado.
- PR2 añade el detalle de *por qué* falló (reintentos, WAF, formato inesperado, descartes).

## 11. Qué no cambia

Contrato de la API (bodies de `200`, `422`, `502`), lógica de reintentos, enfriamiento y cache. El `500` cambia de texto plano a `{"detail": "Internal server error"}`, tal como pide la spec (RF-6); hasta ahora no estaba especificado.
