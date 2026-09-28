# Spec 006 — Refactor y calidad

- **Estado:** aprobada (2026-09-28)
- **Fecha:** 2026-09-28
- **Referencia:** `mercadona-scraper/specs/006-mercadona-scraper-refactor` para el refactor de dependencias (`AppState` tipado, previsto en la hoja de ruta desde el plan 001), ampliado con el análisis del proyecto del 2026-09-28 tras cerrar la spec 005

> **Origen:** el análisis del 2026-09-28 dio mejoras de tres tipos y se repartieron en tres specs: esta (mantenibilidad y calidad), la **008** (protección de salida hacia Alcampo y observabilidad de la cache) y la **007** (los timeouts de Redis se suman a su degradación sin Redis).

## 1. Contexto y objetivo

El código de las specs 001–005 funciona y está bien cubierto, pero tiene huecos que no se ven hasta que algo falla. Las marcadas como **verificado** se comprobaron el 2026-09-28:

- **`app.state` sin tipos:** `settings`, `http_client` y `redis` se guardan como atributos sueltos. Un nombre mal escrito (`state.redsi`) solo falla en ejecución. Es el refactor previsto para la 006 desde el plan 001.
- **Sin comprobador de tipos** (verificado: ni `mypy` ni `pyright` en `pyproject.toml`). La constitución exige tipado estricto (#4), pero ruff (`ANN`) solo comprueba que **haya** anotaciones, no que sean **correctas**. Sin comprobador, tipar `app.state` no detectaría nada.
- **Docstring desfasado** en `app/core/dependencies.py`: describe un `AppState` que no existe.
- **`BaseHTTPMiddleware`** en `RequestContextMiddleware`: funciona, pero es más pesado que un middleware ASGI puro y arrastra limitaciones conocidas de Starlette.
- **Sin CI** (verificado: no hay `.github/`). Los tests, incluidos los de infraestructura de la 005, solo corren en una máquina. El repo aún no tiene remoto.
- **Dependencias sin límite de versión** y un `StarletteDeprecationWarning` en toda la suite: Starlette anuncia un cambio en su cliente de pruebas. Una actualización puede romper la suite sin aviso previo.
- **README inexacto:** dice `postal_code=<5 dígitos>`, pero hoy solo se exige que no esté vacío.

**Objetivo:** que los errores de tipos, las regresiones y las deprecaciones se detecten **antes** de ejecutar, en local y en CI, sin cambiar ningún comportamiento observable de la API.

## 2. Usuarios y actores

- **Desarrollador:** recibe los errores del comprobador de tipos y del CI en vez de descubrirlos en ejecución.
- **Cliente de la API:** no nota nada. Mismo contrato, mismos logs.

## 3. Historias de usuario

- **H1.** Como desarrollador, quiero que un acceso mal escrito a los recursos compartidos sea un error de tipos, no un `500` en producción.
- **H2.** Como desarrollador, quiero que cada push ejecute lint, tipos, tests y el build de Docker, para no depender de acordarme en local.
- **H3.** Como desarrollador, quiero enterarme de una deprecación el día que aparece, no el día que la librería elimina la función.

## 4. Requisitos funcionales (EARS)

### Tipado

- **RF-1.** Los recursos que crea el `lifespan` (`settings`, cliente HTTP, cliente Redis) DEBERÁN guardarse en un objeto tipado, y las dependencias DEBERÁN acceder a ellos a través de ese tipo, de modo que un nombre mal escrito sea un error del comprobador de tipos.
- **RF-2.** El `lifespan` DEBERÁ seguir creando y cerrando los mismos recursos en el mismo orden: el cambio es de tipado, no de ciclo de vida.
- **RF-3.** EL proyecto DEBERÁ incluir `mypy` en modo estricto, con el plugin de Pydantic, como dependencia de desarrollo, y `app/` DEBERÁ pasarlo sin errores (D1). `tests/` queda fuera.
- **RF-4.** El cierre de cada tarea (AGENTS.md, constitución) DEBERÁ incluir `mypy` junto a `ruff check .`, `ruff format --check .` y `pytest -q`.

### Middleware

- **RF-5.** `RequestContextMiddleware` DEBERÁ reimplementarse como middleware ASGI puro, con **exactamente** el comportamiento de las specs 003 y 004: request id generado (nunca el del cliente), líneas de inicio y fin, redacción de parámetros secretos, `X-Request-ID` en toda respuesta y `500` propio con `X-Request-ID` ante un error no controlado.
- **RF-6.** Los tests existentes del middleware y de integración DEBERÁN pasar **sin cambiar sus aserciones**: son la red de seguridad del cambio.

### Documentación del código

- **RF-7.** Los docstrings y comentarios DEBERÁN describir el código que existe; se corrige el de `app/core/dependencies.py`.
- **RF-8.** EL README DEBERÁ describir la validación real de `postal_code`: no vacío hoy; los 5 dígitos llegan en la spec 007.

### Calidad y dependencias

- **RF-9.** EL proyecto DEBERÁ tener un workflow de GitHub Actions que, en cada push y pull request, ejecute `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` y `docker build` (D2).
- **RF-10.** Cada dependencia de `pyproject.toml` DEBERÁ tener un límite superior de versión mayor (D3).
- **RF-11.** La suite DEBERÁ tratar los warnings como errores (`filterwarnings = error`), con cada excepción documentada junto a su motivo (D3).
- **RF-12.** El `StarletteDeprecationWarning` actual DEBERÁ resolverse (adaptando los tests a lo que pida Starlette) o, si hoy no es posible sin romper nada, quedar como excepción documentada con su motivo y un límite de versión que impida la rotura.

## 5. Requisitos no funcionales

- **RNF-1. Sin cambios de comportamiento:** mismo contrato, mismos códigos, mismos logs (formato y contenido). La suite de las specs 001–005 es la prueba.
- **RNF-2. Sin dependencias nuevas de producción** (constitución #1). `mypy` es solo de desarrollo (D1).
- **RNF-3. Docs vivas:** README y AGENTS.md incluyen `mypy` y el CI.

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Acceso a un recurso inexistente del estado tipado | error de `mypy`, no un `AttributeError` en ejecución (RF-1) |
| Error no controlado en una ruta con el middleware ASGI | `500 {"detail": "Internal server error"}` con `X-Request-ID` y traceback en el log, igual que hoy (RF-5) |
| Cliente que envía su propio `X-Request-ID` | se ignora, igual que hoy (RF-5) |
| Una dependencia publica una deprecación nueva | la suite falla hasta que se resuelva o se documente (RF-11) |
| Push sin remoto configurado | el workflow existe pero no se ejecuta; su validez se comprueba en local (D2) |
| El CI sin la herramienta `docker` | no aplica: los runners de GitHub la incluyen, así que los tests de compose de la 005 se ejecutan |

## 7. Fuera de alcance

- **Protección de salida hacia Alcampo** y observabilidad de la cache: spec 008.
- **Timeouts de Redis** y degradación sin Redis: spec 007.
- **Tipar `tests/`** con `mypy`: ruido sin valor proporcional.
- **Lockfile** (limitación documentada en la 005); aquí solo límites de versión mayor.
- **Publicar la imagen** o desplegar desde el CI.
- **`price` como `Decimal`:** rompería el contrato compartido con Mercadona.

## 8. Criterios de finalización

- [ ] RF-1 a RF-12 cubiertos por tests en verde o, en RF-8 a RF-10, por el fichero correspondiente.
- [ ] `mypy` estricto pasa sobre `app/`.
- [ ] La suite de las specs 001–005 sigue en verde **sin cambiar aserciones** (salvo las que el plan justifique una a una).
- [ ] `pytest -q` sin warnings no documentados.
- [ ] `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q` limpios.
- [ ] README y AGENTS.md actualizados.
- [ ] El workflow se valida en local (mismos comandos, en el mismo orden). Su ejecución real queda pendiente hasta que haya remoto, y así se anota.

## 9. Decisiones (dudas resueltas el 2026-09-28)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Comprobador de tipos | `mypy --strict` con el plugin de Pydantic, solo sobre `app/`. Se instala con `pip` como el resto; `pyright` necesitaría Node o un wrapper | RF-3, RF-4. Dependencia nueva de desarrollo, justificada aquí (constitución #1) |
| D2 | CI sin remoto | Se crea ya el workflow de GitHub Actions y se valida en local; la ejecución real queda pendiente de que exista remoto | RF-9 |
| D3 | Warnings y versiones | `filterwarnings = error`, límite superior de versión mayor en cada dependencia y excepciones documentadas una a una | RF-10, RF-11, RF-12 |
