# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones según [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Añadido
- Perfiles de metodología por proyecto (Cascada, Scrum, Kanban, Híbrido, Historias): el perfil decide qué pantallas se ofrecen, sin cambiar las horas guardadas. Épicas, sprints (uno activo a la vez; los cerrados quedan como historial) e historias con tablero por estado, y totales de historias, plan y horas reales por sprint y por épica. El proyecto de demostración PJ-00002 corre como Scrum.
- Trabajo ágil: puntos de historia (con avance de puntos por sprint y épica), límites de trabajo en curso por columna del tablero, gráfico de avance (burndown) por sprint en puntos o, si no hay puntos, en horas, y cierre de sprint que pasa lo no terminado a un sprint planificado o a pendientes y conserva lo que el sprint tenía como historial. Mover una historia a un estado de cierre la marca terminada; sacarla de ahí la reabre. Las pantallas ágiles están en español e inglés.
- Endurecimiento: `make backup` y `make restore` (guía en `docs/respaldos.md`), guía del overlay (`docs/overlay.md`), acceso opcional con la cuenta de la organización por OpenID Connect (`OIDC_*`; solo entran personas ya dadas de alta), accesibilidad (tablas con nombre y encabezados, navegación, foco visible, menos movimiento) e interfaz en español e inglés con selector de idioma (menú, inicio, acceso y errores traducidos; el resto se traduce de a poco y mientras tanto se muestra en español).
- Pruebas de navegador (`make e2e`) con Playwright en Docker sobre una base descartable con los datos demo; de paso regeneran las capturas de `docs/capturas`. `make css` reconstruye el CSS compilado.
- `flask link-resource CORREO` vincula una cuenta con su recurso (reutiliza uno libre con ese nombre); sin ese vínculo no se pueden registrar horas.
- `make demo` ahora incluye una ronda observada, una ronda pendiente y un proyecto cerrado con su nota de desvío.
- Inicio con accesos a las áreas principales.
- Importación de hojas de horas de Excel (una fila por tarea y día pasa a una entrada de horas por día): `flask import-hours`, `import-report` e `import-revert`. Es idempotente, reversible por lote y genera un informe de calidad (códigos no válidos, tickets múltiples, horas fuera de rango, títulos distintos para un mismo código). Las ausencias se guardan aparte. Mapeo ajustable con `import_mapping.toml` en el overlay.
- Esquema del dominio: proyectos y tickets con código externo, plantillas y fases configurables, ciclos de verificación y aprobación, solicitudes de cambio, enlaces entre trabajos, versiones de plan y asignaciones, entradas de horas, ausencias y banco de horas.
- Catálogos configurables, carteras, aplicaciones y repositorios; recursos asignables con o sin cuenta.
- Roles (admin, pm, lead, developer, observer) aplicados en el servidor; comandos `flask grant-role` y `flask revoke-role`, y `--role` en `flask create-user`.
- Registro de auditoría con actor, cambios, justificación y usuario afectado.
- Políticas de evaluación y de jornada con fecha de vigencia.
- Comando `flask seed` (idempotente) con semilla neutra en TOML, reemplazable desde el overlay.
- Base de la aplicación: fábrica Flask, configuración por entorno y error al arrancar si falta una variable obligatoria.
- SQLAlchemy 2.1 + Alembic con convención de nombres y migración inicial de cuentas de usuario.
- Inicio y cierre de sesión con Argon2, CSRF, límite de intentos y redirecciones seguras; comando `flask create-user`.
- Cabeceras seguras (CSP estricta, HSTS en producción), páginas de error propias y `/healthz`.
- Interfaz base con Tailwind CSS 4, HTMX 2 y Alpine.js 3 alojados en la aplicación; modo claro y oscuro.
- Imagen Docker multi-etapa no root, Docker Compose con PostgreSQL 18, Makefile y CI (lint, pruebas, auditoría, gitleaks, Trivy).
