# Spec 016 — Fallos encontrados en la auditoría del repositorio

- **Estado:** aprobada (2026-10-16), con las decisiones recomendadas de la sección 8
- **Fecha:** 2026-10-16
- **Referencia:** auditoría completa de `app/` del 2026-10-16 (hallazgos 1 a 5).

## 1. Contexto y objetivo

Cada caso se ha **reproducido** el 2026-10-16 (`9078ebd`) con un `.venv` de Python 3.11, sin llamar a Alcampo, salvo F3, que se razona con números:

| # | Fallo | Evidencia | Efecto |
|---|---|---|---|
| F1 | Un mismo bloqueo del WAF hace crecer el enfriamiento varias veces | dos búsquedas distintas ya enviadas reciben el desafío una después de otra → `cooldown_s=180` y luego `cooldown_s=360`; `waf:cooldown:last = 360` | un único bloqueo para el servicio el doble (o más) de lo necesario, y el log habla de "bloqueos repetidos" que no existieron |
| F2 | Con varios procesos, uno borra de Redis el registro de región bueno que otro acaba de guardar | A guarda `region:r` (destino `old`); B lo lee y lo deja en su L1; A lo olvida y guarda el destino `new`; B renueva con `old`, recibe un `4xx` y llama a `forget_region` → `region:r` queda vacío | otra resolución completa (crear un destino, el paso que más castiga el WAF) y otro hueco del límite de 2 cada 10 min, por cada proceso con la copia vieja |
| F3 | La resolución de un CP nuevo comparte el tiempo máximo de una búsqueda | 9 peticiones (6 de la cadena + 3 para confirmar la sesión) espaciadas 0,5–1 s por el `OutboundPacer`, que comparte cola con todo el tráfico del proceso: ~7–9 s sin carga; cada búsqueda fría en cola suma ~1 s; `SEARCH_TIMEOUT_SECONDS=15` | con unas 5 búsquedas frías a la vez, la resolución se corta **después** de crear el destino: hueco gastado, `502`, y dos casos así bloquean los CP nuevos 10 min |
| F4 | `fallback=None` y `circuit=None` crean copias privadas en silencio | `OutboundRateLimiter`, `WafCooldownRepository`, `SearchCacheRepository` y `RegionRepository` | un llamador que olvide pasar los compartidos obtiene un límite o un circuito que no protege nada; hoy producción los pasa todos |
| F5 | La línea de estado del README se quedó en la spec 008 | `README.md`, primer párrafo | parece que las specs 009–015 no están implementadas |

**Objetivo:** corregir F1, F2, F3 y F5 sin cambiar el contrato de la API. F4 queda a decisión (D3).

## 2. Historias de usuario

- **H1.** Como responsable del servicio, quiero que un bloqueo del WAF cueste un enfriamiento, no varios.
- **H2.** Como responsable del servicio, quiero que varias instancias no se pisen los registros de región y no gasten resoluciones de más.
- **H3.** Como consumidor, quiero que un CP nuevo se resuelva aunque haya otras búsquedas en curso.

## 3. Requisitos funcionales (EARS)

- **RF-1.** MIENTRAS haya un enfriamiento del WAF activo, un nuevo desafío NO DEBERÁ alargarlo ni hacerlo crecer: `activate` DEBERÁ devolver los segundos que le quedan. Vale igual con Redis (`waf:cooldown`) y sin él (`LocalCooldown`) (D1).
- **RF-2.** `forget_region` SOLO DEBERÁ borrar `region:{id}` de Redis si todavía guarda el destino que falló; si guarda otro, DEBERÁ dejarlo y borrar solo la copia del proceso (L1) (D2).
- **RF-3.** La resolución de un CP nuevo DEBERÁ tener su propio tiempo máximo, `REGION_RESOLUTION_TIMEOUT_SECONDS` (por defecto 30). Un valor menor o igual que 0 DEBERÁ impedir que la app arranque. `SEARCH_TIMEOUT_SECONDS` sigue limitando cada búsqueda y cada renovación de sesión.
- **RF-4.** El README DEBERÁ listar las specs implementadas hasta la 016 y documentar `REGION_RESOLUTION_TIMEOUT_SECONDS`.

## 4. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas.**
- **RNF-2. TDD:** cada RF de código con un test que falle antes del cambio y reproduzca su caso de la sección 1.
- **RNF-3. Sin cambios en el contrato** de `/api/v1/products`, `/health` ni `/ready`.
- **RNF-4. `.env.example` lo edita el usuario a mano** (la regla global de permisos lo impide, como en las specs 004 y 008). El test de sincronización lo exige.

## 5. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Primer desafío, sin enfriamiento | 180 s, como hoy |
| Desafío después de que caduque el enfriamiento, dentro de `WAF_COOLDOWN_MAX_SECONDS` | crece (360 s), como hoy |
| Desafío con el enfriamiento activo y Redis sano | devuelve lo que queda; `waf:cooldown:last` no cambia |
| Lo mismo con Redis caído | devuelve lo que queda del local; su última duración no cambia |
| `forget_region` con Redis caído | borra la L1 y deja el `WARNING` degradado, como hoy |
| `forget_region` cuando el registro ya no existe en Redis | no hace nada más que borrar la L1 |
| El registro cambia entre la lectura y el borrado | no se borra (transacción con `WATCH`) |

## 6. Fuera de alcance

- La línea `ERROR` del bloqueo se repite una vez por cada búsqueda que esperaba la misma resolución de región. Es ruido, pero no cambia el enfriamiento una vez aplicado RF-1.
- Que una sola página profunda con `page_size` pequeño gaste la ventana corta de todos: decisión documentada en la spec 009 (plan-D3).

## 7. Criterios de finalización

- [x] F1, F2 y F3, reproducidos en un test RED y en verde tras el cambio.
- [x] `ruff`, `mypy` y `pytest` en verde.
- [x] README y `.env.example` actualizados.

## 8. Decisiones (dudas resueltas el 2026-10-16)

| # | Duda | Decisión |
|---|---|---|
| D1 | F1: ¿no hacer crecer el enfriamiento, o además no registrar `ERROR` para un desafío durante uno activo? | Solo no crecer. Cambiar el log exige que `activate` devuelva si lo inició, y el `ERROR` sigue siendo útil para ajustar límites |
| D2 | F2: ¿comparar y borrar con `WATCH`, o borrar solo la L1 y dejar que Redis caduque? | `WATCH`: borrar solo la L1 haría que el proceso volviera a leer de Redis el mismo destino roto durante días |
| D3 | F4: ¿hacer obligatorios `fallback` y `circuit`? | **Descartarlo.** Son 54 llamadas en los tests para cubrir un riesgo hipotético, y hoy producción pasa los compartidos |
| D4 | Entrega | Una spec, un PR, una tarea por fallo |
