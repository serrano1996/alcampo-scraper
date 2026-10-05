# Spec 014 — Dependencias fijadas (lockfile) y CI al día

- **Estado:** aprobada (2026-10-05)
- **Fecha:** 2026-10-05
- **Referencia:** limitación "Imagen Docker no reproducible al 100 %" del README; spec 006 RF-10 (rangos `<siguiente major`) y RF-9 (CI)

## 1. Contexto y objetivo

Estado verificado el 2026-10-05:

- **Nada está fijado.** `pyproject.toml` declara rangos (`fastapi>=0.141,<1`…), y tanto el `Dockerfile` (`pip install .`) como la CI (`pip install -e ".[dev]"`) instalan lo último que cumpla el rango ese día. Dos builds en fechas distintas pueden llevar versiones distintas de fastapi, pydantic, httpx, redis o de sus dependencias transitivas (starlette, anyio…), sin que nadie lo decida. Un fallo puede aparecer en producción sin un solo cambio en el repo.
- **Sin verificación de integridad:** se instala lo que sirva PyPI, sin hashes.
- **`uvicorn[standard]` trae `uvloop`, que no existe en Windows** (desarrollo local) pero sí en la imagen y en la CI (Linux): un lock generado en Windows no vale para la imagen, y al revés.
- **CI con avisos (2026-10-05, run 37295889158):** `actions/checkout@v4` y `actions/setup-python@v5` apuntan a Node 20, ya obsoleto en los runners (se fuerzan a Node 24); `ubuntu-latest` pasa a Ubuntu 26 desde el 2026-10-19. Últimas versiones: `checkout` v7.0.1, `setup-python` v7.0.0.

**Objetivo:** que la imagen y la CI instalen **exactamente** las versiones revisadas, con hashes, que actualizarlas sea un paso explícito y documentado, y que la CI deje de depender de versiones obsoletas o móviles.

## 2. Historias de usuario

- **H1.** Como responsable del servicio, quiero que dos builds del mismo commit instalen las mismas dependencias.
- **H2.** Como responsable del servicio, quiero actualizar dependencias cuando yo decida, con un comando documentado, y verlo en el diff.
- **H3.** Como responsable del servicio, quiero una CI sin avisos de obsolescencia y sin cambios de sistema operativo por sorpresa.

## 3. Requisitos funcionales (EARS)

### A. Lockfiles

- **RF-1.** El repositorio DEBERÁ incluir `requirements.lock` (dependencias de producción) y `requirements-dev.lock` (producción + extra `dev`), con versión exacta y hashes de **todas** las dependencias, transitivas incluidas, generados a partir de `pyproject.toml` (D1, D3).
- **RF-2.** Los lockfiles DEBERÁN generarse para la plataforma de la imagen y de la CI: Linux y Python 3.11 (D2).
- **RF-3.** El `Dockerfile` DEBERÁ instalar las dependencias desde `requirements.lock` verificando los hashes, y después el paquete sin resolver dependencias (`--no-deps`).
- **RF-4.** La CI DEBERÁ instalar desde `requirements-dev.lock` (con hashes) y después el paquete con `--no-deps`; si el lock no satisface `pyproject.toml`, DEBERÁ fallar (`pip check`).
- **RF-5.** Actualizar las dependencias DEBERÁ ser un comando documentado en el README que regenera ambos lockfiles; sin él, nada cambia de versión.

### B. CI

- **RF-6.** La CI DEBERÁ usar `actions/checkout@v7` y `actions/setup-python@v7`, y un runner fijo (`ubuntu-24.04`) en lugar de `ubuntu-latest` (D5).

## 4. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas del proyecto** (constitución #1): `pip-tools` solo se usa, en un contenedor desechable, para generar los lockfiles; no entra en `pyproject.toml`.
- **RNF-2. Contrato y comportamiento intactos:** mismas versiones que hoy (las que resuelva el rango el día de la generación).
- **RNF-3.** El desarrollo local en Windows sigue con `pip install -e ".[dev]"` (D4).

## 5. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Alguien añade una dependencia a `pyproject.toml` sin regenerar el lock | la CI falla en `pip check` (RF-4) |
| Un paquete en PyPI cambia de contenido con la misma versión | la instalación falla por hash (RF-3, RF-4) |
| Se publica una versión nueva de fastapi | la imagen no la coge hasta regenerar el lock (RF-5) |
| `uvloop` en Windows | no afecta: el lock es para Linux; en local, `pip install -e ".[dev]"` (RNF-3) |

## 6. Fuera de alcance

- Actualizaciones automáticas (Dependabot, Renovate).
- Migrar a `uv` o Poetry.
- Fijar la imagen base por digest (`python:3.11-slim@sha256:…`).

## 7. Criterios de finalización

- [ ] Lockfiles en el repo; `docker build` y la CI instalan desde ellos con hashes.
- [ ] Un test (sin red) comprueba que cada dependencia directa de `pyproject.toml` aparece fijada (`==`) y con hash en el lock que le toca.
- [ ] CI en verde con las acciones v7 y `ubuntu-24.04`, sin avisos de Node 20.
- [ ] README: cómo se actualizan las dependencias; se quita la limitación "no reproducible".

## 8. Decisiones (resueltas el 2026-10-05: las recomendadas)

| # | Duda | Opciones | Recomendación |
|---|---|---|---|
| D1 | Herramienta | **A)** `pip-tools` (`pip-compile`); **B)** `uv lock`; **C)** `pip freeze` | **A.** Estándar, salida `requirements` que `pip` instala sin nada más; B cambia la forma de instalar el proyecto (y lo separa de Mercadona); C es manual y arrastra lo que haya en el entorno |
| D2 | Dónde generarlos | **A)** contenedor desechable `python:3.11-slim` (Linux, 3.11, como la imagen y la CI); **B)** en local (Windows, 3.14) | **A.** B resolvería para otra plataforma y otra versión de Python (p. ej. sin `uvloop`) |
| D3 | ¿Hashes? | **A)** sí, en los dos; **B)** solo versiones | **A.** Cuesta lo mismo y protege contra un paquete sustituido en PyPI |
| D4 | Desarrollo local | **A)** sigue con `pip install -e ".[dev]"`; **B)** también desde el lock | **A.** El lock es para Linux (B fallaría en Windows por `uvloop`); la CI, que usa el lock, es la referencia |
| D5 | Versiones de CI | **A)** `@v7` y `ubuntu-24.04`; **B)** fijar por SHA de commit | **A.** B es más estricto pero exige actualizarlos a mano sin ayuda (fuera de alcance Dependabot); `ubuntu-24.04` evita el salto a Ubuntu 26 sin revisarlo |
