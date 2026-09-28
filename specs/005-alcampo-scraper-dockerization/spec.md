# Spec 005 — Dockerización

- **Estado:** aprobada (2026-09-28)
- **Fecha:** 2026-09-28
- **Referencia:** misma secuencia que `mercadona-scraper/specs/005-mercadona-scraper-dockerization`, adaptada a lo que ya existe en este proyecto (`/health` desde la spec 001) y a lo que todavía no (degradación sin Redis, spec 007)

## 1. Contexto y objetivo

Hoy la API solo se ejecuta desde un entorno Python local (`uvicorn app.main:app --reload` en un venv) y necesita un Redis accesible en la URL de `REDIS_URL`. Cada máquina que la ejecute tiene que tener Python 3.11+, las dependencias correctas y un Redis ya configurados a mano. Las verificaciones manuales de las specs 003 y 004 tuvieron que sustituir Redis por fakeredis porque no había un Redis a mano.

Dos diferencias con Mercadona condicionan esta spec:

- **`GET /health` ya existe** (spec 001) y ya es público (spec 004 RF-6). Aquí solo se garantiza que siga sirviendo como sonda de vida; no hay endpoint nuevo.
- **Sin Redis, la búsqueda falla con `500`** (spec 001, caso límite "Redis caído": la degradación llega en la 007). En Mercadona la API seguía funcionando sin Redis; aquí el `docker-compose.yml` tiene que evitar que la API reciba peticiones antes de que Redis esté listo (D2).

**Objetivo:** empaquetar la API en una imagen Docker reproducible, con Redis como servicio acompañante para desarrollo y pruebas locales, **sin tocar `app/`**. `Settings` ya lee toda la configuración de variables de entorno y ese mecanismo se reutiliza tal cual.

## 2. Usuarios y actores

- **Desarrollador:** levanta el sistema completo (API + Redis) con un comando, sin instalar Python ni Redis.
- **Responsable de desplegar el scraper:** usa la imagen como artefacto de despliegue, sin gestionar el proceso Python.
- **Orquestador o daemon de Docker:** consulta la sonda de vida para marcar el contenedor como sano o reiniciarlo.

## 3. Historias de usuario

- **H1.** Como desarrollador, quiero construir y levantar la API y Redis con un solo comando, para probar el sistema completo sin instalar nada más que Docker.
- **H2.** Como responsable del despliegue, quiero que la imagen falle rápido y con un error claro si falta configuración obligatoria, para no desplegar un contenedor roto en silencio.
- **H3.** Como responsable del despliegue, quiero que Docker sepa si la API está viva sin llamar a un endpoint de negocio, para detectar y reiniciar instancias rotas sin generar tráfico hacia Alcampo.

## 4. Requisitos funcionales (EARS)

### Imagen

- **RF-1.** EL sistema DEBERÁ proveer un `Dockerfile` que construya, a partir del código fuente y de `pyproject.toml`, una imagen basada en `python:3.11-slim` que sirva la API con `uvicorn`, sin ningún cambio en `app/` (D1).
- **RF-2.** CUANDO se construya la imagen, EL sistema DEBERÁ instalar solo las dependencias de producción de `pyproject.toml`, sin el grupo `dev` (`pytest`, `pytest-asyncio`, `pytest-cov`, `respx`, `fakeredis`, `ruff`).
- **RF-3.** EL proceso de la API DEBERÁ ejecutarse dentro del contenedor con un usuario sin privilegios, nunca como `root`.
- **RF-4.** EL `CMD` de la imagen DEBERÁ arrancar `uvicorn` escuchando en `0.0.0.0:8000`, sin `--reload` y con `--no-access-log`: el middleware de la spec 003 ya registra cada petición con request id (D3). Se puede reactivar sobrescribiendo el `command` del compose, sin reconstruir la imagen.
- **RF-5.** EL sistema DEBERÁ proveer un `.dockerignore` que excluya del contexto de build, como mínimo, `.env` y `.env.*`, `.git/`, `.venv/`, `__pycache__/`, `tests/`, `specs/`, `docs/`, `.pytest_cache/`, `.ruff_cache/`, `.coverage`, `*.egg-info/` y `.atl/`.

### Configuración

- **RF-6.** Toda la configuración de `Settings` (`ALCAMPO_BASE_URL`, `REDIS_URL`, `API_KEYS`, `LOG_LEVEL`, `WAF_COOLDOWN_SECONDS`, etc.) DEBERÁ inyectarse al contenedor por variables de entorno o por un `.env`, sin ningún valor de configuración fijado en la imagen.
- **RF-7.** SI el contenedor arranca sin `ALCAMPO_BASE_URL` o sin `REDIS_URL`, ENTONCES DEBERÁ fallar al arrancar con el `ValidationError` que ya lanza `Settings`, y el contenedor DEBERÁ terminar con código distinto de 0. Nunca quedará a medio configurar.

### Composición local

- **RF-8.** EL sistema DEBERÁ proveer un `docker-compose.yml` que levante la API y un servicio Redis en la misma red interna, con `REDIS_URL` apuntando al servicio Redis del compose (no a `localhost`), aunque el `.env` local diga otra cosa.
- **RF-9.** EL `docker-compose.yml` DEBERÁ arrancar la API solo cuando Redis esté listo para aceptar conexiones, no solo cuando su contenedor haya arrancado: Redis declara un `healthcheck` (`redis-cli ping`) y la API depende de él con `condition: service_healthy` (D2).
- **RF-10.** EL puerto de la API en el host DEBERÁ ser configurable desde el compose (por defecto `8000`).
- **RF-11.** EL `docker-compose.yml` NO DEBERÁ fallar si no existe `.env` (justo tras clonar solo existe `.env.example`); en ese caso la API fallará al arrancar por RF-7, con un error claro.

### Sonda de vida

- **RF-12.** `GET /health` DEBERÁ seguir respondiendo `200` sin `X-API-Key` y sin tocar Redis ni Alcampo, de modo que la sonda nunca genere tráfico hacia el WAF de Alcampo y siga respondiendo aunque Redis esté caído.
- **RF-13.** EL `Dockerfile` DEBERÁ declarar un `HEALTHCHECK` que consulte `GET /health` periódicamente, de modo que `docker ps` y `docker compose ps` muestren el estado real (`healthy`/`unhealthy`).

### Logs

- **RF-14.** Los logs de la app DEBERÁN seguir saliendo por `stderr` sin buffer, de modo que `docker logs` / `docker compose logs` los muestren en tiempo real, con el formato y el request id de la spec 003.

## 5. Requisitos no funcionales

- **RNF-1. Sin cambios en `app/`:** todo vive en ficheros de infraestructura nuevos (`Dockerfile`, `docker-compose.yml`, `.dockerignore`).
- **RNF-2. Sin dependencias nuevas de Python** (constitución #1). El `HEALTHCHECK` no instala `curl`: usa Python y `httpx`, que ya están en la imagen.
- **RNF-3. Nunca secretos en la imagen** (constitución #12): ningún token de `API_KEYS` ni el `.env` local se copian a la imagen ni se fijan en `Dockerfile` o `docker-compose.yml`. Solo se referencian variables de entorno.
- **RNF-4. Imagen pequeña:** base `slim` y build multi-etapa, de modo que la imagen final no lleve herramientas de compilación, cachés de `pip` ni `pyproject.toml`.
- **RNF-5. Docs vivas** (constitución #9): el README explica cómo construir y levantar con Docker, qué variables hacen falta y cómo ver los logs; `AGENTS.md` añade los comandos.

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Arrancar sin `.env` (recién clonado) | `docker compose config`/`build` funcionan; `up` hace que la API termine con el `ValidationError` de `ALCAMPO_BASE_URL` (RF-7, RF-11) |
| `API_KEYS` sin configurar | la API arranca, registra el `WARNING` de la spec 004 y responde `401` a todo `/api/v1/` (fallo cerrado, no es un bug de esta spec) |
| `.env` local con `REDIS_URL=redis://localhost:6379/0` | el compose lo sobrescribe con el servicio Redis (RF-8) |
| Redis tarda en arrancar | la API no arranca hasta que Redis responde (RF-9) |
| Redis cae con la API ya levantada | las búsquedas responden `500` (spec 001, hasta la 007); `/health` sigue `200` y el contenedor sigue `healthy` (RF-12). Es una limitación conocida, no de esta spec |
| Reiniciar el compose | Redis del compose es efímero: se pierden la cache **y la marca de enfriamiento del WAF**. La siguiente búsqueda no cacheada vuelve a ir a Alcampo |
| Varias réplicas de la API con el mismo Redis | comparten cache y enfriamiento (spec 002). No se configuran réplicas en esta spec |
| Cada sonda del `HEALTHCHECK` | deja sus 2 líneas `INFO` (inicio y fin), como cualquier petición (D4). Se mitiga con `LOG_LEVEL=WARNING` o alargando el intervalo, nunca excluyendo `/health` en `app/` |
| Build sin red | falla al descargar dependencias: limitación del entorno de build, no del `Dockerfile` |
| La sonda de vida bajo autenticación por error | imposible por construcción: `require_api_key` solo cuelga del router `/api/v1` (spec 004) |

## 7. Fuera de alcance

- **Kubernetes, Helm** u otro orquestador: solo `Dockerfile` y `docker-compose.yml` locales.
- **CI/CD** y publicación de la imagen en un registry.
- **HTTPS/TLS:** lo gestiona un proxy externo, como en la spec 004.
- **Redis gestionado o persistente en producción:** el Redis del compose es para desarrollo y pruebas. En producción basta con apuntar `REDIS_URL` a otro Redis.
- **Fijar versiones de dependencias (lockfile):** `pyproject.toml` no fija versiones; dos builds en fechas distintas pueden instalar versiones distintas. Se documenta como limitación.
- **Degradación sin Redis:** llega en la spec 007.
- **Sonda de disponibilidad** (`/ready`, que comprobaría Redis): no hay orquestador que la use todavía.

## 8. Criterios de finalización

- [ ] RF-12 cubierto por tests en verde: `/health` responde `200` sin token y sin llamar a Redis ni a Alcampo.
- [ ] `ruff check .`, `ruff format --check .` y `pytest -q` limpios.
- [ ] `README.md` y `AGENTS.md` actualizados.
- [ ] **Verificación manual con el daemon de Docker arrancado** (sin el sustituto fakeredis de las specs 003 y 004):
  - `docker build` completa; la imagen no lleva `tests/`, `.env` ni dependencias `dev`; el proceso no corre como `root`.
  - Sin variables obligatorias, el contenedor termina con código distinto de 0 y el `ValidationError` en `docker logs`.
  - `docker compose up`: la API espera a Redis, pasa a `healthy`, `/health` y `/docs` responden `200`, y `GET /api/v1/products` responde `401` sin token y `200` con un token sintético, en 1 búsqueda real a Alcampo desde el contenedor, dejando al menos 10 min desde la última (D5).
  - Una segunda búsqueda igual sale de la cache (Redis del compose), sin petición a Alcampo.
  - `docker compose logs api` muestra las líneas con request id y ningún token.

## 9. Decisiones (dudas resueltas el 2026-09-28)

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Imagen base de Python | `python:3.11-slim`: la mínima que declara `pyproject.toml`. Ejecutarla en la imagen es la única prueba real de que el código funciona en 3.11 (en local se usa la 3.14). `slim` y no `alpine`, para evitar musl y compilaciones | RF-1. Se renuncia a las mejoras de rendimiento de 3.12+, irrelevantes aquí (la latencia la marca la red) |
| D2 | Orden de arranque API ↔ Redis | `healthcheck` en Redis (`redis-cli ping`) y `depends_on: condition: service_healthy` en la API | RF-9. Diferencia con Mercadona: allí la API se degradaba sin Redis; aquí, hasta la 007, sería un `500` |
| D3 | Access log de `uvicorn` | `--no-access-log` en el `CMD` de la imagen | RF-4. Diferencia con Mercadona. Reactivable sobrescribiendo el `command` |
| D4 | Ruido del `HEALTHCHECK` en los logs | Se acepta: cada sonda deja sus 2 líneas `INFO`, como cualquier petición | Caso límite de la sección 6. No se toca `app/` (RNF-1); si molesta, será una spec aparte |
| D5 | Búsqueda de la verificación manual | 1 búsqueda real a Alcampo desde el contenedor, con al menos 10 min desde la última, como en las specs 002–004 | Criterios de finalización. Prueba salida a internet, TLS y fingerprint desde la imagen. Diferencia con Mercadona (upstream simulado) |
