# Spec 012 — Robustez operativa

- **Estado:** aprobada (2026-10-05)
- **Fecha:** 2026-10-05
- **Referencia:** `mercadona-scraper` spec 010 (*operational robustness*). Parte ya está hecha aquí: tiempos límite de Redis (`REDIS_TIMEOUT_SECONDS`, spec 007) y circuit breaker (spec 007 RF-18), que Mercadona dejó fuera de alcance

## 1. Contexto y objetivo

Estado verificado en el código el 2026-10-05:

- **No hay `/ready`.** `GET /health` responde `200` sin mirar nada (prueba de vida del `HEALTHCHECK` de Docker, spec 005). No hay forma de saber si la instancia tiene Redis, y Mercadona ya expone `GET /ready` con un contrato concreto.
- **El tiempo límite HTTP hacia Alcampo es una constante:** `REQUEST_TIMEOUT_SECONDS = 10.0` en `app/scrapers/http_client.py`. No se puede ajustar sin tocar código (Mercadona: `HTTP_TIMEOUT_SECONDS`).
- **Una rama del middleware no tiene test:** en `app/middleware/request_context.py`, si una excepción ocurre **después** de empezar la respuesta, se registra y se relanza (no se puede convertir en `500`). Ningún test la recorre.
- **Los logs de httpx llevan el token de página:** cada petición deja `INFO httpx: HTTP Request: GET …/search?q=…&pageToken=…` (visto en la verificación de la spec 009). El token es opaco y solo vale con las cookies de su sesión, que no se registran, pero es un dato de sesión de Alcampo que no aporta nada al log.

**Objetivo:** un `/ready` con el mismo contrato que Mercadona, el tiempo límite HTTP configurable, la rama del middleware cubierta y los tokens de página fuera de los logs.

## 2. Usuarios y actores

- **Operador / orquestador:** distingue "proceso vivo" (`/health`) de "listo con sus dependencias" (`/ready`) y ajusta tiempos límite por entorno.
- **Responsable del servicio:** logs sin datos de sesión de Alcampo.

## 3. Historias de usuario

- **H1.** Como operador, quiero un endpoint que diga si la instancia tiene Redis, con el mismo contrato que Mercadona.
- **H2.** Como operador, quiero configurar el tiempo límite de las peticiones a Alcampo sin tocar código.
- **H3.** Como responsable del servicio, quiero que los logs no guarden tokens de sesión de Alcampo.

## 4. Requisitos funcionales (EARS)

### A. Disponibilidad

- **RF-1.** EL sistema DEBERÁ exponer `GET /ready`, público como `/health` (sin `X-API-Key`), que comprueba que Redis responde (`PING`) dentro de `REDIS_TIMEOUT_SECONDS`, **a través del circuit breaker** (D2). No DEBERÁ llamar a Alcampo.
- **RF-2.** CUANDO Redis responda, `GET /ready` DEBERÁ devolver `200 {"status": "ready"}`. SI no responde, falla o el circuito está abierto, DEBERÁ devolver `503 {"status": "unavailable", "redis": "unreachable"}` (D1), sin la URL de Redis, credenciales ni detalles del error.
- **RF-3.** `GET /health` NO DEBERÁ cambiar (sigue siendo la prueba de vida de Docker).

### B. Tiempo límite HTTP

- **RF-4.** EL sistema DEBERÁ tomar el tiempo límite de cada petición a Alcampo (conexión, lectura, escritura y espera del pool) de `HTTP_TIMEOUT_SECONDS`, por defecto **10** (el valor actual, D3), mayor que 0; si no, la app no arranca.
- **RF-5.** Superarlo DEBERÁ tratarse como hoy: error de transporte, reintento según la spec 002 y `502` al agotarlos, dentro del tiempo máximo de la búsqueda (spec 008).

### C. Middleware

- **RF-6.** CUANDO una excepción ocurra después de empezar la respuesta, EL sistema DEBERÁ registrarla (`ERROR` con traceback y request id) y relanzarla, sin intentar enviar un `500`. Comportamiento actual: se fija con un test.

### D. Logs

- **RF-7.** EL sistema NUNCA DEBERÁ registrar el valor de `pageToken`: en las líneas de petición de httpx aparecerá como `pageToken=<redacted>`; el resto de la línea (método, ruta, otros parámetros, estado) no cambia (D4).

## 5. Requisitos no funcionales

- **RNF-1. Contrato de `/api/v1/products` intacto.**
- **RNF-2. Sin dependencias nuevas.**
- **RNF-3. Tests sin red ni Redis real** (fakeredis y dobles que fallan o no responden).
- **RNF-4. Docs vivas:** README y `.env.example` (lo edita el usuario).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Redis caído | `/ready` → `503`; `/health` → `200`; las búsquedas siguen sirviéndose sin cache (spec 007) |
| Redis colgado | `/ready` → `503` en ≈ `REDIS_TIMEOUT_SECONDS` |
| Circuito abierto (fallo hace < 10 s) | `/ready` → `503` al instante, sin `PING` (D2) |
| Redis vuelve | `/ready` → `200` en cuanto pasa el periodo del circuito y el `PING` funciona |
| Alcampo caído o WAF activo | `/ready` → `200` si Redis responde (un tercero no marca la instancia como no lista) |
| `HTTP_TIMEOUT_SECONDS=0` | la app no arranca |
| Petición de la página 2 | log de httpx con `pageToken=<redacted>` |

## 7. Fuera de alcance

- Comprobar Alcampo en `/ready`.
- Cambiar el `HEALTHCHECK` de Docker a `/ready`.
- Redactar otros parámetros (`q` es el término del cliente, ya registrado por la propia app).

## 8. Criterios de finalización

- [ ] RF-1 a RF-7 con tests en verde.
- [ ] `ruff`, `mypy`, `pytest` limpios; README actualizado; `.env.example` (el usuario) con `HTTP_TIMEOUT_SECONDS`.
- [ ] Verificación manual con `docker compose` (sin tocar Alcampo): `/ready` → `200`; parar Redis → `/ready` → `503`, `/health` → `200`; arrancar Redis → `/ready` → `200`.

## 9. Decisiones (resueltas el 2026-10-05: las recomendadas)

| # | Duda | Opciones | Recomendación |
|---|---|---|---|
| D1 | ¿`/ready` con Redis caído? La app **sí** sirve sin Redis (respaldos locales, spec 007) | **A)** `503`, como Mercadona; **B)** `200 {"status": "degraded", "redis": "unreachable"}` | **A**, por paridad de contrato (objetivo del proyecto). Consecuencia a documentar: si un orquestador saca de servicio las instancias con `/ready` en `503`, con Redis caído se quedarían todas fuera aunque podrían servir; para eso está `/health`. B rompería el contrato común |
| D2 | ¿`PING` siempre, o a través del circuit breaker? | **A)** por el circuito: abierto → `503` sin `PING`; **B)** `PING` siempre | **A.** No machaca un Redis caído (un orquestador sondea cada pocos segundos) y es coherente con el resto de la app; el coste es que `/ready` tarda hasta 10 s (`REDIS_CIRCUIT_OPEN_SECONDS`) en volver a `200` |
| D3 | `HTTP_TIMEOUT_SECONDS` por defecto | **A)** 10 (el actual); **B)** 5 (Mercadona) | **A.** Sin cambio de comportamiento; la portada de Alcampo es HTML pesado, y el tiempo máximo de la búsqueda (15 s, spec 008) ya acota el total |
| D4 | ¿Cómo quitar el token de los logs? | **A)** filtro de logging sobre `httpx` que sustituye el valor; **B)** subir `httpx` a `WARNING` (desaparecen las líneas de petición); **C)** dejarlo | **A.** Las líneas de httpx son las que permiten medir el espaciado y el tráfico en las verificaciones (specs 010 y 009); B las perdería |
