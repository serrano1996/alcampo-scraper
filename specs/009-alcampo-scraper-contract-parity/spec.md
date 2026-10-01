# Spec 009 — Paridad de contrato con Mercadona y paginación

- **Estado:** borrador (pendiente de resolver las dudas de la sección 9; varias exigen verificación en vivo antes del plan)
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
- **RF-3.** La longitud máxima de `term` DEBERÁ ser la de Mercadona (100) **si** Alcampo trata igual los términos de más de 50 caracteres (D6); si no, se documenta la diferencia.

### B. Total y páginas

- **RF-4.** `search.total_results` DEBERÁ ser el número total de productos que coinciden con la búsqueda en esa región, no los de esta página. Cómo se obtiene lo decide D3 tras la verificación en vivo.
- **RF-5.** `search` DEBERÁ incluir `page`, `page_size` y `total_pages`, con el mismo significado que en Mercadona.
- **RF-6.** SI la página pedida no existe (más allá de la última), ENTONCES EL sistema DEBERÁ responder `404 {"detail": "Page out of range"}` sin cachear nada. La página 1 sin resultados sigue siendo `200` con lista vacía.

### C. Cursor y tráfico hacia Alcampo

- **RF-7.** EL sistema DEBERÁ guardar en Redis el token de cada página que obtenga, por región, término normalizado y tamaño de página, para no recorrer de nuevo las páginas anteriores (D2).
- **RF-8.** Para servir una página N sin su token, EL sistema DEBERÁ recorrer desde la última página conocida, con **todas** las protecciones de las specs 002 y 008 (límite global, enfriamiento, tiempo máximo) aplicadas a cada petición. SI N supera `MAX_PAGE` (D2), ENTONCES `422` sin llamar a Alcampo.
- **RF-9.** Cada página DEBERÁ cachearse por separado: `search:{región}:{término}:{page}:{page_size}`.

### D. Forma de la respuesta

- **RF-10.** `search.term` DEBERÁ ser el término normalizado, como en Mercadona (D5).
- **RF-11.** `products[].image_url` y `products[].category` DEBERÁN tener el mismo tipo en ambas APIs (D4).

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

## 9. Dudas abiertas

| # | Duda | Opciones | Recomendación |
|---|---|---|---|
| D1 | **Verificación en vivo previa** | Comprobar: **(a)** si alguna respuesta, cabecera o parámetro de la búsqueda da un **total exacto**; **(b)** si un `pageToken` sirve más tarde y en otra sesión de la **misma región** (para poder guardarlo); **(c)** si `maxPageSize` admite 100; **(d)** cómo es la última página (¿sin token? ¿vacía?) y qué devuelve un token inválido; **(e)** cuántos de 100 productos reales traen `image` o `categoryPath` vacíos; **(f)** qué hace Alcampo con un término de más de 50 caracteres | **Hacerla**, al ritmo seguro: 30 s entre peticiones, **sin crear destinos** (todo en la región por defecto). Unas 10–12 peticiones |
| D2 | Cómo servir la página N con un cursor | (a) guardar los tokens en Redis y recorrer desde la última conocida, con un tope `MAX_PAGE`. (b) solo permitir avanzar de una en una | **(a)** con `MAX_PAGE = 20`, como el límite efectivo de Algolia en Mercadona (1.000 resultados con 50 por página). El peor caso de una página nueva son 20 peticiones encadenadas, todas bajo el límite global |
| D3 | `total_results` y `total_pages` sin total exacto | (a) total exacto, si D1a lo encuentra. (b) suma de los `productCount` de categoría como estimación documentada. (c) `total_results` solo exacto al llegar a la última página | **(a) si existe**; si no, **(b)**, con `total_pages = ceil(total / page_size)` acotado por `MAX_PAGE` y documentado como estimación |
| D4 | `image_url` y `category` nulos | (a) Alcampo nunca devuelve `null`: cadena vacía si falta. (b) ambos contratos los declaran opcionales (cambio en Mercadona). (c) se mantiene la diferencia, documentada | **Decidir con los datos de D1e.** Si en 100 productos reales nunca faltan, **(a)**; si faltan, **(b)**, porque inventar una cadena vacía oculta un dato ausente |
| D5 | `search.term` normalizado o el del cliente | (a) normalizado, como Mercadona. (b) el del cliente, como hoy | **(a)**. Cambia una decisión de la spec 008 de Alcampo, pero la paridad es el objetivo de base |
| D6 | Longitud máxima de `term` | (a) 100, como Mercadona, si D1f muestra que Alcampo busca igual con más de 50. (b) 50, documentado como diferencia | **Según D1f** |
| D7 | `page_size` mayor que lo que admita Alcampo | (a) si `maxPageSize` no llega a 100, componer una página con varias peticiones. (b) limitar `page_size` a lo que admita Alcampo y documentarlo | **Según D1c.** Si no admite 100, **(b)**: componer páginas multiplica las peticiones |
