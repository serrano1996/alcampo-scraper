# Spec 010 — Ritmo de salida hacia Alcampo

- **Estado:** aprobada (2026-10-01)
- **Fecha:** 2026-10-01
- **Referencia:** sin equivalente en `mercadona-scraper`. Amplía la spec 008 (límite global de salida) tras el challenge del WAF del 2026-10-01 (spec 009, sección 10). Va **antes** de implementar la 009, que añade tráfico (recorrer páginas)

## 1. Contexto y objetivo

La spec 008 limita la salida a **20 peticiones cada 60 s**, un valor estimado: el umbral real del WAF para la búsqueda nunca se ha medido. El 2026-10-01 el WAF bloqueó la IP con **mucho menos tráfico** que ese límite. Todo lo observado hasta hoy:

| Fecha | Tráfico | ¿Challenge? |
|---|---|---|
| 2026-09-24 (Fase 0) | ~30 peticiones en 5 min, ráfagas manuales | Sí, al crear un destino |
| 2026-09-24 (Fase 0) | 26 en 13 min, a 30 s | Sí, al crear el 4.º destino |
| 2026-09-24 (Fase 0) | rondas de 11–13 a 30 s, 1 destino por ronda, 10 min entre rondas | No |
| 2026-09-30 (spec 007, T13) | 25 en 10 min, con **ráfagas de ~10 en 1–3 s** (la cadena de resolución) | **No** |
| 2026-10-01 (spec 009, D1) | 10 búsquedas en ~15 min, a 30 s, con **un `400` y un `401` provocados** justo antes | **Sí** |

**El recuento de peticiones no explica los datos:** el 30-09 hubo más tráfico y más concentrado que el 01-10, sin bloqueo. Lo que distingue al 01-10 son **dos respuestas de error provocadas** (un token de página usado en otra sesión → `400`, un token inválido → `401`) justo antes del challenge. Es una **hipótesis**: el WAF podría puntuar las respuestas de error como señal de bot. No se puede confirmar sin provocar más bloqueos.

Lo que sí es seguro:

- **El límite de 20/60 s no evitó el bloqueo**, así que hoy no protege lo que promete.
- **La app no sabe nada de un bloqueo salvo que ocurrió**: no registra cuánto tráfico había ni de qué tipo, así que tampoco hay datos para afinar el límite.
- **La spec 009 añadirá tráfico** (recorrer páginas, hasta 20 peticiones encadenadas) y **puede provocar respuestas de error** si alguna vez usa un token de página caducado.

**Objetivo:** reducir el riesgo de bloqueo con medidas prudentes que no dependan de conocer el umbral exacto, evitar que la propia app provoque respuestas de error, y **registrar en cada challenge el tráfico de los minutos anteriores**, para afinar los límites con datos de producción en lugar de con más bloqueos provocados.

## 2. Usuarios y actores

- **Responsable del servicio:** quiere menos bloqueos y datos para decidir los límites.
- **Aplicación cliente:** prefiere un `502` rápido en un pico a varios minutos sin servicio por un bloqueo.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que la app no pueda enviar a Alcampo más tráfico del que ya sabemos seguro, ni a corto ni a medio plazo.
- **H2.** Como responsable del servicio, quiero que la app no provoque respuestas de error en Alcampo que puedan delatarla como bot.
- **H3.** Como responsable del servicio, quiero saber, cada vez que salte el WAF, cuánto tráfico y de qué tipo había enviado la app antes.

## 4. Requisitos funcionales (EARS)

### A. Límite en dos ventanas

- **RF-1.** Además de la ventana corta de la spec 008, EL sistema DEBERÁ limitar las peticiones a Alcampo en una **ventana larga** (`ALCAMPO_RATE_LIMIT_LONG` por `ALCAMPO_RATE_WINDOW_LONG_SECONDS`), con las mismas reglas: compartida en Redis, con respaldo local, reintentos incluidos y un rechazo que no consume cupo (D1).
- **RF-2.** Los valores por defecto DEBERÁN ser **10 peticiones / 60 s** (ventana corta, antes 20) y **30 peticiones / 900 s** (ventana larga), configurables (D2). No están demostrados: se revisan con los datos de RF-6.

### B. Espaciado

- **RF-3.** EL sistema DEBERÁ dejar al menos `ALCAMPO_MIN_INTERVAL_MS` (por defecto 500) más un jitter aleatorio de hasta `ALCAMPO_INTERVAL_JITTER_MS` (por defecto 500) entre dos peticiones consecutivas a Alcampo **del mismo proceso**, en lugar de enviarlas de golpe (D3, D4). `ALCAMPO_MIN_INTERVAL_MS=0` lo desactiva. Esperar no consume cupo y cuenta dentro del tiempo máximo de cada operación (spec 008 RF-7).

### C. No provocar errores

- **RF-4.** EL sistema NUNCA DEBERÁ enviar a Alcampo una petición que sepa inválida. En concreto, un token de página solo DEBERÁ usarse con la sesión que lo obtuvo (lo exige también la spec 009).
- **RF-5.** CUANDO Alcampo responda un `4xx` distinto de `404` y `429`, EL sistema DEBERÁ registrar un `WARNING` con el código, el paso o endpoint y el tipo de petición, para poder relacionarlo con futuros challenges.

### D. Datos para afinar

- **RF-6.** CUANDO llegue un challenge del WAF, el `ERROR` que ya se registra (spec 003 RF-11) DEBERÁ incluir el tráfico de salida reciente de la instancia: número de peticiones en los últimos 1, 5 y 15 minutos, desglosado por tipo (búsqueda, cadena de resolución, renovación de sesión) y número de respuestas `4xx` en esos 15 minutos.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas** (constitución #1).
- **RNF-2. Contrato intacto:** al agotar cualquier ventana, el mismo `502` y `WARNING` de la spec 008.
- **RNF-3. Tests sin red ni tiempo real:** relojes y esperas inyectables.
- **RNF-4. Docs vivas:** README y `.env.example` (lo edita el usuario).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Ventana corta con cupo, larga agotada | `502` + `WARNING` con el motivo de la ventana larga (RF-1) |
| Una resolución de región (~10 peticiones) | se envían espaciadas por `ALCAMPO_MIN_INTERVAL_MS`, no en 1–3 s (RF-3) |
| El espaciado empuja una operación más allá de su tiempo máximo | `502` por tiempo máximo, como hoy |
| Redis caído | ambas ventanas en el respaldo local del proceso (spec 007) |
| Challenge | `ERROR` con el desglose de los últimos 1/5/15 min (RF-6) |
| `400`/`401` de Alcampo | `WARNING` con el código y el tipo de petición (RF-5) |

## 7. Fuera de alcance

- **Medir el umbral real** provocando bloqueos: cada prueba cuesta minutos de servicio.
- **Superar el challenge** (constitución #2).
- **IPs de salida alternativas o proxies.**

## 8. Criterios de finalización

- [ ] RF-1 a RF-6 cubiertos por tests en verde.
- [ ] Un test demuestra que una resolución de región sale espaciada (reloj falso).
- [ ] Un test demuestra el desglose del `ERROR` de challenge.
- [ ] `ruff`, `mypy` y `pytest` limpios; README y `.env.example` actualizados.
- [ ] **Sin verificación en vivo de los límites** (sería provocar bloqueos). Verificación manual corta con `docker compose`: una búsqueda real y una resolución de región, comprobando en los logs el espaciado.

## 9. Decisiones (resueltas el 2026-10-01)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Cómo limitar a medio plazo | Segunda ventana, larga, de 15 min, con las mismas reglas que la corta | RF-1. El bloqueo del 01-10 (10 en 15 min a 30 s) nunca lo habría frenado una ventana de 1 min |
| D2 | Valores por defecto | Corta 10 / 60 s (antes 20); larga 30 / 900 s | RF-2. **No demostrados**: el 01-10 hubo bloqueo con 10 en 15 min. Más estrictos dejarían la app casi inutilizable (una búsqueda de un CP nuevo ≈ 11 peticiones). Se revisan con RF-6 |
| D3 | Espaciado mínimo | 500 ms + jitter de hasta 500 ms | RF-3. Una resolución pasa de 1–3 s a ~7 s; una búsqueda normal (1 petición) no cambia |
| D4 | Espaciado entre instancias | Por proceso | RF-3. La ventana larga ya es global (Redis) |
