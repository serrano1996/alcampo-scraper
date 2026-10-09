# Spec 015 — Fallos encontrados por las revisiones de dia-scraper

- **Estado:** aprobada (2026-10-09), con las decisiones de la sección 8; F10 añadido al preparar el plan
- **Fecha:** 2026-10-09
- **Referencia:** `dia-scraper` copió el diseño de Alcampo, y sus revisiones con contexto nuevo encontraron fallos en ese diseño: specs 001 (T-revisión), 003 (T14), 004, 005, 007 (T5) y 008 (T9) de `dia-scraper`.

## 1. Contexto y objetivo

Cada caso se ha **reproducido en Alcampo** el 2026-10-09 (`7f59eae`), con su `.venv` y sin llamar a Alcampo:

| # | Fallo | Evidencia en Alcampo | Efecto |
|---|---|---|---|
| F1 | `send_with_retry` solo captura `httpx.TransportError` | `issubclass(httpx.DecodingError, TransportError)` y `TooManyRedirects` → `False` | una respuesta mal comprimida o un bucle de redirecciones da `500` en vez de reintentar o `502` |
| F2 | `Retry-After` con una fecha de año desbordado | `parse_retry_after("Wed, 21 Oct 99999999999999999999 07:28:00 GMT")` lanza `OverflowError` | un `429` con esa cabecera da `500` |
| F3 | `API_KEYS` sale en los volcados de `Settings` | `"secret-token-1" in settings.model_dump()` y en `model_dump_json()` → `True` (el `repr` ya lo oculta) | cualquier log o depuración que vuelque la configuración filtra los tokens |
| F4 | Redacción de parámetros solo por nombre exacto | `SECRET_PARAM_NAMES = {"api_key", "apikey", "x-api-key", "key", "token"}`: `access_token`, `api-key`, `apiToken`, `password`, `secret`… salen en claro | un secreto mandado por error en la URL acaba en los logs |
| F5 | Los parámetros del log, sin tope ni duplicados | `redact_params(QueryParams(...))` hace un `dict`: `?t=a&t=b` registra solo uno; una query larga (lo que acepte el servidor, decenas de KB) va entera a una línea | logs inflables desde fuera; un valor repetido se pierde |
| F6 | `setuptools` sin hash | `[build-system] requires = ["setuptools>=68"]`; el `Dockerfile` (`pip install --no-deps .`) y la CI (`-e .`) lo descargan de PyPI sin hash para construir el paquete en un entorno aislado | rompe la promesa de la spec 014 (todo lo instalado, verificado por hash) |
| F7 | Circuito de Redis: pasado el periodo, **todas** las llamadas concurrentes prueban Redis | 20 llamadas a la vez → 20 operaciones contra Redis | con Redis colgado, cada petición en curso paga `REDIS_TIMEOUT_SECONDS` y deja su `WARNING`, contra la spec 007 RF-18 |
| F8 | Limitador: si el `ZREM` que devuelve un hueco rechazado falla, la petición **pasa** | `limit=1`, `zrem` fallando → la segunda `acquire` se admite y quedan 2 miembros | se supera el límite hacia Alcampo justo cuando Redis falla; además el rechazo, lanzado dentro del circuito, impide que una prueba lo cierre |
| F9 | Enfriamiento del WAF: uno iniciado en Redis se pierde si Redis cae después | `activate` con Redis sano no guarda nada en el `LocalCooldown` | con Redis caído en mitad de un bloqueo, la instancia vuelve a llamar a Alcampo antes de tiempo: renueva el bloqueo |
| F10 | El `500` registra el mensaje de la excepción | `logger.exception("unhandled error")` en `request_context.py`; Dia lo cambió por "tipo y frames, nunca el mensaje" (su spec 004, T13) | el mensaje puede llevar un fragmento de la respuesta de Alcampo o una URL con el término del cliente |

También `scripts/lock.sh` pasa `$*` sin filtrar dentro de `sh -c` y no limpia `*.egg-info`/`build` si falla (Dia, spec 007 T5): va con F6.

**Objetivo:** corregir los diez, con el mismo criterio con que se corrigieron en Dia, sin cambiar el contrato de la API.

## 2. Historias de usuario

- **H1.** Como consumidor, quiero un `502` (o un reintento), nunca un `500`, cuando Alcampo responde algo raro.
- **H2.** Como responsable del servicio, quiero que ningún secreto acabe en los logs y que los logs no se puedan inflar desde fuera.
- **H3.** Como responsable del servicio, quiero que todo lo que se instala, también la herramienta de build, esté verificado por hash.
- **H4.** Como responsable del servicio, quiero que, con Redis fallando, los límites y el enfriamiento sigan siendo al menos tan estrictos como con Redis, y que solo una petición pague el timeout.

## 3. Requisitos funcionales (EARS)

### A. Respuestas de Alcampo

- **RF-1.** CUANDO `send` lance cualquier `httpx.RequestError` (no solo `TransportError`), EL sistema DEBERÁ tratarlo como un fallo de transporte: reintento y, agotados, `UpstreamUnavailableError` (`502`) (D1).
- **RF-2.** SI `Retry-After` no se puede convertir en una espera finita (fecha desbordada, número infinito), ENTONCES DEBERÁ tratarse como ausente (backoff exponencial), sin excepción.

### B. Secretos y logs

- **RF-3.** `API_KEYS` NO DEBERÁ aparecer en `model_dump()` ni `model_dump_json()` de `Settings`.
- **RF-4.** Un parámetro DEBERÁ redactarse si su nombre, normalizado (minúsculas, sin `-`, `_` ni `.`), **contiene** `key`, `token`, `secret`, `auth` o `pass` (D2).
- **RF-5.** La línea `request started` DEBERÁ registrar todos los valores de un parámetro repetido y cortar la representación de los parámetros a 500 caracteres.
- **RF-10.** Un error no controlado DEBERÁ registrarse con el tipo de la excepción y sus frames (fichero, línea, función), nunca con su mensaje.

### C. Build

- **RF-6.** `scripts/lock.sh` DEBERÁ generar `requirements-build.lock` con hash desde `[build-system].requires`; la imagen y la CI DEBERÁN instalarlo con `--require-hashes` y construir el paquete con `--no-build-isolation`. `lock.sh` solo DEBERÁ aceptar `--upgrade` y limpiar lo que deja `pip-compile` aunque falle.

### D. Redis caído

- **RF-7.** Pasado el periodo del circuito, solo **una** llamada DEBERÁ probar Redis; mientras tanto, las demás lo ven abierto. Una prueba que termina sin respuesta de Redis (un error ajeno, una cancelación) deja que la siguiente llamada pruebe.
- **RF-8.** Una vez Redis ha dicho "límite agotado", la petición DEBERÁ rechazarse aunque falle la devolución del hueco; y ese rechazo NO DEBERÁ lanzarse dentro de la operación del circuito (una respuesta de Redis, aunque sea "no", cierra el circuito).
- **RF-9.** Todo desafío del WAF DEBERÁ activar también el enfriamiento local del proceso, para que sobreviva a una caída posterior de Redis (D3).

## 4. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas de la app.**
- **RNF-2. TDD:** cada RF con un test que falle antes del cambio, reproduciendo el caso de la sección 1.
- **RNF-3. Sin cambios en el contrato** de `/api/v1/products`, `/health` ni `/ready`.

## 5. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `DecodingError` en el último intento | `502`, con el `ERROR` de reintentos agotados |
| `Retry-After: 99999999999999999999` (entero enorme) | se acota a 60 s, como hoy (no es un error) |
| Parámetro `Authorization`, `client_secret`, `X-Api-Token` | `***` |
| Parámetro `keyword`, `monkey` | también `***` (contiene `key`): falso positivo aceptado (D2) |
| Varios desafíos del WAF seguidos con Redis sano | la duración crece en Redis como hoy; la local se alinea con lo que devuelve Redis (D3) |
| Prueba del circuito cancelada | la siguiente llamada vuelve a probar |

## 6. Fuera de alcance

- Cambiar el tope de 60 s de `Retry-After` por un `502` como hace Dia: es una diferencia de diseño, no un fallo.
- Unificar el código de Alcampo y Dia en una librería común.
- Fijar las acciones de la CI por SHA.

## 7. Criterios de finalización

- [ ] Los diez casos de la sección 1, reproducidos en un test RED y en verde tras el cambio.
- [ ] `ruff`, `mypy`, `pytest` y la CI en verde.
- [ ] `docker build` con el daemon: la imagen se construye sin aislamiento, con `setuptools` del lock.
- [ ] README actualizado donde cambie el comportamiento (redacción, locks).
- [ ] Revisión con contexto nuevo antes del PR.

## 8. Decisiones (dudas resueltas el 2026-10-09)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | `DecodingError` y `TooManyRedirects` | Se reintentan como un fallo de transporte | RF-1 |
| D2 | Redacción | Por subcadena del nombre normalizado (`key`, `token`, `secret`, `auth`, `pass`) | RF-4; falsos positivos aceptados |
| D3 | Enfriamiento local con Redis sano | El local toma la duración que devuelve Redis | RF-9 |
| D4 | Entrega | Una spec, un PR, una tarea por fallo | tasks.md |
