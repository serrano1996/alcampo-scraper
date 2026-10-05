# Spec 011 — Resiliencia ante cambios de formato de Alcampo

- **Estado:** aprobada (2026-10-05)
- **Fecha:** 2026-10-05
- **Referencia:** `mercadona-scraper` spec 011 (*upstream schema resilience*). Mismo objetivo, adaptado: aquí el punto débil no es el mismo (ver §1)

## 1. Contexto y objetivo

La búsqueda depende de un JSON interno de Alcampo (plataforma Ocado) que Alcampo puede cambiar sin avisar. Estado actual, verificado en el código el 2026-10-05:

- **Campos no usados: ya resiliente.** Los modelos crudos (`app/models/alcampo.py`) declaran solo los campos que la app consume (`retailerProductId`, `name`, `price.amount`, `unitPrice`, `image.src`, `categoryPath`, más los de paginación de la spec 009) con `extra="ignore"`. Quitar o cambiar de tipo cualquier otro campo no afecta. El fallo que motivó la spec 011 de Mercadona (un campo no usado tumba todas las búsquedas) **no se da aquí**; se fija con tests para que no aparezca.
- **Envoltorio roto: ya `502`.** Sin `productGroups`, o con JSON inválido, el scraper lanza `UpstreamUnavailableError` → `502` (spec 001), sin cachear.
- **Campo usado roto: `200 []` cacheado.** Cada producto se valida por separado y el que no cumple se **descarta** (spec 001 RF-9, spec-D7). Si Alcampo cambia el formato de un campo usado (por ejemplo, `price.amount` pasa a número, o `image` a `images[]`), **todos** los productos se descartan y la API responde **`200` con `products: []`**, igual que una búsqueda sin resultados, y lo **guarda en cache 1 hora** (`CACHE_TTL_SECONDS`). Solo queda un `ERROR` en el log (spec 003 RF-15). El cliente no puede distinguir "no hay leche" de "Alcampo ha cambiado su formato", y la cache prolonga el fallo después de corregirlo.
- **El log del descarte no dice qué ha cambiado:** registra los ids descartados, no el campo que falló.
- **Unidades de precio desconocidas, en silencio:** una `unitName` que no está en la tabla (hoy solo `PER_LITRE` está verificada en vivo) da `price_format: null` sin dejar rastro.

**Objetivo:** que un cambio de formato en los datos que la app usa se comunique como fallo de Alcampo (`502`, sin cachear), no como "sin resultados", y que el log diga **qué campo** cambió, sin volcar datos de Alcampo.

## 2. Usuarios y actores

- **Aplicación cliente:** necesita distinguir "sin resultados" de "el proveedor falla".
- **Responsable del servicio:** necesita saber qué campo cambió para adaptar el modelo rápido.

## 3. Historias de usuario

- **H1.** Como aplicación cliente, quiero un `502` cuando Alcampo cambia el formato de los datos que la API necesita, en lugar de una lista vacía que parece "sin resultados".
- **H2.** Como aplicación cliente, quiero que, una vez corregido el fallo, la búsqueda vuelva a funcionar al momento, sin esperar a que caduque una respuesta vacía en cache.
- **H3.** Como responsable del servicio, quiero un log que diga qué campo falló y de qué forma, sin el contenido de la respuesta.
- **H4.** Como aplicación cliente, quiero que la búsqueda siga funcionando si Alcampo cambia campos que la API no usa.

## 4. Requisitos funcionales (EARS)

### A. Todos los productos rotos

- **RF-1.** SI una página de Alcampo trae **al menos un producto** y **todos** se descartan por mal formados, EL sistema DEBERÁ responder `502 {"detail": "Upstream service unavailable"}` y NO DEBERÁ guardar esa página en cache.
- **RF-2.** CUANDO una página no traiga ningún producto (búsqueda sin resultados de verdad), EL sistema DEBERÁ seguir respondiendo `200` con `products: []` (spec 001, sin cambios).
- **RF-3.** SI la página con todos los productos rotos es una intermedia de un recorrido (spec 009), EL sistema DEBERÁ responder `502` en ese punto; las páginas válidas anteriores siguen cacheadas (D4).

### B. Algunos productos rotos

- **RF-4.** SI solo parte de los productos se descartan, EL sistema DEBERÁ seguir devolviéndolos sin ellos (`200`) y registrando un `WARNING` (spec 001 RF-9, sin cambios) (D1).

### C. Logs que dicen qué cambió

- **RF-5.** CUANDO se descarte algún producto, el log DEBERÁ incluir, además de los ids, **los campos que fallaron y el tipo de error** (por ejemplo `image.src:missing`, `price.amount:string_pattern_mismatch`), sin repetirlos y **sin los valores** recibidos (D2).
- **RF-6.** CUANDO un producto traiga una `unitName` que no está en la tabla de unidades, EL sistema DEBERÁ registrar un `WARNING` por búsqueda con las unidades desconocidas, y seguir devolviendo `price_format: null` (D3).

### D. Campos no usados

- **RF-7.** CUANDO Alcampo quite, añada o cambie de tipo un campo que la app no usa, EL sistema DEBERÁ responder con normalidad (`200`). Ya se cumple; se fija con tests sobre la fixture real.

## 5. Requisitos no funcionales

- **RNF-1. Contrato intacto:** mismos códigos y cuerpos; solo cambia que el caso de RF-1 pasa de `200 []` a `502`.
- **RNF-2. Sin volcar datos de Alcampo** en los logs (spec 003 RF-17): solo rutas de campos, tipos de error, ids y nombres de unidad.
- **RNF-3. Tests sin red**, con la fixture real modificada para simular cada cambio.
- **RNF-4. Sin dependencias nuevas.**

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `price.amount` llega como número en todos los productos | `502`, sin cache, `ERROR` con `price.amount:string_type` (RF-1, RF-5) |
| `image` pasa a llamarse `images` en todos | `502`, `ERROR` con `image:missing` (RF-1, RF-5) |
| 1 producto sin imagen entre 50 válidos | `200` con 49, `WARNING` con `image:missing` (RF-4, RF-5) |
| Búsqueda sin resultados (`productGroups` vacío o sin productos) | `200 []`, cacheado (RF-2) |
| Productos duplicados entre grupos | se deduplican como hoy; no cuentan como descarte |
| Página 3 con todo roto, en un recorrido en frío | `502`; páginas 1 y 2 cacheadas (RF-3) |
| `unitName: "PER_100G"` | `price_format: null` + `WARNING` con `PER_100G` (RF-6) |
| Desaparece un campo no usado (p. ej. `brand`) | `200` (RF-7) |
| Sin `productGroups` | `502` (ya hoy) |

## 7. Fuera de alcance

- Detección automática o alertas de cambios de esquema.
- La cadena de resolución de región (spec 007): ya responde `502` con un `ERROR` que dice el paso.
- Cambiar la política de descartes parciales (D1).

## 8. Criterios de finalización

- [ ] RF-1 a RF-7 con tests en verde, sin red.
- [ ] Un test de integración: todo roto → `502` y nada en cache; al "arreglarse" Alcampo, la siguiente búsqueda → `200` sin esperar al TTL.
- [ ] `ruff`, `mypy` y `pytest` limpios; README actualizado.
- [ ] Verificación manual corta: 1 búsqueda real con `docker compose` → `200` con productos y sin `WARNING` (el modelo sigue validando la respuesta real de hoy).

## 9. Decisiones (resueltas el 2026-10-05: las recomendadas)

| # | Duda | Opciones | Recomendación |
|---|---|---|---|
| D1 | ¿Qué hacer si **solo algunos** productos están rotos? | **A)** descartarlos y devolver el resto (hoy); **B)** `502` para toda la búsqueda, como Mercadona; **C)** `502` si se descarta más de un umbral (p. ej. la mitad) | **A.** Mercadona eligió B porque su `total_results` es exacto y una lista con huecos no cuadraría; aquí el total ya es una estimación (spec 009). Un formato que cambia suele romper todos los productos (RF-1 lo cubre), y en 100 productos reales no apareció ninguno roto. B haría que un solo producto raro tumbe la búsqueda; C añade un número arbitrario |
| D2 | ¿Qué detalle del fallo registrar? | **A)** ruta del campo + tipo de error de Pydantic; **B)** además el valor recibido; **C)** solo los ids (hoy) | **A.** Basta para adaptar el modelo; B vuelca datos de Alcampo (spec 003 RF-17) |
| D3 | ¿Avisar de unidades de precio desconocidas? | **A)** `WARNING` por búsqueda con las unidades; **B)** `DEBUG`; **C)** nada (hoy) | **A.** Solo `PER_LITRE` está verificada: es la forma barata de verificar las otras con tráfico real. Si fuese ruidoso, se baja a `INFO` |
| D4 | Página intermedia con todo roto en un recorrido | **A)** `502` en ese punto; **B)** saltarla y seguir | **A.** B es posible (el token de la siguiente sí se lee), pero es el mismo fallo de formato que RF-1: lo normal es que la página pedida también esté rota, y seguir solo gastaría peticiones |
