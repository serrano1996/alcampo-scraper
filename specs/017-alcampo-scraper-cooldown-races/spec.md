# Spec 017 — Carreras al iniciar el enfriamiento del WAF

- **Estado:** aprobada (2026-10-16), con las decisiones recomendadas de la sección 8
- **Fecha:** 2026-10-16
- **Referencia:** avisos A1 y A2 de la revisión con contexto nuevo de la spec 016 (`specs/016-alcampo-scraper-audit-fixes/tasks.md`, T5), y el aviso de su recomprobación.

## 1. Contexto y objetivo

Desde la spec 016 (RF-1), un desafío que llega con el enfriamiento activo se suma a él y no lo hace crecer. Pero el paso que decide si "hay enfriamiento" y el que lo inicia son comandos sueltos (`TTL`, `GET`, `SET`, `SET`), no uno atómico. Reproducido el 2026-10-16 (`54a31b2`), sin llamar a Alcampo:

| # | Fallo | Evidencia | Efecto |
|---|---|---|---|
| F1 | Dos inicios simultáneos duplican el enfriamiento | A y B (mismo `FakeServer`). B lee `TTL` (sin enfriamiento); A inicia el suyo; B lee `waf:cooldown:last = 180` y pone 360 → `A 180, B 360, ttl 360` | un único bloqueo para el servicio el doble, lo que la spec 016 RF-1 quería evitar. Pasa entre instancias y entre tareas del mismo proceso |
| F2 | Un enfriamiento activo sin `waf:cooldown:last` se reescribe con la duración base | `waf:cooldown` con 600 s y sin `last` → `activate` devuelve 180 y el `TTL` queda en 180 | se acorta un enfriamiento más largo. Hoy solo pasa si alguien borra `last` a mano, porque `last` dura `WAF_COOLDOWN_MAX_SECONDS ≥ WAF_COOLDOWN_SECONDS` |
| F3 | `TTL` redondea a segundos | en el último medio segundo de un enfriamiento, `TTL` puede dar `0` con la clave aún viva (no verificado contra un Redis real) | un desafío justo ahí se trata como "sin enfriamiento" y lo hace crecer. Ventana despreciable; se cierra con lo mismo que F1 |

**Objetivo:** que iniciar el enfriamiento en Redis sea atómico: un solo inicio por bloqueo, y quien llega después se suma, sin escribir nada.

## 2. Historias de usuario

- **H1.** Como responsable del servicio, quiero que un bloqueo del WAF cueste un enfriamiento aunque varias instancias o búsquedas reciban el desafío a la vez.

## 3. Requisitos funcionales (EARS)

- **RF-1.** CUANDO llegue un desafío y no haya enfriamiento, EL sistema DEBERÁ iniciarlo con una escritura que solo tenga efecto si la clave no existe (`SET … NX`). SI otro lo inició antes, ENTONCES DEBERÁ sumarse al que haya, sin escribir nada (D1).
- **RF-2.** MIENTRAS haya un enfriamiento activo, un desafío NO DEBERÁ reescribirlo aunque falte `waf:cooldown:last`. Su duración completa, para el espejo local, será el mayor entre `last` y lo que queda (D2).
- **RF-3.** Lo que queda DEBERÁ medirse con `PTTL`, redondeado hacia arriba a segundos, para que una clave viva nunca cuente como "sin enfriamiento" (D3).

## 4. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas.**
- **RNF-2. TDD:** cada RF con un test que falle antes del cambio y reproduzca su caso de la sección 1.
- **RNF-3. Sin cambios en el contrato** de la API ni en las claves de Redis (`waf:cooldown`, `waf:cooldown:last`).
- **RNF-4.** Lo de la spec 015 RF-9 y la spec 016 (RF-1, T5) se mantiene: cada desafío arma el enfriamiento local, y su crecimiento sigue desde la duración completa.

## 5. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Primer desafío, nadie más | 180 s, como hoy |
| Desafío tras caducar, dentro de `WAF_COOLDOWN_MAX_SECONDS` | crece (360 s), como hoy |
| Dos inicios simultáneos | uno lo inicia (180); el otro se suma (≤ 180); `last = 180` |
| `waf:cooldown` sin caducidad (`PTTL = -1`, puesto a mano) | se trata como activo y se respeta. Para quitarlo, se borra la clave |
| Redis caído | el respaldo local, como hoy |

## 6. Fuera de alcance

- Hacer atómico el paso con un script Lua: `fakeredis` solo lo ejecuta con `lupa`, una dependencia nueva (RNF-1).
- La línea `ERROR` repetida por cada búsqueda que esperaba la misma resolución de región (spec 016 §6).

## 7. Criterios de finalización

- [x] F1, F2 y F3, reproducidos en un test RED y en verde tras el cambio.
- [x] `ruff`, `mypy` y `pytest` en verde.
- [x] README actualizado si cambia algo visible (no se espera).

## 8. Decisiones (dudas resueltas el 2026-10-16)

| # | Duda | Decisión |
|---|---|---|
| D1 | Atomicidad: ¿`SET NX` o una transacción con `WATCH`? | `SET NX`: un comando, sin reintentos. Quien pierde solo tiene que leer lo que hay |
| D2 | Enfriamiento activo sin `last`: ¿sumarse o reescribirlo? | Sumarse: acortarlo es peor que no saber su duración completa |
| D3 | `waf:cooldown` sin caducidad: ¿respetarlo o reescribirlo con caducidad? | Respetarlo: un operador que lo pone a mano quiere parar el tráfico; la spec 016 ya dejó que "mientras Redis responde, decide Redis" |
| D4 | Entrega | Una spec, un PR, una tarea |
