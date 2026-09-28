# Plan 005 — Dockerización

- **Estado:** aprobado (2026-09-28). Los supuestos pendientes de §2 se verifican en T5. Entrega: un solo PR (§10)
- **Fecha:** 2026-09-28
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D5**; las de specs anteriores como **004-plan-Dn**, etc. Las decisiones de diseño de este plan son **D1…**

## 1. Visión general

```
docker compose up
  ├─ redis   redis:7-alpine, healthcheck "redis-cli ping", sin volumen (efímero)      RF-8, RF-9
  └─ api     imagen del Dockerfile, espera a redis "service_healthy"                   RF-9
       ├─ env_file .env (opcional) + environment REDIS_URL=redis://redis:6379/0        RF-6, RF-8, RF-11
       ├─ puerto host ${API_PORT:-8000} → 8000                                         RF-10
       └─ HEALTHCHECK → GET http://127.0.0.1:8000/health (sin token, sin Redis)        RF-12, RF-13

Dockerfile (multi-etapa)
  builder  python:3.11-slim → venv /opt/venv → pip install .  (solo dependencias de producción)   RF-2
  runtime  python:3.11-slim → COPY /opt/venv, usuario sin privilegios (uid 10001)               RF-1, RF-3
           CMD uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log                 RF-4

.dockerignore: lista de permitidos — solo entran pyproject.toml y app/                   RF-5, RNF-3
```

`app/` no cambia (RNF-1): `Settings` ya falla con `ValidationError` sin `ALCAMPO_BASE_URL`/`REDIS_URL` (RF-7), `/health` ya existe y no toca nada (RF-12), y los logs ya van a `stderr` (RF-14).

## 2. Verificaciones previas

### Hechas sin Docker (2026-09-28)

| Supuesto | Experimento | Resultado | Consecuencia |
|---|---|---|---|
| `pip install .` necesita `README.md` (lo declara `readme` en `pyproject.toml`) | `pip wheel . --no-deps` en una copia con solo `pyproject.toml` y `app/` | El wheel se construye sin `README.md` | El contexto de build no necesita el README (D5) |
| El wheel incluye el código | Listado del wheel | 26 ficheros bajo `app/` | Instalar el proyecto ya deja `app` en `site-packages`: no hace falta copiar `app/` aparte (D1) |
| `uvicorn` admite desactivar su access log | `uvicorn --help` | `--access-log / --no-access-log` | spec-D3 sin configuración extra |
| Compose admite `env_file` opcional | `docker compose version` | v5.3.1 (`required: false` existe desde v2.24) | RF-11 sin trucos |
| Nuestro handler vacía el buffer en cada línea | `logging.StreamHandler.emit` llama a `flush()` | Sí | RF-14 se cumple ya; `PYTHONUNBUFFERED=1` es solo refuerzo para otras salidas (D3) |
| `/health` no toca Redis ni Alcampo | Lectura de [app/main.py](../../app/main.py): la función no recibe dependencias ni usa `app.state` | Por construcción | RF-12 se fija con un test de regresión (T1), no con código |

### Pendientes: el daemon de Docker está apagado

| Supuesto | Cómo se verifica | Si falla |
|---|---|---|
| `uvicorn[standard]` (uvloop, httptools) instala en `python:3.11-slim` sin compilar | `docker build` | Se para y se avisa: habría que añadir herramientas de compilación al `builder` |
| El `HEALTHCHECK` con `httpx` pasa a `healthy` | `docker compose ps` | Se para y se avisa |
| Los certificados TLS de la imagen `slim` valen para Alcampo | 1 búsqueda real (spec-D5) | Se para y se avisa |

Se verifican en la **primera tarea que construye la imagen** (T5). **Alternativa:** si arrancas Docker Desktop antes de aprobar el plan, los verifico ahora y el plan se aprueba sin supuestos abiertos.

## 3. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `.dockerignore` | **nuevo**: lista de permitidos | RF-5, RNF-3 |
| `Dockerfile` | **nuevo**: `builder` + `runtime`, usuario sin privilegios, `HEALTHCHECK`, `CMD` | RF-1…RF-4, RF-13, RF-14, RNF-2, RNF-4 |
| `docker-compose.yml` | **nuevo**: `api` + `redis` con healthcheck | RF-6, RF-8…RF-11 |
| `tests/integration/test_health.py` | **nuevo**: `/health` sin Redis ni Alcampo | RF-12 |
| `tests/infra/` | **nuevo**: tests de los tres ficheros de infraestructura (D7) | RF-3…RF-5, RF-8…RF-11, RF-13, RNF-3 |
| `README.md`, `AGENTS.md` | sección Docker y comandos | RNF-5 |

`app/`, `pyproject.toml` y `.env.example` **no cambian**: no hay variables nuevas (`API_PORT` es del compose, no de `Settings`, así que el test de sincronización de `.env.example` no se ve afectado).

## 4. Modelo de datos

Sin cambios. Nota operativa: el Redis del compose no tiene volumen, así que al recrearlo se pierden la cache y la marca `waf:cooldown` (caso límite de la spec).

## 5. Decisiones de diseño

**D1 — Multi-etapa con un venv que se copia entero.** `builder` crea `/opt/venv`, instala el proyecto con `pip install --no-cache-dir .` y `runtime` copia solo `/opt/venv`. El código viaja dentro del venv (el wheel incluye `app/`, verificado en §2), así que hay **una sola copia** del código y no se arrastran `pyproject.toml` ni cachés (RNF-4).
- *Descartada (Mercadona):* copiar `/usr/local/lib/python3.11/site-packages` **y** `/usr/local/bin` **y** `app/`. Deja el código dos veces (en `site-packages` y en `/app`) y copia binarios del sistema que no hacen falta.
- *Descartada:* una sola etapa. Deja `pip`, su cache y los metadatos de build en la imagen final.

**D2 — Usuario sin privilegios con uid fijo 10001.** `useradd --system --uid 10001 --no-create-home app` y `USER 10001` (numérico). Un orquestador con `runAsNonRoot` solo puede comprobar un uid numérico. El venv es de `root` y de solo lectura para `app`: el proceso no puede modificar su propio código (RF-3).

**D3 — `ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1`.** Nuestro handler ya vacía el buffer en cada línea (§2); `PYTHONUNBUFFERED` cubre cualquier otra salida para que `docker logs` sea en tiempo real (RF-14). `PYTHONDONTWRITEBYTECODE` evita intentos de escribir `.pyc` en un venv de solo lectura.

**D4 — `HEALTHCHECK` con Python y `httpx`, contra `127.0.0.1`, sin proxy.**

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx, sys; sys.exit(httpx.get('http://127.0.0.1:8000/health', timeout=3, trust_env=False).status_code != 200)"]
```

- `127.0.0.1` y no `localhost`: `localhost` puede resolverse a `::1` (IPv6) y `uvicorn` solo escucha en IPv4 (`0.0.0.0`).
- `trust_env=False`: si quien despliega define `HTTP_PROXY`, la sonda no debe ir al proxy.
- Cualquier excepción (conexión rechazada, timeout) termina con código distinto de 0: `unhealthy`.
- *Descartada:* `curl`. Instalarlo añade un paquete y superficie de ataque solo para esto (RNF-2).
- **Aviso:** Docker marca el contenedor como `unhealthy`, pero **no lo reinicia** por sí solo; eso lo hace un orquestador (Swarm, Kubernetes…), fuera de alcance. Se documenta en el README.

**D5 — `.dockerignore` como lista de permitidos.** `*` excluye todo y solo se readmiten `pyproject.toml` y `app/`, y después se vuelven a excluir `**/__pycache__/` y `**/*.py[cod]`. Cumple RF-5 con margen: cualquier fichero nuevo (otro `.env.local`, un `credentials.json`…) queda fuera **por defecto**, sin acordarse de añadirlo (RNF-3).
- *Descartada (Mercadona):* lista de excluidos. Un fichero secreto nuevo entra en el contexto hasta que alguien lo añade a la lista.

**D6 — `docker-compose.yml`.**
- `api`: `build: .`, `env_file: [{path: .env, required: false}]` (RF-11), `environment: REDIS_URL: redis://redis:6379/0`, que prevalece sobre el `.env` (RF-8), `ports: "${API_PORT:-8000}:8000"` (RF-10), `depends_on: redis: condition: service_healthy` (RF-9, spec-D2).
- `redis`: `image: redis:7-alpine` (versión mayor fijada, no `latest`), `healthcheck: test: ["CMD", "redis-cli", "ping"]`, intervalo de 5 s, 10 reintentos. Sin volumen y sin puerto publicado: solo la API lo necesita.
- Sin `API_KEYS` ni ninguna otra variable de `Settings` escrita en el compose: todo sale de `.env` (RNF-3).

**D7 — Tests de los ficheros de infraestructura.** Construir imágenes en `pytest` es lento y exige el daemon, pero las propiedades **de seguridad** no pueden quedar solo en una verificación manual que nadie repite. Tres tipos:
- **`.dockerignore` y `Dockerfile`:** lectura de texto línea a línea (formatos sencillos): `.dockerignore` empieza por `*` y solo readmite `pyproject.toml` y `app/`; `Dockerfile` usa `python:3.11-slim` en ambas etapas, tiene `USER` no `root` antes del `CMD`, el `CMD` lleva `--no-access-log` y no `--reload`, tiene `HEALTHCHECK` contra `/health` y no menciona `API_KEYS` ni `.env`.
- **`docker-compose.yml`:** no hay parser de YAML en las dependencias y no se añade uno (constitución #1). Se usa el propio Docker: `docker compose -f <copia en tmp_path> config --format json`, que **no necesita el daemon**. Se copia a un directorio temporal para que no lea el `.env` real (los secretos nunca llegan a la salida del test) y, de paso, prueba RF-11 (sin `.env` no falla). Si el CLI `docker` no está instalado, el test se salta con `skipif` y un motivo claro.
- **`/health`:** test de integración con la app real (T1).

**D8 — Build sin `--reload` y sin montar código.** La imagen es para ejecutar, no para desarrollar (RF-4). Para desarrollar se sigue usando el venv local.

## 6. Regresiones previstas

Ninguna: no cambia `app/` ni los fixtures. La única interacción es con `tests/core/test_env_example.py`, que no se ve afectado porque `API_PORT` no es un campo de `Settings` (§3).

## 7. Estrategia de test por RF

**U** = unitario (lectura de ficheros), **C** = `docker compose config` (sin daemon, `skipif` sin CLI), **I** = integración (app real + `lifespan` + fakeredis + respx), **M** = verificación manual con Docker.

| RF | Test | Tipo |
|---|---|---|
| RF-12 | `/health` sin cabecera con un Redis que falla en cualquier operación y sin rutas respx → `200 {"status": "ok"}`. No hay RED real (ya pasa): se hace una **mutación** (que `/health` haga `ping` a Redis) y se comprueba que el test falla | I |
| RF-5, RNF-3 | `.dockerignore`: primera regla `*`; readmisiones exactas `{pyproject.toml, app/}`; `__pycache__` excluido otra vez | U |
| RF-1, RF-2, spec-D1 | `Dockerfile`: dos `FROM python:3.11-slim`, `pip install` sin `[dev]` | U |
| RF-3 | `USER` distinto de `root`/`0` y posterior al último `FROM` | U |
| RF-4, spec-D3 | `CMD` en forma exec con `0.0.0.0`, `8000`, `--no-access-log` y sin `--reload` | U |
| RF-13 | `HEALTHCHECK` que apunta a `/health` | U |
| RNF-3 | `Dockerfile` no contiene `API_KEYS` ni `.env` | U |
| RF-8 | `services.api.environment.REDIS_URL == "redis://redis:6379/0"` | C |
| RF-9 | `services.api.depends_on.redis.condition == "service_healthy"` y `services.redis.healthcheck.test` contiene `ping` | C |
| RF-10 | puerto publicado `8000` por defecto | C |
| RF-11 | `config` funciona sin `.env` en el directorio | C |
| RNF-3 | `services.api.environment` no contiene `API_KEYS` | C |
| RF-1…RF-4, RF-7, RF-9, RF-12…RF-14 | build, contenido de la imagen, `whoami`, fallo sin variables, `healthy`, `401`/`200` con 1 búsqueda real, cache, logs sin tokens | M |

Cobertura: no hay código Python nuevo en `app/`.

## 8. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | Los tests de texto del `Dockerfile` son frágiles ante cambios de formato | Falsos rojos | Solo se comprueban propiedades de seguridad y contrato (base, usuario, `CMD`, secretos), no el orden de cada línea |
| R2 | Sin lockfile, dos builds pueden instalar versiones distintas | Imagen no reproducible al 100% | Fuera de alcance (spec §7), documentado como limitación |
| R3 | Docker no reinicia contenedores `unhealthy` | Una instancia colgada sigue colgada | Documentado (D4); lo resuelve un orquestador |
| R4 | La verificación manual depende del daemon de Docker, apagado en las specs 003 y 004 | La spec no se puede cerrar | Criterio explícito de la spec: sin Docker no se cierra (no vale fakeredis) |
| R5 | La búsqueda real desde el contenedor dispara el WAF | Bloqueo de la IP 2–4 min | 1 sola búsqueda, ≥10 min desde la última (spec-D5); la segunda sale de la cache |

## 9. Secuencia de implementación

1. **`/health`:** test de regresión de RF-12 (+ mutación).
2. **`.dockerignore`** con sus tests.
3. **`Dockerfile`** con sus tests.
4. **`docker-compose.yml`** con sus tests.
5. **Verificación manual de la imagen:** build (y supuestos pendientes de §2), contenido, usuario, fallo sin variables, `/health` y `/docs`.
6. **Verificación manual del compose:** espera a Redis, `healthy`, `401`/`200` con 1 búsqueda real, cache, logs.
7. **Docs:** README (sección Docker, limitaciones) y `AGENTS.md`.

## 10. Estimación y entrega

| Bloque | Infra | Tests | Docs | Total |
|---|---|---|---|---|
| Todo | ~70 | ~150 | ~50 | **~270** |

**Por debajo de las 400 líneas: un solo PR.**

## 11. Qué no cambia

El código de la aplicación, el contrato de la API, la configuración (`Settings` y `.env.example`) y la forma de desarrollar en local con el venv.
