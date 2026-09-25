# Plan 002 — Medidas antibaneo

- **Estado:** aprobado (2026-09-25). Entrega: 2 PRs encadenados (§9)
- **Fecha:** 2026-09-25
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D5**; las de la 001 como **001-plan-Dn** / **001-spec-Dn**. Las decisiones de diseño de este plan son **D1…**

## 1. Visión general

```
create_http_client(settings, choose=random.choice)
  └─ User-Agent del pool (1 vez por cliente) + Referer + ecom-request-source      RF-1…RF-4

send_with_retry(send, max_attempts, base_delay, jitter_max, sleep, uniform, now)
  ├─ WAF challenge ──────────────► UpstreamBlockedError (sin espera)             RF-12
  ├─ 2xx ────────────────────────► respuesta
  ├─ 429 ─► espera = Retry-After | backoff, + jitter, tope 60 s                   RF-5…RF-9
  ├─ 5xx / transporte ─► espera = backoff + jitter                                RF-9
  └─ otro 4xx / agotado ─► UpstreamUnavailableError                               RF-11, RF-13

ProductService.search
  1. cache hit ─────────────────────► 200                                         RF-17
  2. marca de enfriamiento activa ──► UpstreamUnavailableError (0 peticiones)     RF-16
  3. scraper.search
       └─ UpstreamBlockedError ─► activar marca (TTL WAF_COOLDOWN_SECONDS) y relanzar   RF-15
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `retry_jitter_max_s: float = Field(0.3, ge=0)` y `waf_cooldown_seconds: int = Field(180, ge=0)` | RF-10, RF-19 |
| `app/scrapers/http_client.py` | `USER_AGENTS`, `ALCAMPO_REFERER`; `create_http_client(settings, *, choose=random.choice)` | RF-1…RF-4 |
| `app/scrapers/retry.py` | `parse_retry_after()`, cálculo de espera con jitter y tope, nuevos parámetros inyectables; el WAF lanza `UpstreamBlockedError` | RF-5…RF-9, RF-11…RF-13 |
| `app/scrapers/alcampo_search.py` | pasa `jitter_max=settings.retry_jitter_max_s` a `send_with_retry` | RF-9 |
| `app/exceptions.py` | **nuevo** `UpstreamBlockedError(UpstreamUnavailableError)` | RF-12, RF-14, RF-15 |
| `app/services/waf_cooldown.py` | **nuevo** `WafCooldownRepository` (`is_active`, `activate`) | RF-15, RF-16, RF-18, RF-19 |
| `app/services/product_service.py` | comprueba la marca tras la cache; la activa ante `UpstreamBlockedError` | RF-15…RF-17 |
| `app/core/dependencies.py` | inyecta `WafCooldownRepository` en `ProductService` | RF-15 |
| `README.md`, `.env.example` | documentan las 2 variables nuevas y el enfriamiento | RNF-6 |

Tests en espejo, más escenarios nuevos en `tests/integration/`.

## 3. Modelo de datos

### `Settings` (nuevos campos)

```python
retry_jitter_max_s: float = Field(default=0.3, ge=0)
waf_cooldown_seconds: int = Field(default=180, ge=0)
```

### Redis

| Clave | Valor | TTL | Nota |
|---|---|---|---|
| `search:{warehouse}:{term}` | sin cambios (001) | `CACHE_TTL_SECONDS` | |
| `waf:cooldown` | `"1"` | `WAF_COOLDOWN_SECONDS` | **global**, una sola clave (spec RNF-4) |

### Excepciones

```python
class UpstreamBlockedError(UpstreamUnavailableError):
    """Alcampo's WAF answered with a challenge: the egress IP is blocked for minutes."""
```

## 4. Decisiones de diseño

**D1 — `UpstreamBlockedError` como subclase de `UpstreamUnavailableError`.** El servicio necesita distinguir "WAF" de "upstream caído" para activar el enfriamiento. Al ser subclase, todo el código que ya captura `UpstreamUnavailableError` (incluido el handler `502` de `main.py`) sigue funcionando sin cambios, porque Starlette busca el handler recorriendo la MRO de la excepción.
- *Descartada:* inspeccionar `exc.reason == "WAF challenge"`. Frágil: acopla el comportamiento a un texto pensado para logs.
- *Descartada:* excepción nueva no relacionada. Obligaría a registrar otro handler y a tocar cada `except`.
- Cumple lo previsto en 001-plan-D7: "se subdividirá cuando algún comportamiento dependa del tipo".

**D2 — El enfriamiento vive en el servicio, no en `send_with_retry` ni en el scraper.** `send_with_retry` y `AlcampoSearchScraper` siguen sin conocer Redis. El servicio, que ya orquesta cache → scraper, añade "marca → scraper → activar marca".
- *Descartada:* que `send_with_retry` consulte y escriba la marca. Mezclaría transporte con almacenamiento y obligaría a inyectar Redis en una función pura (001-plan-D3).
- *Descartada:* un middleware HTTP. Actuaría antes de la cache y rompería RF-17 (las búsquedas cacheadas deben seguir funcionando).

**D3 — Orden en el servicio: cache → marca → scraper.** Es exactamente lo que piden RF-16 y RF-17. La marca se lee solo en un miss, así que un hit no añade ninguna lectura a Redis.
- *Descartada:* marca → cache. Rompería RF-17.

**D4 — Repositorio propio para la marca (`WafCooldownRepository`)**, en lugar de meter la lógica en `SearchCacheRepository`.
- *Descartada:* ampliar `SearchCacheRepository`. Son dos responsabilidades distintas (datos cacheados frente a estado del upstream), con claves y TTL distintos.

**D5 — `activate(ttl_seconds)` con `SET waf:cooldown 1 EX ttl`; `ttl_seconds == 0` no escribe nada (RF-19).** No se usa `NX`: si llegara un segundo challenge (solo posible si varias peticiones estaban ya en vuelo), renovar el TTL es correcto, porque el bloqueo se ha vuelto a confirmar.
- *Descartada:* `SET NX`. Mantendría el TTL original aunque el WAF acabe de confirmar el bloqueo.

**D6 — Jitter aditivo `uniform(0, jitter_max)`, sumado a todas las esperas** (`5xx`, transporte y `429`). Mismo criterio que Mercadona (su D4).
- *Descartada:* jitter multiplicativo ("full jitter" de AWS). Con esperas base de 0.5–2 s, un aditivo acotado basta para romper la regularidad y no produce esperas desproporcionadas.

**D7 — `parse_retry_after(value, now) -> float | None`, función pura.**
- Segundos: solo enteros no negativos (`^[0-9]+$`), como dice el estándar. `"1.5"` o `"-5"` → `None` → backoff (RF-6).
- Fecha: `email.utils.parsedate_to_datetime`; si viene sin zona se asume UTC. Fecha pasada → `0.0` (RF-7).
- Cualquier otra cosa → `None`.
- *Descartada:* aceptar segundos decimales. El estándar no lo permite y no hay evidencia de que Alcampo los use (ni siquiera se ha visto un `429`).

**D8 — Tope de 60 s aplicado a la espera final, jitter incluido, solo en esperas por `429`** (RF-8): `min(base + jitter, 60.0)`. Las esperas de `5xx` y transporte no llevan tope: su base máxima con la configuración por defecto es 1 s.
- *Descartada:* topar la base y sumar el jitter después. Podría superar los 60 s que promete la spec.

**D9 — Parámetros nuevos de `send_with_retry` con valores por defecto neutros.** `jitter_max: float = 0.0`, `uniform = random.uniform`, `now = lambda: datetime.now(UTC)`. Con `jitter_max=0.0`, el comportamiento es idéntico al de la 001, así que **todos los tests de `test_retry.py` existentes siguen valiendo sin tocarlos**. Solo el scraper pasa el valor real desde `Settings`.
- *Descartada:* parámetros obligatorios. Obligaría a reescribir los 9 tests de la 001 sin cambiar lo que verifican.

**D10 — Elección de User-Agent inyectable: `create_http_client(settings, *, choose=random.choice)`.** Los tests fijan la elección (`choose=lambda pool: pool[3]`) y verifican que el valor sale del pool.
- *Descartada:* `random.seed()` en los tests. Acopla el test al orden interno del generador y afecta a otros tests.

**D11 — Cabeceras fijas nuevas en la factoría:** `Referer: https://www.compraonline.alcampo.es/` y `ecom-request-source: web` (spec-D2, spec-D3). El `Referer` es **constante**, no deriva de `ALCAMPO_BASE_URL`.
- *Descartada:* construir el `Referer` desde `ALCAMPO_BASE_URL`. En los tests esa URL es `https://alcampo.test`, y enviaríamos un `Referer` falso en producción si alguien apuntara la variable a un proxy. El Referer debe imitar al navegador real, no a nuestra configuración.

**D12 — Integración con `RETRY_JITTER_MAX_S=0`** en `tests/integration/conftest.py`. Los tests de integración usan el `asyncio.sleep` real, y con jitter por defecto (0.3) el escenario de `503` persistente esperaría hasta 0.6 s reales.

## 5. Regresiones previstas en tests de la 001

| Test | Por qué se rompe | Corrección (tarea dedicada) |
|---|---|---|
| `tests/scrapers/test_http_client.py::test_create_http_client_sets_realistic_headers` | exige `"Chrome/"` en el UA; con el pool puede salir Firefox o Safari | fijar `choose` y comprobar pertenencia al pool |
| `tests/integration/test_products_endpoint.py::test_waf_challenge_returns_502_with_a_single_call` | exige `redis.dbsize() == 0` tras un challenge; ahora queda `waf:cooldown` | comprobar que no hay claves `search:*` y que **sí** existe `waf:cooldown` |

**Corrección (2026-09-25):** la regresión de WAF la provoca T11 (al conectar el enfriamiento en `dependencies.py`), así que se corrige en T11 y no en T13, como se planteó al principio. Sigue estando en PR2.

Los tests de `test_retry.py` no se rompen gracias a D9. El test `retry` de WAF (`pytest.raises(UpstreamUnavailableError)`) sigue pasando porque `UpstreamBlockedError` es subclase (D1).

## 6. Estrategia de test por RF

**U** = unitario, **I** = integración.

| RF | Test | Tipo |
|---|---|---|
| RF-1 | `choose` se llama **una vez** por `create_http_client`; dos peticiones del mismo cliente llevan el mismo UA | U |
| RF-2 | `USER_AGENTS` es exactamente la tupla de la spec (6 entradas, sin duplicados); el UA elegido pertenece al pool | U |
| RF-3 | `Accept`, `Accept-Language`, `Referer` y `ecom-request-source` presentes con el valor exacto | U |
| RF-4 | la petición no lleva `Origin` (se comprueba en la petición capturada por `respx`) | U |
| RF-5 | `parse_retry_after("120")` → `120.0`; fecha HTTP 30 s en el futuro (con `now` fijo) → `30.0`; `429` + `Retry-After: 2` → espera registrada `2 + jitter` | U |
| RF-6 | `"abc"`, `""`, `"-5"`, `"1.5"`, `None` → `None`; `429` sin cabecera → backoff | U |
| RF-7 | fecha pasada → `0.0` | U |
| RF-8 | `Retry-After: 3600` → espera `60.0`; `Retry-After: 60` con jitter 0.3 → `60.0`, no `60.3` | U |
| D4 | `503` + `Retry-After: 30` → espera = backoff, no 30 | U |
| RF-9 | `503, 200` con `uniform` fijo en `0.2` → espera `0.5 + 0.2`; transporte igual; `uniform` recibe `(0, jitter_max)` | U |
| RF-9 | el scraper pasa `settings.retry_jitter_max_s` a `send_with_retry` | U |
| RF-10 | default `0.3`; negativo → `ValidationError`; `0` válido | U |
| RF-11 | `429 × 3` → `UpstreamUnavailableError` y 2 esperas | U (regresión) |
| RF-12 | challenge → `UpstreamBlockedError`, 0 esperas, 1 llamada | U |
| RF-13 | `404` → sin reintento (test de la 001, sigue pasando) | U (regresión) |
| RF-14 | handler: `UpstreamBlockedError` → `502` con el `detail` estándar | U (API) |
| RF-15 | servicio: el scraper lanza `UpstreamBlockedError` → marca activa con TTL `WAF_COOLDOWN_SECONDS`, y la excepción se propaga | U |
| RF-15 | servicio: `UpstreamUnavailableError` normal → **no** activa la marca | U |
| RF-16 | marca activa + miss → `UpstreamUnavailableError`, 0 llamadas al scraper | U + I |
| RF-17 | marca activa + hit → respuesta de cache | U + I |
| RF-18 | repositorio: tras borrar/expirar la clave, `is_active()` → `False` | U |
| RF-19 | default `180`; negativo → `ValidationError`; `activate(0)` no escribe clave | U |
| — | integración end-to-end: búsqueda A cacheada → challenge en B (1 llamada) → B otra vez (0 llamadas nuevas, `502`) → A (`200`) | I |

## 7. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | El enfriamiento (180 s) es menor que el bloqueo máximo observado (~4 min) | 1–2 challenges extra por episodio | Aceptado en la spec (§6). Configurable |
| R2 | El pool de UA envejece | UA desfasado = señal de bot | Deuda registrada en la spec (revisión ~trimestral) |
| R3 | Un UA de Firefox o Safari con cabeceras que un Firefox real no enviaría igual (orden, `sec-*`) | Fingerprint imperfecto | Aceptado: imitar huellas completas está fuera de alcance (spec §7) |
| R4 | `respx` para distinguir búsquedas por término en integración | Tests de integración mal montados | Rutas con `params={"q": …}`; si `respx` no permite el matcheo parcial de params, se usa `side_effect` por petición. Se verifica en la tarea de integración |
| R5 | La verificación real del nuevo fingerprint dispara el WAF | IP bloqueada 2–4 min | 1 sola petición, con ≥10 min desde la última |

## 8. Secuencia de implementación

1. **Config:** `retry_jitter_max_s`.
2. **Fingerprint:** pool, `choose`, cabeceras (+ regresión del test de UA).
3. **`Retry-After`:** `parse_retry_after`.
4. **Esperas:** jitter en `send_with_retry`, `Retry-After` en `429`, tope de 60 s.
5. **Scraper:** pasar `jitter_max`; `RETRY_JITTER_MAX_S=0` en integración.
6. **Excepción:** `UpstreamBlockedError` en el WAF (+ test del handler).
7. **Config:** `waf_cooldown_seconds`.
8. **Repositorio** `WafCooldownRepository`.
9. **Servicio y dependencias:** marca tras cache; activación ante bloqueo.
10. **Integración:** escenario end-to-end (la regresión del test de WAF se corrige en el paso 9, donde aparece).
11. **Docs** y verificación real con 1 búsqueda.

## 9. Estimación y entrega

| Bloque | `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| 1–5 Fingerprint, `Retry-After`, jitter, tope | ~110 | ~220 | — | ~330 |
| 6–11 Excepción, enfriamiento, integración, docs | ~70 | ~190 | ~35 | ~295 |
| **Total** | **~180** | **~410** | **~35** | **~625** |

**Supera las 400 líneas**, así que propongo **2 PRs encadenados**, igual que en la 001:

```
main ◄── PR1 fingerprint + Retry-After + jitter (~330) ◄── PR2 enfriamiento WAF + docs (~295)
```

- Cada PR deja la suite en verde. Las dos regresiones de §5 se corrigen en el mismo PR que las provoca (la de UA en PR1, la de WAF en PR2), así que ningún PR queda en rojo.
- PR1 es paridad pura con Mercadona; PR2 es lo específico de Alcampo. Se pueden revisar por separado con criterios distintos.

## 10. Qué no cambia

Contrato de la API (`ProductSearchResponse`, `422`, `502`), clave de cache de búsqueda, `DEFAULT_WAREHOUSE`, mapper y modelos.
