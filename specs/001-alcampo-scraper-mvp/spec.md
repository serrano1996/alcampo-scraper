# Spec 001 — MVP de búsqueda de productos

- **Estado:** aprobada (2026-09-25)
- **Fecha:** 2026-09-24
- **Evidencia:** [Fase 0](../../docs/investigacion/fase-0-alcampo.md)

## 1. Contexto y objetivo

Ya existe una API para Mercadona. Queremos su hermana para Alcampo, con **el mismo contrato de respuesta**, para que un consumidor compare ambos supermercados sin tocar su código.

Esta spec entrega lo mínimo útil: buscar productos por texto en Alcampo y devolverlos normalizados, con cache Redis y un manejo de errores de red predecible.

**Limitación consciente del MVP.** La Fase 0 demostró que Alcampo cambia precio y catálogo según la región (§3 del informe), y que fijar la región cuesta 8 peticiones y roza el WAF. En esta spec **toda búsqueda se hace en la región por defecto de la sesión anónima** ("Vaguada", Madrid). `postal_code` se acepta y se devuelve, pero **no influye todavía** en precios ni catálogo. La resolución real llega en la spec 007. Así replicamos la secuencia que siguió Mercadona.

## 2. Usuarios y actores

- **Consumidor de la API:** un servicio o script que compara precios entre supermercados. Llama a `GET /api/v1/products`.
- **Alcampo (upstream):** la web `compraonline.alcampo.es` (Ocado), con AWS WAF delante.
- **Redis:** cache de resultados.

## 3. Historias de usuario

- **H1.** Como consumidor, quiero buscar productos de Alcampo por texto y recibirlos en el mismo formato que Mercadona, para compararlos sin adaptar mi código.
- **H2.** Como consumidor, quiero que una búsqueda sin resultados me devuelva una lista vacía y no un error, para distinguir "no hay" de "algo falló".
- **H3.** Como consumidor, quiero un error claro y estable (`502`) cuando Alcampo no está disponible, para reintentar más tarde o degradar.
- **H4.** Como operador, quiero que las búsquedas repetidas salgan de cache, para no golpear Alcampo ni disparar su WAF.

## 4. Requisitos funcionales (EARS)

### Búsqueda

- **RF-1.** CUANDO el consumidor llame a `GET /api/v1/products?postal_code=<str>&term=<str>` con parámetros válidos, EL sistema DEBERÁ responder `200` con un cuerpo `ProductSearchResponse` (§6).
- **RF-2.** EL sistema DEBERÁ validar ambos parámetros con un modelo Pydantic declarado en la firma de la ruta (`Annotated[ProductQuery, Query()]`). SI falta alguno, `term` queda vacío tras recortar espacios o `term` supera 50 caracteres tras recortar, ENTONCES DEBERÁ responder `422` sin llamar a Alcampo ni a Redis (D6).
- **RF-3.** CUANDO se consulte Alcampo, EL sistema DEBERÁ hacer **una sola** petición (sin contar reintentos) a `GET {ALCAMPO_BASE_URL}/api/webproductpagews/v6/product-pages/search` con `q=<term>`, `tag=web`, `maxPageSize=50` y **`maxProductsToDecorate=50`**. Sin este último parámetro Alcampo devuelve solo IDs. No se sigue `nextPageToken` (D2).
- **RF-4.** EL sistema DEBERÁ reunir los productos de **todos** los `productGroups[*].decoratedProducts[]` de la respuesta, en orden. SI un `retailerProductId` aparece más de una vez, ENTONCES DEBERÁ conservar solo la primera aparición (D7).
- **RF-5.** CUANDO Alcampo devuelva `productGroups` vacío, EL sistema DEBERÁ responder `200` con `products: []` y `total_results: 0`.

### Mapeo del producto

- **RF-6.** EL sistema DEBERÁ mapear cada producto crudo a `Product` así: `id` ← `retailerProductId` (D3); `name` ← `name`; `price` ← `price.amount` convertido a número; `image_url` ← `image.src`; `category` ← **último** elemento de `categoryPath`, o `null` si viene vacío o ausente (D4).
- **RF-7.** CUANDO un producto tenga `unitPrice`, EL sistema DEBERÁ construir `price_format` como `"<importe> €/<unidad>"`, con el importe **tal como llega** en `unitPrice.price.amount` (string, sin reformatear; D9) y la unidad según la tabla de D8. Ejemplo: `{"amount": "0.88", "unitName": "PER_LITRE"}` → `"0.88 €/L"`.
- **RF-8.** SI un producto no tiene `unitPrice`, o su `unitName` no está en la tabla de D8, ENTONCES EL sistema DEBERÁ devolver `price_format: null` sin fallar la validación.
- **RF-9.** SI un producto individual no puede mapearse porque le falta un campo obligatorio (`retailerProductId`, `name`, `price.amount`) o su precio no es numérico, ENTONCES EL sistema DEBERÁ descartarlo y seguir con el resto, en vez de fallar toda la respuesta (D7).

### Metadatos de la búsqueda

- **RF-10.** EL sistema DEBERÁ rellenar `search` con: `postal_code` y `term` de la petición; `warehouse` = `"5"`, el `retailerRegionId` de la región por defecto "Vaguada" verificado en la Fase 0, definido como **constante en código** y no leído de Alcampo (D1); `strategy_used: "api"`; `scraped_at` = instante UTC de la consulta a Alcampo; `total_results` = número de productos devueltos.

### Cache

- **RF-11.** CUANDO llegue una búsqueda, EL sistema DEBERÁ consultar Redis antes que Alcampo. SI hay entrada, ENTONCES DEBERÁ devolverla sin llamar a Alcampo.
- **RF-12.** CUANDO una consulta a Alcampo tenga éxito (con o sin resultados), EL sistema DEBERÁ guardar la respuesta en Redis con TTL `CACHE_TTL_SECONDS` (por defecto 3600).
- **RF-13.** EL sistema NO DEBERÁ cachear respuestas de error.
- **RF-14.** CUANDO se sirva desde cache, `scraped_at` DEBERÁ conservar el instante de la consulta original a Alcampo.

### Errores y reintentos

- **RF-15.** CUANDO Alcampo responda `5xx` o `429`, o haya un error de transporte (timeout, conexión), EL sistema DEBERÁ reintentar hasta `RETRY_MAX_ATTEMPTS` intentos en total, con backoff exponencial (`RETRY_BASE_DELAY × 2^(n-1)`).
- **RF-16.** CUANDO Alcampo responda cualquier otro `4xx`, EL sistema NO DEBERÁ reintentar.
- **RF-17.** SI se agotan los reintentos, o Alcampo responde un `4xx` no reintentable, ENTONCES EL sistema DEBERÁ lanzar la excepción de dominio `UpstreamUnavailableError`, y la API DEBERÁ responder `502` con un `detail` propio (sin reenviar el cuerpo de Alcampo).
- **RF-18.** CUANDO Alcampo responda con la cabecera `x-amzn-waf-action` (challenge del WAF, llega como `202` vacío), EL sistema DEBERÁ tratarlo como fallo del upstream y **no** como éxito, **sin reintentar**: `UpstreamUnavailableError` → `502` en el primer intento (D5).
- **RF-19.** SI Alcampo responde `2xx` con un cuerpo que no es JSON o no encaja con el schema crudo (p. ej. sin `productGroups`), ENTONCES EL sistema DEBERÁ responder `502`.
- **RF-20.** Ningún tipo de `httpx` DEBERÁ cruzar la capa de servicio: los scrapers traducen a excepciones de `app/exceptions.py`.

### Configuración

- **RF-21.** EL sistema DEBERÁ leer `ALCAMPO_BASE_URL`, `REDIS_URL`, `CACHE_TTL_SECONDS`, `RETRY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY` y `LOG_LEVEL` con pydantic-settings. SI falta una obligatoria (`ALCAMPO_BASE_URL`, `REDIS_URL`), ENTONCES la aplicación DEBERÁ fallar al arrancar.

## 5. Requisitos no funcionales

- **RNF-1. Async:** toda E/S (HTTP y Redis) es `async`. Un único `httpx.AsyncClient` y un único cliente Redis por proceso, creados y cerrados en el `lifespan`.
- **RNF-2. Carga sobre Alcampo:** **1 petición HTTP por búsqueda no cacheada** (más reintentos). Ni `GET /` ni pasos de sesión en esta spec: minimiza la superficie ante el WAF.
- **RNF-3. Latencia:** con cache hit, p95 < 50 ms en local. Sin cache no hay objetivo (depende de Alcampo); timeout HTTP de 10 s por intento.
- **RNF-4. Tipado:** type hints en toda función pública; `Any` prohibido.
- **RNF-5. Tests:** nunca llaman a Alcampo real (`respx`) ni a Redis real (`fakeredis`, instancia nueva por test). Cobertura de cada RF según la trazabilidad del `plan.md`.
- **RNF-6. Docs vivas:** `/docs` muestra `ProductSearchResponse` real; `README.md` y `.env.example` actualizados.
- **RNF-7. Seguridad:** no se loguean cookies de Alcampo (`VISITORID`, `global_sid`) ni cabeceras completas.

## 6. Contrato de respuesta

Idéntico al de Mercadona:

```json
{
  "search": {
    "postal_code": "28001",
    "term": "leche",
    "warehouse": "5",
    "strategy_used": "api",
    "scraped_at": "2026-09-24T10:00:00Z",
    "total_results": 1
  },
  "products": [
    {
      "id": "54180",
      "name": "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo.",
      "price": 5.28,
      "price_format": "0.88 €/L",
      "image_url": "https://www.compraonline.alcampo.es/images-v3/.../300x300.jpg",
      "category": "Leche semidesnatada"
    }
  ]
}
```

Errores: `422` (validación, formato FastAPI) y `502` `{"detail": "Upstream service unavailable"}`.

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `term` sin resultados | `200`, `products: []` (RF-5) |
| `term` con espacios alrededor | se recorta antes de consultar y de construir la clave de cache |
| `term` > 50 caracteres (tras recortar) | `422` (RF-2, D6) |
| `term` con tildes, `ñ`, espacios internos | se envía codificado en la URL. ❌ No verificado en vivo con tildes (se buscó "platano" sin tilde) |
| Producto sin `unitPrice` | `price_format: null` (RF-8). **No observado en vivo**: se cubre con una fixture sintética |
| `unitName` desconocido (p. ej. `PER_DOSE`) | `price_format: null` (RF-8) |
| Producto con `available: false` | se devuelve igual (el contrato no tiene campo de disponibilidad) |
| Varios `productGroups` / `id` repetido | se concatenan y se deduplica por `retailerProductId`, conservando la primera aparición (RF-4). ❌ No observado en vivo |
| WAF challenge (`202` + `x-amzn-waf-action`) | `502` sin reintento (RF-18) |
| Alcampo `404` en la búsqueda | `502` sin reintento (RF-16, RF-17) |
| Alcampo `200` con HTML o JSON inesperado | `502` (RF-19) |
| Redis caído | **fuera de alcance en 001** (llega en 007): hoy la petición falla con `500` |

## 8. Fuera de alcance

- Resolución real de `postal_code` → región (spec 007).
- Rotación de User-Agent, `Retry-After`, jitter y anti-baneo (spec 002).
- Logging estructurado, request id y handler global de `500` (spec 003).
- Autenticación `X-API-Key` (spec 004), Docker (005), refactor de dependencias (006).
- Paginación: se devuelve **solo la primera página** (D2).
- Superar el challenge del WAF: exige navegador, prohibido por la constitución.
- Filtros, orden, categorías y detalle de producto.

## 9. Criterios de finalización

- [ ] Todos los RF tienen al menos un test que falla sin la implementación y pasa con ella.
- [ ] Hay un test de integración con la app real (lifespan + fakeredis + respx) que cubre hit, miss, lista vacía y `502`.
- [ ] Un test demuestra que en un cache hit **no** sale ninguna petición HTTP.
- [ ] Un test demuestra que un `404` de Alcampo se pide **una sola vez**.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] `/docs` muestra el schema real; `README.md` y `.env.example` actualizados.
- [ ] Prueba manual contra Alcampo real (una sola petición) documentada en el PR.

## 10. Decisiones (dudas resueltas el 2026-09-25)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Qué va en `warehouse` | Se mantiene el nombre `warehouse` con el `retailerRegionId` (`"5"`), como constante en código | El contrato sigue intacto. Si Alcampo cambia su región por defecto, el dato será incorrecto hasta la 007 (RF-10) |
| D2 | Cuántos productos | Una sola página de 50 | 1 petición por búsqueda, sin paginación (RF-3) |
| D3 | `id` | `retailerProductId` | RF-6 |
| D4 | `category` | Último nivel de `categoryPath`; `null` si viene vacío | RF-6 |
| D5 | Challenge del WAF | Sin reintento; `502` inmediato | RF-18 |
| D6 | `term` largo | `max_length=50` → `422` | RF-2 |
| D7 | Robustez del mapeo | Descartar productos mal formados y deduplicar por `retailerProductId` (primera aparición) | RF-4, RF-9 |
| D8 | Tabla de unidades | `PER_LITRE→L` (✅ verificado), `PER_KG→kg`, `PER_EACH→ud`, `PER_METER→m` (❌ sin verificar); cualquier otra → `null` | RF-7, RF-8 |
| D9 | Importe de `price_format` | Tal como llega de Alcampo (`unitPrice.price.amount`), sin reformatear | RF-7 |

### Supuestos pendientes de confirmar (no bloquean el plan)

- **S1 (D2):** que Mercadona también devuelve un número acotado de resultados. Si devuelve todos, la equivalencia es parcial y se documentará en el README.
- **S2 (D4):** que el contrato de Mercadona admite `category: null`.
