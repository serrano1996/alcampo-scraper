# Spec 013 — Unidades de precio reales

- **Estado:** aprobada (2026-10-05)
- **Fecha:** 2026-10-05
- **Referencia:** corrige la tabla de unidades de la spec 001 (spec-D8, RF-7) con el hallazgo de la verificación manual de la spec 011 (T3). Sin equivalente en `mercadona-scraper` (su precio unitario viene con otro formato)

## 1. Contexto y objetivo

`price_format` (`"0.88 €/L"`) se construye con `unitPrice.price.amount` y una tabla `unitName → sufijo` (`app/mappers/product_mapper.py`). En la spec 001 solo se vio `PER_LITRE` (150 de 150 productos de `leche`, Fase 0 §4); `PER_KG`, `PER_EACH` y `PER_METER` se **dedujeron** de claves de traducción del bundle web, sin verlas en una respuesta. Cualquier otra unidad da `price_format: null` (spec 001 RF-8), y desde la spec 011 RF-6, un `WARNING`.

**Hallazgo (2026-10-05T09:23Z, spec 011 T3):** 1 búsqueda real de `arroz` (100 productos) registró `unknown price units units=['PER_1KG']`. **Alcampo usa `PER_1KG`, no `PER_KG`**: 89 de los 100 productos salían con `price_format: null`. Todo producto que se vende al peso (fruta, carne, arroz, legumbres…) tiene hoy el precio unitario a `null`.

**Objetivo:** que la tabla solo contenga nombres de unidad **observados** en respuestas reales, empezando por `PER_1KG → kg`, y que la evidencia quede en fixtures reales.

## 2. Historias de usuario

- **H1.** Como aplicación cliente, quiero el precio por kilo de los productos que se venden al peso, como ya lo tengo por litro.
- **H2.** Como responsable del servicio, quiero que la tabla de unidades se base en datos reales y que una unidad nueva se vea en los logs (ya lo hace la spec 011).

## 3. Requisitos funcionales (EARS)

- **RF-1.** CUANDO un producto traiga `unitName: "PER_1KG"`, EL sistema DEBERÁ devolver `price_format: "<amount> €/kg"`.
- **RF-2.** La tabla de unidades DEBERÁ contener solo nombres **observados en una respuesta real** de Alcampo, cada uno con su fixture (D1).
- **RF-3.** Una unidad fuera de la tabla DEBERÁ seguir dando `price_format: null` y el `WARNING` de la spec 011 RF-6 (sin cambios).

## 4. Requisitos no funcionales

- **RNF-1. Contrato intacto:** `price_format` sigue siendo `str | null`; solo cambian los productos que hoy dan `null` por `PER_1KG`.
- **RNF-2. Evidencia real:** una fixture nueva `alcampo_search_arroz.json` capturada de Alcampo (D2), con una única petición de búsqueda y respetando la pausa de ≥10 min.
- **RNF-3. Sin dependencias nuevas.**

## 5. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `PER_1KG` | `"x €/kg"` (RF-1) |
| `PER_LITRE` | `"x €/L"` (sin cambios) |
| `PER_KG`, `PER_EACH`, `PER_METER` (nunca vistos) | según D1 |
| Otra unidad (`PER_100G`…) | `null` + `WARNING` (RF-3) |
| `unitPrice` ausente | `null` (sin cambios) |

## 6. Fuera de alcance

- Buscar a propósito más unidades (más tráfico al WAF): las irá descubriendo el `WARNING` de la spec 011 con tráfico normal.
- Normalizar precios (p. ej. pasar `€/100 g` a `€/kg`).

## 7. Criterios de finalización

- [ ] RF-1 a RF-3 con tests en verde sobre la fixture real.
- [ ] `ruff`, `mypy`, `pytest` limpios; README y el docstring del mapper actualizados.
- [ ] Verificación: la captura de la fixture (D2) es la propia verificación en vivo; con ella, 0 productos `PER_1KG` con `price_format: null`.

## 8. Decisiones (resueltas el 2026-10-05: las recomendadas)

| # | Duda | Opciones | Recomendación |
|---|---|---|---|
| D1 | ¿Qué hacer con `PER_KG`, `PER_EACH` y `PER_METER`, nunca vistos? | **A)** quitarlos: la tabla solo con lo observado (`PER_LITRE`, `PER_1KG`); **B)** dejarlos como estaban y añadir `PER_1KG`; **C)** cambiarlos por las variantes `PER_1…` supuestas (`PER_1EACH`…) | **A.** `PER_KG` ya se ha demostrado falso, y nada indica que los otros dos sean ciertos (la Fase 0 ni siquiera vio `kg` en el bundle: `each`, `dose`, `meter`, `flexible_units`). Quitarlos no empeora nada: si no existen, nunca se usaban; si existen, el `WARNING` de la spec 011 lo dirá y se añadirán con su evidencia. C sería volver a adivinar |
| D2 | ¿Cómo obtener la evidencia real de `PER_1KG`? | **A)** capturar `alcampo_search_arroz.json`: 2 peticiones (portada + búsqueda, sin resolver región: la unidad no depende de la región) y recortarla a unos pocos productos; **B)** sintetizarla modificando `unitName` en el producto real de `leche` | **A.** Es lo que pide la constitución para los formatos de Alcampo; el log de la spec 011 da el nombre, pero no el `unit` ni la forma del importe que acompañan a `PER_1KG`. 2 peticiones, tras ≥10 min de pausa |
