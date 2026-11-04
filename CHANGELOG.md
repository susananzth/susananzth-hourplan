# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/); versiones según [SemVer](https://semver.org/lang/es/).

## [Sin publicar]

### Añadido
- Base de la aplicación: fábrica Flask, configuración por entorno y error al arrancar si falta una variable obligatoria.
- SQLAlchemy 2.1 + Alembic con convención de nombres y migración inicial de cuentas de usuario.
- Inicio y cierre de sesión con Argon2, CSRF, límite de intentos y redirecciones seguras; comando `flask create-user`.
- Cabeceras seguras (CSP estricta, HSTS en producción), páginas de error propias y `/healthz`.
- Interfaz base con Tailwind CSS 4, HTMX 2 y Alpine.js 3 alojados en la aplicación; modo claro y oscuro.
- Imagen Docker multi-etapa no root, Docker Compose con PostgreSQL 18, Makefile y CI (lint, pruebas, auditoría, gitleaks, Trivy).
