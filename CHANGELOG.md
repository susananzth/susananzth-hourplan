# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones según [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Añadido
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
