# Spec 003 — Logging

- **Estado:** aprobada (2026-09-25)
- **Fecha:** 2026-09-25
- **Referencia:** misma secuencia que `mercadona-scraper/specs/003-mercadona-scaper-logging`, adaptada a los fallos propios de Alcampo
- **Evidencia:** estado actual del código tras las specs 001 y 002

## 1. Contexto y objetivo

Hoy el servicio **no deja ningún rastro** de lo que hace:

- **No hay ni una sola llamada a `logging`** en `app/`. `LOG_LEVEL` está declarada en `Settings` desde la 001, pero nadie la usa.
- **Todo `502` es silencioso.** El handler de `UpstreamUnavailableError` en `main.py` responde `502` sin registrar nada, y el `reason` de la excepción (pensado "para logs", 001-plan-D7) se pierde.
- **Los reintentos son invisibles:** ni cada reintento ni su agotamiento dejan huella.
- **Cualquier bug no previsto** acaba en el `500` por defecto de Starlette (texto plano `Internal Server Error`), fuera de nuestro formato y sin contexto.

Y hay fallos propios de Alcampo, que Mercadona no tenía, igual de silenciosos:

- **Challenge del WAF y enfriamiento (spec 002):** la IP se bloquea y el servicio deja de llamar a Alcampo durante minutos, sin que nadie se entere.
- **Productos descartados por el mapper (001-spec-D7):** si Alcampo cambiara un campo del JSON, **todos** los productos se descartarían y la API respondería `200` con una lista vacía. Parecería un "no hay resultados" legítimo.
- **Entrada de cache corrupta (001-plan-D8):** se trata como miss sin avisar.

**Objetivo:** que cualquier error o degradación en producción deje una línea de log clara, con el nivel adecuado y contexto suficiente para diagnosticarlo sin reproducirlo. Sin dependencias nuevas (constitución #1) y sin filtrar secretos (constitución #12).

## 2. Usuarios y actores

- **Sistema:** emite logs durante el ciclo de vida de la app y de cada petición.
- **Responsable del servicio en producción:** lee esos logs (en `stderr`, capturado por quien despliegue) para detectar y diagnosticar problemas.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que cada error (Alcampo caído, reintentos agotados, bloqueo del WAF, respuesta con un formato inesperado o un bug no previsto) deje una línea de log clara con contexto, para diagnosticarlo sin reproducirlo.
- **H2.** Como responsable del servicio, quiero poder seguir todas las líneas de una misma petición aunque haya tráfico concurrente, para reconstruir qué pasó con una petición concreta.
- **H3.** Como responsable del servicio, quiero enterarme de las degradaciones silenciosas (productos descartados, cache corrupta, enfriamiento activo), para detectar cambios en Alcampo antes de que los note un consumidor.

## 4. Requisitos funcionales (EARS)

### Configuración

- **RF-1.** CUANDO arranque la aplicación (`lifespan`), EL sistema DEBERÁ configurar el logging una sola vez: nivel desde `LOG_LEVEL` (por defecto `INFO`), salida a `stderr` y formato con timestamp, nivel, logger, request id y mensaje. Solo con el módulo `logging` estándar.
- **RF-2.** SI `LOG_LEVEL` no es un nivel válido de `logging` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`; sin distinguir mayúsculas), ENTONCES la aplicación DEBERÁ fallar al arrancar.

### Petición

- **RF-3.** CUANDO llegue una petición HTTP, EL sistema DEBERÁ generarle un **request id** único y registrar a nivel `INFO` una línea de **inicio** (método, ruta y parámetros de consulta) y otra de **fin** (código de estado y duración en ms), ambas con el mismo request id. Esto incluye las peticiones que fallan la validación (`422`) antes de llegar a la ruta.
- **RF-4.** EL sistema DEBERÁ incluir el request id en **toda** línea de log emitida mientras se procesa esa petición, incluidas las de módulos que no conocen HTTP (scraper, reintentos, cache, servicio). Fuera de una petición (arranque, parada), el campo DEBERÁ valer `-`.
- **RF-5.** EL sistema DEBERÁ generar el request id siempre él mismo (`uuid4().hex`) y devolverlo en la cabecera de respuesta `X-Request-ID`, también en respuestas `422`, `500` y `502` (D1).
- **RF-5b.** SI la petición trae una cabecera `X-Request-ID`, ENTONCES EL sistema DEBERÁ ignorarla y usar el id generado (D1): un valor externo en los logs permitiría inyectar líneas falsas.

### Errores

- **RF-6.** CUANDO una excepción no controlada llegue al límite de la aplicación, EL sistema DEBERÁ registrarla a nivel `ERROR` con el traceback completo y responder `500 {"detail": "Internal server error"}`, mediante un exception handler global propio y no el de Starlette. El body NO DEBERÁ incluir el mensaje de la excepción.
- **RF-7.** CUANDO una búsqueda termine en `502` por `UpstreamUnavailableError`, EL sistema DEBERÁ registrar a nivel `ERROR` el motivo (`reason`) y los parámetros de la búsqueda (`postal_code`, `term`), **salvo** que el `502` lo provoque el enfriamiento activo: ese caso solo deja el `WARNING` de RF-12 (D2). La respuesta sigue siendo el `502` de siempre, sin el motivo.

### Reintentos

- **RF-8.** CUANDO se vaya a reintentar una petición a Alcampo, EL sistema DEBERÁ registrar un `WARNING` con el número de intento, el motivo (código de estado o tipo de error de transporte), la URL y la espera calculada.
- **RF-9.** SI se agotan los reintentos, ENTONCES EL sistema DEBERÁ registrar un `ERROR` con el número de intentos realizados y la URL. Es un evento distinto de los `WARNING` por intento.
- **RF-10.** SI Alcampo responde un `4xx` no reintentable, ENTONCES EL sistema DEBERÁ registrar un `ERROR` con el código y la URL.

### Fallos propios de Alcampo

- **RF-11.** CUANDO Alcampo responda un challenge del WAF, EL sistema DEBERÁ registrar un `ERROR` que indique que la IP está bloqueada y que empieza un enfriamiento de `WAF_COOLDOWN_SECONDS` segundos (o que el enfriamiento está desactivado, si vale `0`).
- **RF-12.** MIENTRAS el enfriamiento esté activo, CUANDO una búsqueda no cacheada se rechace sin llamar a Alcampo, EL sistema DEBERÁ registrar **solo** un `WARNING`, y no un `ERROR`: es una degradación prevista y gestionada, y el evento accionable ya quedó registrado en RF-11 (D2).
- **RF-13.** SI la respuesta de Alcampo no es JSON o no tiene la forma esperada, ENTONCES EL sistema DEBERÁ registrar un `ERROR` con la URL y el tipo de fallo (JSON inválido o schema inesperado).
- **RF-14.** CUANDO el mapper descarte productos mal formados, EL sistema DEBERÁ registrar **un** `WARNING` por búsqueda (no uno por producto) con el número de descartados y sus `retailerProductId`, si los tienen.
- **RF-15.** SI el mapper descarta **todos** los productos de una respuesta que traía alguno, ENTONCES EL sistema DEBERÁ registrarlo como `ERROR` (en lugar del `WARNING` de RF-14), porque apunta a un cambio de formato en Alcampo y no a una búsqueda sin resultados (D3).
- **RF-16.** CUANDO una entrada de cache esté corrupta y se trate como miss, EL sistema DEBERÁ registrar un `WARNING` con la clave.

### Seguridad de los logs

- **RF-18.** EL sistema DEBERÁ registrar todo valor que venga del cliente (`term`, `postal_code`, parámetros de consulta y ruta) con `%r`, de forma que los saltos de línea y los caracteres de control queden escapados y no puedan fabricar líneas de log falsas (D4).
- **RF-17.** EL sistema NO DEBERÁ registrar nunca cookies (`VISITORID`, `global_sid`, `AWSALB`, `AWSALBCORS`), la cabecera `X-API-Key` (spec 004) ni ninguna cabecera completa de petición o respuesta. Tampoco el cuerpo de las respuestas de Alcampo.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas:** solo `logging` de la librería estándar (constitución #1). Nada de `structlog`, `python-json-logger` ni integraciones externas.
- **RNF-2. Logging síncrono aceptado:** las llamadas a `logger.*` son síncronas. Se acepta como excepción puntual a la constitución #3, porque escribir en `stderr` cuesta muy poco frente a montar un logging asíncrono propio. Misma decisión que Mercadona.
- **RNF-3. Tipado estricto:** middleware, filtro y handlers con type hints y sin `Any` (constitución #4).
- **RNF-4. Loggers por módulo:** cada módulo usa `logging.getLogger(__name__)`, para poder filtrar por componente.
- **RNF-5. Tests con `caplog`:** cada RF se verifica sobre los registros capturados, sin depender del formato de texto final salvo en RF-1.
- **RNF-6. Docs vivas:** el README documenta `LOG_LEVEL`, el formato de línea y la cabecera `X-Request-ID`.

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Petición con parámetros inválidos (`422`) | se registran el inicio y el fin (RF-3), y la respuesta lleva `X-Request-ID` (RF-5). Por eso el logging de petición va en un middleware, no en la ruta |
| Petición con `X-Request-ID: abc` del cliente | se ignora; los logs y la respuesta usan el id generado (RF-5b) |
| Excepción no controlada | `ERROR` con traceback (RF-6), y la línea de fin de petición registra el `500` |
| Reintento fallido pero éxito posterior | solo `WARNING` por intento (RF-8), sin `ERROR` |
| Búsqueda sin resultados legítima (`productGroups: []`) | nada de RF-14 ni RF-15: no hay nada que descartar |
| Todos los productos descartados | `ERROR` (RF-15), y la respuesta sigue siendo `200` con `[]` (el contrato no cambia) |
| Acierto de cache | inicio y fin de petición; ni reintentos ni scraper |
| Enfriamiento activo con 100 búsquedas no cacheadas | 100 `WARNING` (RF-12) y **ningún** `ERROR` (D2). El único `ERROR` del episodio es el del challenge (RF-11) |
| `LOG_LEVEL=debug` | válido: no distingue mayúsculas (RF-2) |
| `LOG_LEVEL=VERBOSE` | la app no arranca (RF-2) |
| `term` con saltos de línea o caracteres de control (`"leche" + salto de línea + "ERROR fake"`) | aparece escapado como `'leche\nERROR fake'`, en una sola línea (RF-18) |
| Redis caído | sigue fuera de alcance hasta la 007 (`500`). Con esta spec, al menos queda registrado por RF-6 |

## 7. Fuera de alcance

- **Logs en JSON** e integración con plataformas de observabilidad (Sentry, Datadog, ELK, OpenTelemetry): solo texto plano a `stderr`. Igual que Mercadona.
- **Métricas y tracing distribuido.**
- **Rotación o escritura a fichero:** quien despliegue captura `stderr` (Docker, spec 005).
- **Cambiar el access log de uvicorn.** Seguirá emitiendo su propia línea por petición, duplicada con la de fin de RF-3 y sin request id. Si molesta, se desactiva al desplegar (`--no-access-log`, spec 005); no es cosa de la app.
- **Aceptar un request id del cliente** (D1). Si en el futuro hay un proxy que lo genere, se replantea con validación de formato.

## 8. Criterios de finalización

- [ ] RF-1 a RF-18 cubiertos por tests (`caplog` y, para RF-1, el formato real) en verde.
- [ ] Un test de integración demuestra que todas las líneas de una petición que reintenta y acaba en `502` comparten el mismo request id, que coincide con el de `X-Request-ID`.
- [ ] Un test demuestra que ninguna línea contiene los valores de las cookies de Alcampo, aunque la respuesta mockeada las envíe.
- [ ] Un test demuestra que un `term` con un salto de línea produce una sola línea de log.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] README actualizado.
- [ ] **Verificación manual:** arrancar con `uvicorn`, hacer 1 búsqueda real y 1 con un `term` inválido, y comprobar en la consola el formato, el request id y las líneas de inicio y fin. Los errores de upstream (reintentos, WAF, formato inesperado) se verifican solo con `respx`: provocarlos en real no es posible o dispararía el WAF.

## 9. Decisiones (dudas resueltas el 2026-09-25)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Request id | Generado siempre por nosotros (`uuid4().hex`), devuelto en `X-Request-ID`; el del cliente se ignora | RF-5, RF-5b. Diferencia con Mercadona, que no lo devuelve |
| D2 | Ruido durante el enfriamiento | Los `502` del enfriamiento solo dejan un `WARNING`; el único `ERROR` del episodio es el del challenge | RF-7, RF-12 |
| D3 | Descarte total de productos | `ERROR` | RF-15. Aporte propio: el mapper de Mercadona no descarta productos |
| D4 | Inyección en los logs vía `term` | Valores del cliente registrados con `%r` | RF-18 |
