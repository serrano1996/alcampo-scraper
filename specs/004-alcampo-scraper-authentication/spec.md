# Spec 004 — Autenticación

- **Estado:** aprobada (2026-09-28)
- **Fecha:** 2026-09-28
- **Referencia:** misma secuencia que `mercadona-scraper/specs/004-mercadona-scraper-authentication`, más lo que exige la hoja de ruta del proyecto (fallo cerrado y `401` antes de cache y red)

## 1. Contexto y objetivo

Hoy `GET /api/v1/products` no pide ninguna credencial: cualquiera que conozca la URL puede usar la API. En Alcampo esto es más delicado que en Mercadona:

- **Tráfico ajeno contra un WAF sensible.** Cada búsqueda no cacheada de un tercero genera una petición real a Alcampo. La Fase 0 y la spec 002 demostraron que el AWS WAF de Alcampo bloquea la IP entera durante minutos con muy poco tráfico. Un tercero sin control podría provocar un bloqueo, y con él un enfriamiento, que nos dejaría sin servicio a nosotros.
- **Superficie no controlada:** no hay forma de revocar el acceso a un cliente concreto sin cambiar la URL o apagar el servicio.

**Objetivo:** que solo nuestras aplicaciones, que conocen un token compartido, puedan usar la API. Es un control de acceso de servicio a servicio, sin cuentas de usuario ni dependencias nuevas (constitución #1).

## 2. Usuarios y actores

- **Aplicación cliente autorizada:** un servicio nuestro que conoce un token válido y lo envía en cada petición.
- **Tercero no autorizado:** cualquiera sin un token válido. Debe ser rechazado siempre.
- **Responsable del servicio:** configura, rota y revoca los tokens con una variable de entorno, sin desplegar código.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que la API rechace cualquier petición sin un token válido, para que solo nuestras aplicaciones puedan usarla y ningún tercero pueda provocar un bloqueo del WAF de Alcampo.
- **H2.** Como responsable del servicio, quiero rotar o revocar un token sin desplegar código, para reaccionar rápido si un token se filtra.
- **H3.** Como responsable del servicio, quiero enterarme de los intentos rechazados sin que los logs expongan ningún token.

## 4. Requisitos funcionales (EARS)

### Control de acceso

- **RF-1.** CUANDO llegue una petición a cualquier endpoint bajo `/api/v1/`, EL sistema DEBERÁ exigir la cabecera `X-API-Key` con un valor que coincida con alguno de los tokens configurados, **antes** de consultar la cache, el enfriamiento o Alcampo.
- **RF-2.** SI `X-API-Key` falta o está vacía, ENTONCES EL sistema DEBERÁ responder `401` con un JSON de error, sin ejecutar la lógica del endpoint.
- **RF-3.** SI `X-API-Key` está presente pero no coincide con ningún token configurado, ENTONCES EL sistema DEBERÁ responder `401` con **exactamente el mismo** cuerpo que en RF-2, para no dar pistas a quien intenta adivinar un token.
- **RF-4.** EL sistema DEBERÁ comparar el token recibido con cada token configurado en tiempo constante (`secrets.compare_digest`), para no filtrar información por temporización.
- **RF-5.** EL sistema DEBERÁ comparar el valor **exacto** recibido, sin `strip()` ni cambios de mayúsculas: un token es un secreto opaco.
- **RF-6.** Los endpoints fuera de `/api/v1/` (`/docs`, `/openapi.json`, `/redoc` y `/health`) DEBERÁN seguir siendo públicos.
- **RF-15.** Toda respuesta `401` DEBERÁ incluir la cabecera `WWW-Authenticate: ApiKey`, como exige RFC 9110 (D3).

### Configuración

- **RF-7.** EL sistema DEBERÁ leer los tokens válidos de la variable `API_KEYS`, separados por comas. Al leerla, DEBERÁ recortar los espacios de cada entrada y descartar las entradas vacías (`" a , ,b "` → `{"a", "b"}`). Nunca estarán en el código.
- **RF-8.** SI `API_KEYS` no está definida o no contiene ningún token tras limpiarla, ENTONCES EL sistema DEBERÁ **fallar cerrado**: la app arranca, pero toda petición a `/api/v1/` recibe `401`.
- **RF-9.** CUANDO la app arranque sin ningún token configurado, EL sistema DEBERÁ registrar un `WARNING` indicando que todas las peticiones a `/api/v1/` se rechazarán. La app arranca igualmente (D2).

### Logging y trazabilidad (integración con la spec 003)

- **RF-10.** CUANDO se rechace una petición por autenticación, EL sistema DEBERÁ registrar un `WARNING` con la ruta y si la cabecera faltaba o era inválida, **sin incluir nunca el valor** de `X-API-Key`, válido o no.
- **RF-11.** Las respuestas `401` DEBERÁN llevar `X-Request-ID` y sus líneas de inicio y fin de petición, como cualquier otra respuesta (003 RF-3, RF-5).
- **RF-12.** Ninguna línea de log, en ningún nivel, DEBERÁ contener el valor de `X-API-Key` ni los tokens configurados en `API_KEYS` (refuerza 003 RF-17).
- **RF-14.** CUANDO la línea de inicio de petición (003 RF-3) registre los parámetros de consulta, EL sistema DEBERÁ sustituir por `'***'` el valor de todo parámetro cuyo nombre, sin distinguir mayúsculas, sea `api_key`, `apikey`, `x-api-key`, `key` o `token`. Así un token enviado por error en la URL no acaba en los logs (D1). El parámetro sigue sin autenticar.

### Documentación

- **RF-13.** EL esquema OpenAPI (`/docs`) DEBERÁ mostrar que los endpoints de `/api/v1/` requieren la cabecera `X-API-Key` (constitución #9).

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas:** `fastapi.security` (ya incluido con FastAPI) y `secrets` de la librería estándar. Nada de OAuth2 ni JWT (constitución #1).
- **RNF-2. Tipado estricto:** sin `Any` (constitución #4).
- **RNF-3. Docs vivas:** `README.md` y `.env.example` documentan `API_KEYS`. El test de la 002 que sincroniza `.env.example` con `Settings` obliga a hacerlo. El README explica cómo generar un token fuerte (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), ya que la longitud no se valida (D4).
- **RNF-4. Tests sin tokens reales:** los tests usan tokens sintéticos (constitución #12).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `X-API-Key: ` (vacía) | `401`, igual que si faltara (RF-2) |
| Token válido con un espacio delante o detrás | `401`: se compara el valor exacto (RF-5) |
| Token válido con otras mayúsculas | `401` (RF-5) |
| Varios tokens configurados (rotación sin cortes) | cualquiera de ellos es válido (RF-7) |
| `API_KEYS=" , ,"` | equivale a vacía: nadie entra y se registra el `WARNING` de arranque (RF-8, RF-9) |
| Petición con token inválido y `term` inválido | `401`, no `422`: la autenticación va antes de validar parámetros |
| Petición con token inválido durante el enfriamiento del WAF | `401`, no `502`: ni siquiera se consulta la marca (RF-1) |
| `/health`, `/docs`, `/openapi.json` sin token | `200` (RF-6) |
| Token enviado como parámetro de consulta (`?api_key=…`) | no autentica: solo vale la cabecera → `401`. En la línea de inicio aparece como `'api_key': '***'` (RF-14) |
| `?Token=abc` o `?KEY=abc` | también se ocultan: el nombre no distingue mayúsculas (RF-14) |
| `?term=token` | no se oculta: se mira el **nombre** del parámetro, no su valor |
| `401` | lleva `X-Request-ID` (RF-11) y `WWW-Authenticate: ApiKey` (RF-15) |
| `API_KEYS=abc` (token débil) | se acepta: la longitud no se valida (D4) |

## 7. Fuera de alcance

- **OAuth2, JWT, refresh tokens o cuentas de usuario:** esto es autenticación de servicio a servicio con un secreto compartido.
- **Rate limiting o cuotas por token.** La protección del tráfico hacia Alcampo sigue siendo la de la spec 002.
- **Gestión de tokens por API o panel:** se gestionan a mano con la variable de entorno.
- **Protección contra fuerza bruta** (bloqueo tras N intentos): con tokens largos y aleatorios no es necesaria para este caso.
- **HTTPS/TLS:** es responsabilidad del proxy o de la infraestructura de despliegue.

## 8. Criterios de finalización

- [ ] RF-1 a RF-15 cubiertos por tests en verde.
- [ ] Un test demuestra que una petición sin token válido **no** llama a Alcampo **ni** lee Redis (ni la cache ni la marca de enfriamiento).
- [ ] Un test demuestra que ni el token recibido ni los configurados aparecen en `caplog.text`, en ningún nivel.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] `README.md` y `.env.example` actualizados.
- [ ] **Verificación manual:** con `uvicorn`, peticiones sin cabecera, con cabecera inválida y con cabecera válida (esta última con 1 búsqueda real a Alcampo), comprobando `401`/`401`/`200` y que ningún log muestra el token.

## 9. Decisiones (dudas resueltas el 2026-09-28)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Token enviado por error en la URL | Se oculta como `'***'` en la línea de inicio si el nombre del parámetro es `api_key`, `apikey`, `x-api-key`, `key` o `token` | RF-14. Toca el middleware de la 003 |
| D2 | Arrancar sin tokens | La app arranca, rechaza todo y registra un `WARNING` al arrancar | RF-8, RF-9 |
| D3 | `WWW-Authenticate` | Se incluye `WWW-Authenticate: ApiKey` en todo `401` | RF-15. Diferencia con Mercadona |
| D4 | Tokens débiles | No se valida la longitud; el README explica cómo generar uno fuerte | RNF-3 |
