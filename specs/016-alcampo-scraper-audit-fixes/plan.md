# Plan 016 — Fallos encontrados en la auditoría del repositorio

- **Estado:** aprobado (2026-10-16), con las decisiones recomendadas
- **Fecha:** 2026-10-16
- **Spec:** [spec.md](spec.md). Sus decisiones se citan como **spec-D1…spec-D4**; las de este plan, **D1…**.

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `app/services/waf_cooldown.py` | `activate`: con el enfriamiento activo devuelve lo que queda, sin crecer; `LocalCooldown` igual | RF-1 |
| `app/services/region_repository.py` | `forget_region(region)` compara el destino y borra con `WATCH` | RF-2 |
| `app/services/region_sessions.py` | pasa la región entera a `forget_region` | RF-2 |
| `app/core/config.py` | `region_resolution_timeout_seconds: float = Field(30, gt=0)` | RF-3 |
| `app/services/region_service.py` | `_resolve_within_timeout` usa el nuevo ajuste | RF-3 |
| `README.md`, `.env.example` | línea de estado; la variable nueva (`.env.example`, a mano) | RF-3, RF-4 |

## 2. Decisiones de diseño

**D1 — Un desafío durante el enfriamiento no lo toca** (spec-D1):
- En Redis, `activate_in_redis` lee primero `TTL waf:cooldown`. Si es `> 0`, devuelve ese valor sin escribir nada.
- Si no, sigue como hoy: crece desde `waf:cooldown:last`.
- `LocalCooldown.activate` hace lo mismo: si `is_active()`, devuelve `ceil(until - now)` y no cambia `_last`.
- El espejo local (`hold`) se llama en **cada** desafío, con lo que queda en Redis como fin y la duración completa (`waf:cooldown:last`) como última, nunca lo que queda. *(Corregido en T5: el borrador solo lo llamaba cuando Redis iniciaba el enfriamiento, y así una instancia que se sumaba al de otra quedaba sin enfriamiento local, contra la spec 015 RF-9.)*
- Dos `activate` simultáneos sin enfriamiento siguen leyendo los dos `last` vacío y ponen 180: igual que hoy, sin crecer.

**D2 — Comparar y borrar** (spec-D2): `forget_region(region: Region)`:
- borra la L1 siempre;
- en Redis, `WATCH region:{id}`, `GET`, y solo si el JSON es válido y su `delivery_destination_id` coincide con el de `region`, `MULTI` + `DEL`;
- un `WatchError` (otro proceso lo cambió en medio) cuenta como "ya no es el nuestro": no se borra;
- todo dentro de una sola llamada al circuito; un `RedisError` deja el `WARNING` degradado, como hoy.

**D3 — Su propio tiempo máximo**: `REGION_RESOLUTION_TIMEOUT_SECONDS` (30 s): el doble del de una búsqueda, para 9 peticiones espaciadas más la cola del pacer. La renovación de sesión (4 peticiones) sigue bajo `SEARCH_TIMEOUT_SECONDS`, dentro de la búsqueda.

## 3. Estrategia de test

| RF | Test | Fichero |
|---|---|---|
| RF-1 | Redis: `activate` dos veces seguidas → 180 y luego lo que queda (≤ 180); `waf:cooldown:last` sigue en 180. Local: lo mismo con un reloj falso. Caducado y reciente → 360 (regresión) | `tests/services/test_waf_cooldown.py` |
| RF-1 | El caso de la spec §1: dos búsquedas en vuelo bloqueadas una tras otra → un solo `cooldown_s=180` y `waf:cooldown:last == 180` | `tests/services/test_product_service.py` |
| RF-2 | El caso de la spec §1 con dos repositorios sobre el mismo `fakeredis`: el registro nuevo sobrevive; con el destino coincidente, se borra (regresión) | `tests/services/test_region_repository.py` |
| RF-3 | Valor por defecto 30; `0` y `-1` → `ValidationError`; una resolución que tarda más que `SEARCH_TIMEOUT_SECONDS` y menos que la nueva termina bien | `tests/core/test_config.py`, `tests/services/test_region_service.py` |

## 4. Entrega

Un PR de unas 150 líneas de código y tests. T3 se para para que el usuario añada la variable a `.env.example`.
