# Spec 007 — Resolución de región por código postal

- **Estado:** aprobada (2026-09-28). **Enmendada el 2026-09-28** tras la verificación en vivo de D1 (plan §2): RF-3, RF-9, RF-11, D4 y D6
- **Fecha:** 2026-09-28
- **Referencia:** `mercadona-scraper/specs/007-mercadona-scraper-warehouse-resolution`, adaptada a Alcampo. Además hereda dos pendientes: la degradación sin Redis (prometida desde la spec 001) y los timeouts de Redis (análisis del 2026-09-28, apartados en la spec 008)

## 1. Contexto y objetivo

Hoy toda búsqueda va a la región por defecto de una sesión anónima ("Vaguada", `warehouse: "5"`), sea cual sea el `postal_code`. La Fase 0 (§3) demostró que **precio y catálogo dependen de la región**:

- Canarias (Telde) frente a Vaguada, término "agua": 23 de 26 productos comunes con precio distinto (Bezoya 6×1,5 L: 5,40 € frente a 4,02 €).
- Barcelona (Diagonal Mar) frente a Vaguada: 7 precios distintos y catálogos diferentes.
- Dos regiones de Madrid (Moratalaz y Vaguada): mismos precios, catálogo algo distinto.

Así que hoy un cliente de Las Palmas o de Barcelona recibe **precios de Madrid**. La respuesta no miente del todo (dice `warehouse: "5"`), pero no es lo que pidió.

**En Alcampo, resolver la región es caro y delicado** (Fase 0 §3 y §5):

1. **Cadena de peticiones con sesión:** `GET /` (token CSRF y `visitorId`) → áreas por CP → detalle del área (coordenadas) → `deliverability` → **crear destino temporal** → dirección de entrega (`resolvedRegionId`). Para que las búsquedas usen esa región hay que **confirmarla en la sesión** con dos pasos más (`proposition` y `sessions/active`). Son unas 8 peticiones por código postal nuevo.
2. **El paso "crear destino temporal" es donde saltaron los 3 bloqueos del WAF** de la Fase 0. La hipótesis es un límite estricto de 3–4 creaciones por ventana e IP, que al superarse bloquea la IP entera.
3. **La búsqueda no acepta la región como parámetro:** la toma de la **sesión** (cookies). Hay que mantener una sesión confirmada por región y buscar con ella.
4. **Esa sesión caduca:** la cookie `VISITORID` tiene `Max-Age=3600`. No sabemos si al caducar la sesión vuelve **en silencio** a Vaguada, lo que devolvería precios de Madrid etiquetados como otra región.
5. **CP inexistente:** el primer paso devuelve `200 []` (verificado con `99999`). **CP sin servicio** (`NOT_DELIVERABLE`): el valor existe en el bundle, pero no se ha observado en vivo.

Además, dos pendientes de Redis:

- **Redis caído:** hoy la búsqueda da `500` (spec 001). Esta spec lo degrada.
- **Redis colgado** (verificado el 2026-09-28): el cliente no tiene ningún timeout, así que una petición puede quedarse esperando para siempre mientras `/health` sigue `healthy`.

**Objetivo:** que cada búsqueda use la región real del `postal_code`, con precios y catálogo correctos. Resolver cada código postal **lo menos posible** y sin provocar el WAF. Nunca servir una región por otra en silencio. Y que un Redis caído o colgado no tumbe el servicio.

## 2. Usuarios y actores

- **Aplicación cliente autorizada** (spec 004): envía un `postal_code` real y espera los precios y el catálogo de su región.
- **Responsable del servicio:** ajusta TTLs y límites; necesita ver en los logs qué región se usó y por qué falló una resolución.
- **Sistema:** resuelve código postal → región, mantiene una sesión por región y cachea todo lo posible.

## 3. Historias de usuario

- **H1.** Como aplicación cliente, quiero precios y catálogo de la región de mi código postal, no los de Madrid.
- **H2.** Como aplicación cliente, quiero que un código postal mal formado se rechace con `422` y uno sin servicio con `404`, para detectar errores de integración.
- **H3.** Como responsable del servicio, quiero que resolver regiones nunca provoque un bloqueo del WAF, aunque lleguen muchos códigos postales distintos.
- **H4.** Como responsable del servicio, quiero que el servicio **nunca** devuelva datos de una región etiquetados como otra.
- **H5.** Como responsable del servicio, quiero que un Redis caído o colgado degrade el servicio, no que lo tumbe.

## 4. Requisitos funcionales (EARS)

### A. Validación

- **RF-1.** SI `postal_code` no son exactamente 5 dígitos, ENTONCES EL sistema DEBERÁ responder `422` desde la validación de `ProductQuery`, sin tocar Redis ni Alcampo.

### B. Resolución código postal → región

- **RF-2.** CUANDO llegue una búsqueda con un `postal_code` válido sin resolución en cache, EL sistema DEBERÁ resolver su región con la cadena de la Fase 0 §3 (pasos 0 a 5) y obtener su identificador de región.
- **RF-3.** EL sistema DEBERÁ cachear en Redis la resolución `postal_code → región` con TTL `REGION_CACHE_TTL_SECONDS` (D2), y reutilizarla mientras esté vigente. Por cada región DEBERÁ guardar también el destino de entrega creado al resolverla y su `retailerRegionId`, para poder renovar su sesión sin crear otro destino (RF-11).
- **RF-4.** SI el código postal no existe (paso 1 → `[]`) o no tiene servicio (`deliverability` distinta de `DELIVERABLE`), ENTONCES EL sistema DEBERÁ responder `404 {"detail": "Postal code not served by Alcampo"}` (D8), sin reenviar nada de Alcampo, y cachear esa respuesta negativa con TTL `REGION_NEGATIVE_CACHE_TTL_SECONDS` (D3).
- **RF-5.** SI algún paso responde con una forma inesperada (campos ausentes, tipos distintos, `2xx` sin el dato esperado), ENTONCES EL sistema DEBERÁ responder `502`, registrar un `ERROR` con el paso que falló y **no** cachear nada.
- **RF-6.** Cada petición de la cadena DEBERÁ pasar por la política existente: reintentos (spec 002), detección del challenge y enfriamiento (002/008), límite global de salida (008) y tiempo máximo (008). Un challenge en cualquier paso activa el enfriamiento.
- **RF-7.** Varias búsquedas simultáneas con el **mismo** código postal sin resolver DEBERÁN compartir una única resolución (como RF-1 de la 008).
- **RF-8.** EL sistema DEBERÁ limitar las resoluciones **nuevas** a `REGION_RESOLUTION_LIMIT` por ventana de `REGION_RESOLUTION_WINDOW_SECONDS`, con un límite propio, más estricto que el global, porque el paso "crear destino temporal" es el más sensible al WAF (D4). Agotado, una búsqueda con un código postal sin resolver DEBERÁ responder `502` al momento, con un `WARNING`; las búsquedas de códigos postales ya resueltos no se ven afectadas.

### C. Sesión por región

- **RF-9.** EL sistema DEBERÁ buscar cada región con una sesión (cliente HTTP con sus cookies) confirmada para esa región (pasos 6 y 7). Todos los códigos postales de una misma región DEBERÁN compartir la sesión.
- **RF-10.** EL sistema NUNCA DEBERÁ devolver resultados de una región etiquetados como otra. Una sesión DEBERÁ renovarse antes de que pueda perder su región, o comprobarse que la conserva, según lo que determine la verificación en vivo (D6). Si no puede garantizarse, la búsqueda DEBERÁ fallar con `502`.
- **RF-11.** Renovar la sesión de una región DEBERÁ reutilizar su destino guardado (`proposition` + `active`) y **no** crear uno nuevo. Por eso cuenta solo contra el límite global de la 008, no contra el de resoluciones (RF-8), que queda para la **creación de destinos** de códigos postales nuevos. SI el destino guardado ya no sirve, ENTONCES la región se olvida y el siguiente código postal de esa región se resuelve de nuevo (con RF-8).

### D. Cache y respuesta

- **RF-12.** La clave de cache de búsqueda DEBERÁ ser `search:{región}:{término normalizado}`, de modo que los códigos postales de una misma región compartan cache y los de regiones distintas nunca se mezclen. La clave de agrupación de la 008 también usa la región.
- **RF-13.** `search.warehouse` DEBERÁ ser el identificador real de la región usada (el `retailerRegionId`, como `"5"` hoy, D1c). `search.postal_code` sigue siendo el recibido. Sin cambios en la forma del contrato.

### E. Redis

- **RF-14.** El cliente Redis DEBERÁ tener timeouts de conexión y de operación de `REDIS_TIMEOUT_SECONDS` (por defecto `2`).
- **RF-15.** SI Redis no responde (caído o timeout), ENTONCES EL sistema DEBERÁ degradar en lugar de dar `500`: buscar sin cache (`200` si Alcampo responde) y registrar un `WARNING` por petición afectada. Las protecciones que hoy viven en Redis (límites de salida, enfriamiento, cache de regiones) DEBERÁN seguir actuando según D7.
- **RF-16.** `/health` no cambia (spec 005 RF-12): no toca Redis.
- **RF-18.** *(Enmienda del 2026-09-30, tras la verificación manual de T13.)* CUANDO una operación con Redis falle, EL sistema DEBERÁ dejar de intentar Redis durante `REDIS_CIRCUIT_OPEN_SECONDS` (por defecto 10) y usar directamente los respaldos de RF-15, sin esperar ningún timeout. Pasado ese tiempo, la siguiente operación DEBERÁ probar Redis de nuevo: si responde, se vuelve a usar; si falla, se abre otro periodo. `REDIS_CIRCUIT_OPEN_SECONDS=0` DEBERÁ desactivarlo. EL sistema DEBERÁ registrar un `WARNING` al abrirse y un `INFO` al cerrarse; mientras está abierto, las operaciones saltadas solo en `DEBUG`.

### F. Logs

- **RF-17.** Cada resolución DEBERÁ registrarse (`INFO`: acierto o fallo de cache con la región; `WARNING`: límite agotado; `ERROR`: forma inesperada), con el código postal y la región. **Nunca** el token CSRF, el `visitorId`, las cookies ni las coordenadas o direcciones devueltas por Alcampo.

## 5. Requisitos no funcionales

- **RNF-1. Contrato intacto:** mismos campos. Lo nuevo son los `404` de código postal sin servicio, que coinciden con Mercadona.
- **RNF-2. Sin dependencias nuevas** (constitución #1).
- **RNF-3. Secretos:** el token CSRF, el `visitorId` y las cookies de sesión son tokens efímeros. Nunca se registran, nunca se commitean y los tests usan valores sintéticos (constitución #12). Dónde se guardan lo decide D5.
- **RNF-4. Tests sin red:** toda la cadena con `respx` y fixtures (la Fase 0 ya guardó las de los pasos 1, 2, 3 y 5).
- **RNF-5. Docs vivas:** README y `.env.example` (que edita el usuario a mano).
- **RNF-6. Entrega por bloques** (D9).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `postal_code=2800`, `abcde`, `280011` | `422` sin tocar nada (RF-1) |
| `99999` (no existe) | `404`; la siguiente vez sale de la cache negativa (RF-4) |
| CP sin servicio (`NOT_DELIVERABLE`) | `404`, igual que inexistente (RF-4). No observado en vivo (D1e) |
| Dos CPs de la misma región | una sola sesión y la misma cache de búsqueda (RF-9, RF-12) |
| CP de Canarias y CP de Madrid, mismo término | resultados y `warehouse` distintos (RF-13) |
| Muchos CPs nuevos a la vez | como mucho `REGION_RESOLUTION_LIMIT` resoluciones por ventana; el resto `502` inmediato (RF-8) |
| El mismo CP nuevo en 10 peticiones simultáneas | 1 resolución compartida (RF-7) |
| Challenge del WAF durante una resolución | enfriamiento como en la 002/008; nada se cachea; `502` |
| La sesión de una región caduca | se renueva o se comprueba antes de buscar; nunca resultados de otra región (RF-10) |
| Redis caído | `200` sin cache y `WARNING`; protecciones según D7 (RF-15) |
| Redis colgado | a los ~2 s se trata como caído, no una petición colgada (RF-14) |
| Alcampo cambia la región de un CP antes de que caduque la cache | se acepta la ventana de la TTL (D2) |

## 7. Fuera de alcance

- **Tabla estática de prefijos CP → región:** quedaría desactualizada; siempre se resuelve contra Alcampo (con cache).
- **Comparar precios entre regiones.**
- **Protección frente a enumeración de CPs** más allá de RF-8 y de la API key (spec 004).
- **Superar el challenge del WAF** (exigiría ejecutar JS de AWS: prohibido por la constitución #2).
- **Precargar regiones** al arrancar.

## 8. Criterios de finalización

- [ ] **Antes del plan:** verificación en vivo de D1, con el presupuesto de D1 y anotada en el plan.
- [ ] RF-1 a RF-17 cubiertos por tests en verde con `respx` y fixtures, sin llamar a Alcampo real.
- [ ] Un test demuestra que N búsquedas simultáneas del mismo CP nuevo producen **1** resolución.
- [ ] Un test demuestra que con el límite de resoluciones agotado no sale **ninguna** petición de resolución y los CPs ya resueltos siguen funcionando.
- [ ] Un test demuestra que una sesión que no puede garantizar su región da `502`, nunca resultados de otra región.
- [ ] Un test demuestra que un Redis que no responde da `200` sin cache en torno a `REDIS_TIMEOUT_SECONDS`.
- [ ] La suite de las specs 001–005 y 008 sigue en verde.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] README y `.env.example` actualizados.
- [ ] **Verificación manual** con `docker compose`: `28001` y `35001` dan `warehouse` y precios distintos; repetir no vuelve a resolver; `99999` → `404`; `2800` → `422`; con Redis parado → `200` sin cache. Respetando el presupuesto de D1.

## 9. Decisiones (dudas resueltas el 2026-09-28)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Verificación en vivo previa (Fase 0 bis) | **Se hace antes del plan.** Comprueba: **(a)** si una sesión confirmada conserva su región pasada 1 h o tras un rato inactiva; **(b)** si la búsqueda o algún endpoint barato dice en qué región está la sesión; **(c)** de dónde sale el `retailerRegionId`; **(d)** si una región se puede reconfirmar en una sesión nueva reutilizando el destino creado, sin repetir el paso 4; **(e)** la respuesta real de un CP `NOT_DELIVERABLE` (`07001`). Ritmo de la Fase 0: ≤12 peticiones por ronda, 30 s entre peticiones, ≤1 creación de destino por ronda y 10 min entre rondas; unas 3–4 rondas. Si (a) exige esperar horas, se asume lo peor (caduca a la hora) | Los resultados se anotan en el plan (§2) y deciden D6. Ante un challenge: parar, esperar el enfriamiento y avisar |
| D2 | TTL de la cache CP → región | `REGION_CACHE_TTL_SECONDS = 604800` (7 días) | RF-3. Resolver cuesta ~8 peticiones y la más sensible al WAF; las regiones de un CP cambian muy poco |
| D3 | TTL de la cache negativa | `REGION_NEGATIVE_CACHE_TTL_SECONDS = 3600` (1 h), como Mercadona | RF-4. Un CP inexistente cuesta 1 sola petición |
| D4 | Límite de resoluciones nuevas | `REGION_RESOLUTION_LIMIT = 2` por `REGION_RESOLUTION_WINDOW_SECONDS = 600`, configurable. **Enmienda:** cuenta solo la **creación de destinos**; renovar sesiones no (RF-11) | RF-8. Por debajo de la hipótesis de 3–4 creaciones de destino antes del bloqueo. Un pico de CPs nuevos recibe `502` |
| D5 | Dónde viven las sesiones por región | En memoria de cada proceso | RF-9. Las cookies no salen del proceso. Con varias instancias, cada una confirma sus sesiones (cuenta contra RF-8, global) |
| D6 | Garantizar que la sesión no ha perdido su región | **Tras D1:** renovar la sesión de cada región cuando tenga más de `SESSION_MAX_AGE_SECONDS = 3000` (50 min), reutilizando su destino (4 peticiones, ninguna crea destino), y comprobar con el HTML de `GET /` que la región confirmada es la esperada. Si no coincide, `502`. D1 vio que una sesión conserva la región tras 65 min inactiva, pero una sola prueba no basta para confiar sin renovar | RF-10, RF-11 |
| D7 | Redis caído: protecciones | Respaldo en memoria por proceso para el límite global, el de resoluciones y el enfriamiento; la cache se salta | RF-15. Se pierde la coordinación entre instancias, pero cada proceso se sigue protegiendo |
| D8 | Respuesta a CP sin servicio | `404 {"detail": "Postal code not served by Alcampo"}` | RF-4. Mismo contrato que Mercadona |
| D9 | Entrega | 3 PRs encadenados: **E** (Redis), **A+B** (validación y resolución), **C+D** (sesiones y cache por región) | Lo más urgente e independiente (Redis) va primero |

## 10. Enmienda del 2026-09-30: circuit breaker de Redis

La verificación manual de T13 mostró dos efectos de RF-15 tal como se implementó:

- **Sin Redis, cada búsqueda tardaba ~9 s:** las ~4 operaciones con Redis de una búsqueda esperaban cada una su timeout de 2 s, en cada búsqueda.
- **Con el contenedor de Redis parado, asyncio registraba falsos `ERROR`** (`Future exception was never retrieved`, 3 por búsqueda): la consulta DNS del nombre `redis` dura más que el timeout y su error queda sin recoger.

El usuario decidió incluir la corrección en esta spec (RF-18). Con el circuito abierto, una búsqueda sin Redis cuesta un solo timeout al abrirse y ninguno después, y los intentos (y su ruido) bajan a uno cada `REDIS_CIRCUIT_OPEN_SECONDS`.

| # | Duda | Decisión (aprobada el 2026-09-30) | Consecuencia |
|---|---|---|---|
| D10 | Duración del circuito abierto | 10 s por defecto, configurable; `0` lo desactiva | Tras recuperarse Redis, como mucho 10 s de búsquedas sin cache por proceso |
| D11 | Alcance | Un circuito **por proceso** para todo Redis (cache, límites, enfriamiento, regiones), creado en el `lifespan` | Coherente con los respaldos locales de RF-15 |
| D12 | Logs | `WARNING` al abrir, `INFO` al cerrar; operaciones saltadas en `DEBUG` | Una línea por incidente en vez de una por operación |
