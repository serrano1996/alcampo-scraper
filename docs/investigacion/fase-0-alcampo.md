# Fase 0 — Investigación en vivo de Alcampo

- **Fecha:** 2026-09-24, 11:22–13:05 UTC.
- **Método:** peticiones reales con `curl` y `httpx` desde una IP residencial española, más lectura estática del bundle JS público (`/static/index-DWpEH2nq.js`) para descubrir rutas. No se usó navegador.
- **Base URL:** `https://www.compraonline.alcampo.es` (plataforma **Ocado Smart Platform**, detrás de **AWS CloudFront + AWS WAF**).
- **Leyenda:** ✅ verificado en vivo · 🔶 deducido o parcial · ❌ no verificado.

## Resumen

| # | Pregunta | Respuesta | Estado |
|---|---|---|---|
| 1 | Endpoint de búsqueda | `GET /api/webproductpagews/v6/product-pages/search`. Servicio REST propio de Ocado; sin Algolia ni otro proveedor externo | ✅ |
| 2 | Credenciales | **Ninguna** para buscar. La escritura de sesión (localización) exige un token CSRF que se saca del HTML | ✅ |
| 3 | ¿Influye la localización? | **Sí**: precio **y** catálogo cambian entre regiones (Barcelona y Canarias frente a Madrid; dentro de Madrid solo cambia algo el catálogo). Se fija con una cadena de 7 peticiones | ✅ (ver §3) |
| 4 | CP inexistente | `99999` → `200 []` en el primer paso (geocodificación). Sin 404 ni error | ✅ |
| 4b | CP sin servicio | No encontrado: todos los CPs probados (Madrid, Barcelona, Las Palmas) son `DELIVERABLE`. Baleares (07001) sin resolver por bloqueo del WAF | ❌ |
| 5 | Anti-bot | **AWS WAF**: `202` vacío + `x-amzn-waf-action: challenge`, bloqueo por IP de 2–4 min. Nunca se vio `429` ni `Retry-After` | ✅ (umbral 🔶) |
| 6 | Forma del producto | Ver §6. Fixture en `tests/fixtures/alcampo_search_leche.json` | ✅ |

## 1. Búsqueda por texto ✅

```
GET /api/webproductpagews/v6/product-pages/search
    ?q=leche&maxPageSize=50&maxProductsToDecorate=50&tag=web
Accept: application/json
```

- `200 application/json`. Claves raíz: `productGroups`, `metadata`, `additionalPageInfo`, `missedPromotions`.
- Los productos están en `productGroups[*].decoratedProducts[]`.
- **Sin `maxProductsToDecorate` solo llegan IDs** (`otherProductIds`) y `decoratedProducts` viene vacío.
- La paginación va por cursor en `metadata.nextPageToken` (parámetro `pageToken`).
- El bundle recorta `q` a 50 caracteres antes de enviarlo (constante en el cliente web).
- **Sin resultados** (`q=xqzwvkjhgf`): `200 {"productGroups": [], "metadata": {}, ...}` → fixture `alcampo_search_no_results.json`.
- No acepta región en la URL: la región la aporta la **sesión** (cookies). Ver §3.

## 2. Credenciales y sesión ✅

- La búsqueda **sin ninguna cookie** devuelve `200`: el servidor crea una sesión anónima al vuelo (`Set-Cookie: VISITORID` con `Max-Age=3600`, `global_sid`, `AWSALB*`) en la región por defecto.
- **Región por defecto** de la sesión anónima (según `__INITIAL_STATE__` del HTML): `regionName: "Vaguada"`, `retailerRegionId: "5"`, `regionId: ac90d761-9d58-4918-a37d-dd14e1ce384a`.
- Las escrituras (`PUT`/`POST` a `/api/*`) exigen la cabecera `X-CSRF-Token`, cuyo valor se lee del HTML de `GET /` (`"session":{"csrf":{"token":"<uuid>"}}`). Sin ella → `403` con `Ecom-Csrf-Failure: true`.
- `visitorId` (uuid) también sale del HTML (`session.metadata.visitorId`).
- Son tokens de sesión efímeros, no credenciales de cuenta. No se commitean; los tests usarán valores sintéticos.

## 3. Localización ✅

### Cadena CP → región (todas las peticiones con la misma sesión)

| Paso | Petición | Respuesta relevante |
|---|---|---|
| 0 | `GET /` | HTML con `csrf.token`, `visitorId` y la región actual |
| 1 | `PUT /api/address/v1/addresses/areas` · form `query=<cp>` · cabeceras `visitorid`, `visitor-id` | `[{"id": "<Google Place ID>", ...}]`, o `[]` si el CP no existe |
| 2 | `GET /api/address/v1/addresses/areas/{id}` | `latitude`, `longitude`, `postalCode`, `formattedAddress` |
| 3 | `PUT /api/ecomdeliverydestinations/v2/deliverability` · `{latitude, longitude, postalCode}` | `{"deliverability": "DELIVERABLE"}` |
| 4 | `POST /api/ecomdeliverydestinations/v2/temporary-delivery-destinations` · `{visitorId, latitude, longitude, postalCode, formattedAddress}` | `"<deliveryDestinationId>"` |
| 5 | `GET /api/ecomdeliverydestinations/v4/delivery-addresses/{id}` | `resolvedRegionId` |
| 6 | `POST /api/customersessions/v2/sessions/proposition` · `{destinationRegionId, deliveryDestinationId}` | `originCartProposition` y `destinationCartProposition`. **Solo previsualiza** |
| 7 | `POST /api/customersessions/v2/sessions/active` · cabeceras `visitor-id`, `customer-id: ""` · `{destinationCartPropositionId, originCartPropositionId}` | `{"regionId": "<nueva>", ...}`. **Este paso confirma el cambio** |

Callejones sin salida documentados (para no repetirlos):
- `PUT /api/address/v1/addresses/by-postcode` y `POST /api/address/v3/address-lookup/by-postcode` → `400 webaddressws-3000`.
- `deliverability` sin coordenadas → `400 INVALID_COORDINATES`.
- **Sin el paso 7, la región no cambia**, aunque la respuesta del paso 6 diga `regionChanged: true`. Se comprobó leyendo el SSR: seguía en "Vaguada".

### Regiones resueltas

| CP | Zona | `resolvedRegionId` | Nombre (SSR tras el paso 7) |
|---|---|---|---|
| — | sesión anónima | `ac90d761-…` (retailer 5) | Vaguada |
| 28001 | Madrid | `4ccafbad-0ed4-4271-a4d0-37ade6710304` (retailer 11) | Moratalaz |
| 08001 | Barcelona | `0e859bc4-dfe7-413c-a25b-715154498548` (retailer 43) | Diagonal Mar |
| 35001 | Las Palmas (Canarias) | `c98744f2-ca04-4583-bbfc-c52f24548329` (retailer 32) | Telde |
| 07001 | Palma (Baleares) | no resuelto (el WAF bloqueó) | ❌ |

### ¿Cambian los precios? ✅ Sí

Misma búsqueda (30 productos) en la sesión confirmada en Telde frente a una sesión nueva por defecto (Vaguada):

| Término | En común | Precio distinto | Solo en una región |
|---|---|---|---|
| agua | 26 | 23 | 4 / 4 |
| platano | 19 | 15 | 11 / 11 |

Ejemplos (Telde frente a Vaguada): Bezoya 6×1,5 L **5,40 € / 4,02 €**; Font Vella 4×2 L **5,96 € / 4,80 €**; Danonino **1,85 € / 2,51 €**.

Comparación peninsular, término "agua" (30 productos) frente a Vaguada:

| CP → región | Resultados | En común | Precio distinto | Solo en una región |
|---|---|---|---|---|
| 08001 → Diagonal Mar | 22 / 30 | 13 | **7** (Nestlé Aquarel garrafa 1,76 € / 2,42 €; Font Vella 6× 4,26 € / 4,62 €) | 9 / 17 |
| 28001 → Moratalaz | 30 / 30 | 28 | **0** | 2 / 2 |

**Conclusión:** el precio y el catálogo dependen de la **región** (la tienda que sirve), no del código postal en sí. Dos regiones de Madrid (Moratalaz y Vaguada) comparten precios pero no todo el catálogo; regiones de zonas distintas (Barcelona, Canarias) difieren en ambos. Por eso la clave de cache debe ir por región y no por CP (spec 007).

> **Descartado:** una primera comparación entre 28001, 08001 y 35001 dio precios idénticos, pero se hizo sin el paso 7, así que todas las búsquedas iban a Vaguada. No vale como evidencia.

## 4. CP inexistente o sin servicio

- ✅ **Inexistente** (`99999`): paso 1 → `200 []`. La cadena se corta ahí. Es responsabilidad nuestra traducirlo a `404`.
- ❌ **Sin servicio**: no se ha encontrado ninguno. Canarias, que se esperaba sin servicio, es `DELIVERABLE`. El bundle maneja `deliverability: "NOT_DELIVERABLE"` (texto `delivery.destination.temporary.home.alert.nondeliverable`), así que el valor existe, pero su respuesta real no se ha observado.

## 5. Protección anti-bot ✅ (umbral 🔶)

**Qué se observa** cuando salta:

```
HTTP/1.1 202 Accepted
x-amzn-waf-action: challenge
Content-Type: text/html; charset=UTF-8
Content-Length: 0
```

- Afecta a **toda la IP**: también a la búsqueda y a clientes nuevos sin cookies.
- Duración observada: **2–4 minutos**.
- **No hay `429` ni `Retry-After`.** Es un `202`, un código 2xx que `httpx` considera éxito: hay que detectarlo por la cabecera.
- Superar el challenge exige ejecutar el JS de AWS (`captcha-sdk.awswaf.com`) y obtener la cookie `aws-waf-token`, es decir, un navegador. Queda **fuera de la constitución**.

**Cuándo saltó** (3 bloqueos):

| Ronda | Ritmo | Peticiones | Dónde apareció el primer challenge |
|---|---|---|---|
| 1 | ráfagas manuales | ~30 en 5 min | `POST temporary-delivery-destinations` (2.º intento) |
| 2 | 15 s entre peticiones, justo tras desbloqueo | 5 | `POST temporary-delivery-destinations` (1.er intento) |
| 3 | 30 s, tras 10 min de enfriamiento | 26 en 13 min | `POST temporary-delivery-destinations` (4.º intento) |

Con 30 s de espaciado, una sola creación de destino por ronda y unos 10 min entre rondas no volvió a bloquear (4 rondas limpias, 11–13 peticiones cada una).

🔶 **Hipótesis**, no demostrable desde fuera: hay un límite de tasa estricto sobre `temporary-delivery-destinations` (unas 3–4 creaciones por ventana y por IP) que, al superarse, marca la IP entera. La configuración del WAF es privada.

**Otras cabeceras:** no hay `RateLimit-*` ni `X-RateLimit-*`. Se envió siempre un User-Agent de Chrome real; el bloqueo por User-Agent ❌ no se ha probado.

## 6. Forma del producto ✅

Extracto de `productGroups[0].decoratedProducts[0]` (fixture completa en `tests/fixtures/alcampo_search_leche.json`):

```json
{
  "productId": "176cfd5e-afa9-4aa1-8b6f-fe3d757c535e",
  "retailerProductId": "54180",
  "name": "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo.",
  "brand": "PRODUCTO ALCAMPO",
  "packSizeDescription": "6000ml",
  "price": {"amount": "5.28", "currency": "EUR"},
  "unitPrice": {"price": {"amount": "0.88", "currency": "EUR"}, "unit": "fop.price.per.litre", "unitName": "PER_LITRE"},
  "available": true,
  "image": {"src": "https://www.compraonline.alcampo.es/images-v3/.../300x300.jpg", "...": "..."},
  "categoryPath": ["Leche, Huevos, Lácteos, Yogures y Bebidas vegetales", "Leche", "Leche semidesnatada"]
}
```

| Campo de la API | Origen en Alcampo | Nota |
|---|---|---|
| `id` | `retailerProductId` (`"54180"`) o `productId` (uuid) | a decidir en la spec |
| `name` | `name` | |
| `price` | `price.amount` | **string** decimal (`"5.28"`) |
| `price_format` | `unitPrice.price.amount` + `unitPrice.unitName` | ✅ solo se ha visto `PER_LITRE` (150/150). El bundle referencia `each`, `dose`, `meter`, `flexible_units`, `additional.unit` (❌ no observados). `unitPrice` opcional ❌ no observado ausente |
| `image_url` | `image.src` | 300×300 jpg |
| `category` | `categoryPath` (lista de 3 niveles) | a decidir cuál usar |

## Fixtures guardadas (`tests/fixtures/`)

| Fichero | Contenido |
|---|---|
| `alcampo_search_leche.json` | búsqueda real "leche", recortada a 3 productos |
| `alcampo_search_no_results.json` | búsqueda real sin resultados |
| `alcampo_address_areas_28001.json` | paso 1 |
| `alcampo_address_area_details_28001.json` | paso 2 |
| `alcampo_deliverability_deliverable.json` | paso 3 |
| `alcampo_delivery_address_28001.json` | paso 5 |

No contienen tokens CSRF, `visitorId` ni cookies. Los IDs de Google Place y los uuid de región o destino son identificadores públicos o efímeros, no credenciales.

## Implicaciones para las specs

1. **001:** buscar sin sesión es viable. En la región por defecto `warehouse` = región de Ocado (Vaguada). Un `202` con `x-amzn-waf-action` debe tratarse como fallo del upstream y no como éxito.
2. **002:** el anti-baneo de verdad no va de `429`/`Retry-After` (no aparecen) sino del WAF. Reintentar pronto un challenge no sirve (el bloqueo dura minutos).
3. **007:** aplica. Cuesta 8 peticiones por CP nuevo (incluida la que dispara el WAF), así que la cache CP → región es crítica. La búsqueda por región exige que la sesión httpx mantenga cookies **por región**: el pool de clientes tiene impacto de diseño.
