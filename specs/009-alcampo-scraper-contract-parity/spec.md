# Spec 009 — Paridad de contrato con Mercadona y paginación

- **Estado:** aprobada (2026-10-01), con las decisiones de la sección 9 tomadas tras la verificación en vivo de la sección 10
- **Fecha:** 2026-10-01
- **Referencia:** `mercadona-scraper/specs/008-mercadona-scraper-search-completeness` (paginación y total real) y el contrato actual de Mercadona, comparado campo a campo el 2026-10-01

## 1. Contexto y objetivo

El objetivo de base del proyecto es que un consumidor pueda usar Alcampo y Mercadona **sin adaptarse** (mismo contrato). El análisis del 2026-10-01 encontró que ya no es así, y que la búsqueda de Alcampo además devuelve una fracción de los resultados con un total falso:

**Contrato divergido** (esquemas OpenAPI de ambas APIs, comparados el 2026-10-01):

| Punto | Mercadona (tras su spec 008) | Alcampo hoy |
|---|---|---|
| Parámetros `page` y `page_size` | `page ≥ 1` (por defecto 1); `page_size` 1–100 (por defecto 50); fuera de rango → `422` | no existen |
| `search.page`, `search.page_size`, `search.total_pages` | presentes y obligatorios | no existen |
| `search.total_results` | total real de coincidencias | `len(products)` de esta respuesta |
| Página fuera de rango | `404 {"detail": "Page out of range"}` | no aplica |
| `search.term` | el término **normalizado** | el término tal como lo envió el cliente (spec 008 de Alcampo, plan-D1) |
| Longitud máxima de `term` | 100 | 50 (el cliente web de Alcampo recorta a 50, Fase 0 §1) |
| `products[].image_url`, `products[].category` | texto, nunca nulo | pueden ser `null` |

**Resultados incompletos y total falso** (verificado en vivo el 2026-10-01, 2 peticiones, región por defecto):

- `leche`, página 1: 50 productos y un `metadata.nextPageToken`. Pedida la página 2 con ese token: **otros 50 productos distintos** (0 en común) y otro token. Hay más páginas.
- La respuesta no trae un total explícito. Lo más parecido son los `productCount` de `additionalPageInfo.categories`: suman **~670** para `leche`, pero dieron 669 y 670 en las dos peticiones, así que no son un total fiable sin más.
- La API devuelve 50 y dice `total_results: 50`.

**La paginación de Alcampo es por cursor**, no por número de página: cada respuesta trae el token de la siguiente. Servir la página N exige conocer el token de la N−1, lo que obliga a recorrer las anteriores (tráfico hacia el WAF) salvo que los tokens se guarden.

**Objetivo:** que la respuesta de Alcampo tenga **exactamente la misma forma y las mismas reglas** que la de Mercadona, que permita recorrer todos los resultados por páginas y que diga la verdad sobre el total, sin generar ráfagas hacia el WAF.

## 2. Usuarios y actores

- **Aplicación cliente** que ya consume Mercadona: quiere cambiar a Alcampo, o usar ambos, sin cambiar su código.
- **Responsable del servicio:** necesita que la paginación no dispare el tráfico hacia Alcampo.

## 3. Historias de usuario

- **H1.** Como aplicación cliente, quiero que la respuesta de Alcampo tenga los mismos campos, tipos y códigos de error que la de Mercadona.
- **H2.** Como aplicación cliente, quiero recorrer todos los productos de una búsqueda por páginas y saber cuántos hay.
- **H3.** Como responsable del servicio, quiero que pedir una página alta no genere una ráfaga de peticiones a Alcampo.

## 4. Requisitos funcionales (EARS)

### A. Parámetros (como Mercadona)

- **RF-1.** EL sistema DEBERÁ aceptar `page` (entero ≥ 1, por defecto 1) y `page_size` (entero de 1 a 100, por defecto 50). Fuera de rango → `422` sin llamar a Alcampo ni a Redis.
- **RF-2.** SIN `page` ni `page_size`, EL sistema DEBERÁ devolver los mismos productos que hoy (primera página de 50): los clientes actuales no notan el cambio.
- **RF-3.** La longitud máxima de `term` DEBERÁ ser la de Mercadona (100). A Alcampo se le DEBERÁN enviar como mucho los 50 primeros caracteres del término normalizado, como hace su propia web (Fase 0 §1, D6).

### B. Total y páginas

- **RF-4.** `search.total_results` DEBERÁ ser el número total de productos que coinciden con la búsqueda en esa región, no los de esta página. Alcampo no da un total exacto (sección 10), así que DEBERÁ estimarse con la suma de los `productCount` de primer nivel de `additionalPageInfo.categories`, y pasar a ser exacto cuando se sirva la última página (la que llega sin `nextPageToken`): `(page − 1) × page_size + productos de esa página` (D3).
- **RF-5.** `search` DEBERÁ incluir `page`, `page_size` y `total_pages`, con el mismo significado que en Mercadona. `total_pages = min(ceil(total_results / page_size), MAX_PAGE)`; en la última página, la página servida.
- **RF-6.** SI la página pedida no existe (más allá de la última), ENTONCES EL sistema DEBERÁ responder `404 {"detail": "Page out of range"}` sin cachear nada. La página 1 sin resultados sigue siendo `200` con lista vacía.

### C. Cursor y tráfico hacia Alcampo

- **RF-7.** EL sistema DEBERÁ guardar el token de cada página que obtenga **en memoria, en la sesión de la región que lo obtuvo**, por término normalizado y tamaño de página: los tokens están ligados a la sesión (sección 10). SI la sesión se renueva, ENTONCES sus tokens se descartan (D2).
- **RF-8.** Para servir una página N sin su token, EL sistema DEBERÁ recorrer desde la última página conocida, con **todas** las protecciones de las specs 002 y 008 (límite global, enfriamiento, tiempo máximo) aplicadas a cada petición. SI N supera `MAX_PAGE` (D2), ENTONCES `422` sin llamar a Alcampo.
- **RF-9.** Cada página DEBERÁ cachearse por separado: `search:{región}:{término}:{page}:{page_size}`.

### D. Forma de la respuesta

- **RF-10.** `search.term` DEBERÁ ser el término normalizado, como en Mercadona (D5).
- **RF-11.** `products[].image_url` y `products[].category` DEBERÁN ser texto no nulo, como en Mercadona. Un producto sin imagen o sin categoría se descarta como cualquier otro mal formado (spec 001, D4).

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas** (constitución #1).
- **RNF-2. Compatibilidad hacia atrás:** sin parámetros nuevos, la respuesta solo **añade** campos; no cambia los existentes salvo los que D4 y D5 decidan alinear (se anuncian en el README).
- **RNF-3. Tests sin red** (respx, fakeredis), con fixtures de 2–3 páginas encadenadas por token, sintetizadas a partir de respuestas reales.
- **RNF-4. Docs vivas:** README con los parámetros y ejemplos de paginación; `.env.example` si hay variables nuevas (las edita el usuario).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Sin `page` ni `page_size` | primera página de 50, como hoy (RF-2) |
| `page=0`, `page_size=0`, `page_size=101` | `422` sin tocar nada (RF-1) |
| `page=3` con los tokens de 1 y 2 en cache | 1 sola petición a Alcampo (RF-7) |
| `page=3` sin ningún token | 3 peticiones encadenadas, cada una por el límite global (RF-8) |
| `page` más allá de la última | `404 "Page out of range"`, sin cachear (RF-6) |
| Término sin resultados y `page=2` | `404`: esa página no existe |
| Límite global agotado a mitad del recorrido | `502`, como cualquier búsqueda limitada (spec 008) |
| Dos regiones, mismo término | tokens y caches separados (RF-7, RF-9) |
| `page > MAX_PAGE` | `422` (RF-8) |

## 7. Fuera de alcance

- **Cambiar Mercadona.** Si D4 decide que la forma común es otra, el cambio en Mercadona se hace en su propio repositorio y spec.
- **Ordenación o filtros** (por precio, categoría…).
- **Exponer el cursor** al cliente (`next_page_token`): rompería la paridad con Mercadona, que pagina por número.

## 8. Criterios de finalización

- [ ] **Antes del plan:** verificación en vivo de D1, anotada en el plan.
- [ ] El esquema OpenAPI de la respuesta de Alcampo coincide con el de Mercadona en nombres, tipos y obligatoriedad (un test lo compara con una copia del esquema de Mercadona).
- [ ] RF-1 a RF-11 cubiertos por tests en verde.
- [ ] Un test demuestra que `page=3` con los tokens en cache hace **1** petición a Alcampo.
- [ ] `ruff`, `mypy` y `pytest` limpios.
- [ ] README actualizado.
- [ ] Verificación manual con `docker compose`: páginas 1, 2 y 3 de `leche`, la 3 repetida sale de cache, y una página fuera de rango da `404`.

## 9. Decisiones (resueltas el 2026-10-01)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Verificación en vivo previa | Hecha (sección 10); cortada por un challenge del WAF en la 8.ª petición | D2, D3, D4 y D7 se deciden con datos; D6 sin datos |
| D2 | Cómo servir la página N con un cursor | Tokens **en memoria, en la sesión de cada región** (no en Redis: ligados a la sesión); si la sesión se renueva, se vuelve a recorrer. `MAX_PAGE = 20` | RF-7, RF-8. Revisada tras la sección 10 (la versión aprobada antes, tokens en Redis, no funcionaría) |
| D3 | Total sin dato exacto | Estimación por la suma de `productCount` de categorías de primer nivel, documentada; exacto al servir la última página | RF-4, RF-5. "Sin total hasta el final" rompería el tipo `int` del contrato de Mercadona |
| D4 | `image_url` / `category` nulos | Nunca nulos, como Mercadona; un producto sin ellos se descarta | RF-11. 0 de 100 productos reales vacíos (sección 10) |
| D5 | `search.term` | Normalizado, como Mercadona | RF-10. Cambia la decisión plan-D1 de la spec 008 de Alcampo |
| D6 | Términos de más de 50 caracteres | Se aceptan hasta 100; a Alcampo van los 50 primeros, como hace su web | RF-3. No verificado en vivo (sección 10) |
| D7 | `page_size` | De 1 a 100, en una sola petición | RF-1. `maxPageSize=100` verificado |

## 10. Verificación en vivo de D1 (2026-10-01, 11:50–11:53Z)

Región por defecto, sin crear destinos, 30 s entre peticiones. **Cortada por un challenge del WAF en la 8.ª petición** (R8, término de 60 caracteres); no se hizo ninguna más.

| Pregunta | Resultado |
|---|---|
| (a) Total exacto | **No existe.** Ni en el cuerpo (el único número fuera de productos y categorías es `additionalPageInfo.currentCategory.productCount: 0`) ni en cabeceras |
| (b) Token en otra sesión / más tarde | En **otra sesión: `400`**. En la misma sesión, 1,5 min después: `200` con 50 productos. **El token está ligado a la sesión** |
| (c) `maxPageSize=100` | Funciona: 100 productos decorados |
| (d) Última página y token inválido | Última página **sin `nextPageToken`** y con `metadata: {}` (`quinoa`: 25 resultados, una página). Token inválido: **`401`** `CC-090` |
| (e) Imagen o categoría vacías | **0 de 100** productos reales sin `image.src` y 0 sin `categoryPath` |
| (f) Término de más de 50 caracteres | **Sin respuesta:** challenge del WAF en esa petición |

**Consecuencias para las dudas:**

- **D2 queda invalidada en su forma aprobada:** los tokens no se pueden guardar en Redis ni compartir entre instancias, ni sobreviven a la renovación de sesión (cada 50 min, spec 007). Propuesta revisada: tokens **en memoria, en la sesión de cada región** (`RegionSessions`), por término normalizado y tamaño de página; si la sesión se renueva, se vuelve a recorrer. Las páginas ya servidas siguen en la cache de Redis y no necesitan token. `MAX_PAGE = 20` se mantiene.
- **D3:** sin total exacto, quedan la estimación por categorías o "total desconocido hasta la última página".
- **D4:** con 0 de 100 vacíos, la opción (a) (nunca nulo) es viable.
- **D7:** `maxPageSize=100` funciona: `page_size` de 1 a 100 en una sola petición.
- **D6:** sin datos.

**Hallazgo fuera de esta spec:** el challenge llegó con 10 búsquedas en ~15 min a 30 s de separación, muy por debajo del límite por defecto de la spec 008 (20 por minuto). O el umbral real de la búsqueda es mucho menor, o el WAF penalizó las respuestas de error provocadas (el `400` del token ajeno y el `401` del token inválido). Se trata aparte (límite de salida y espaciado).
