# Guía del overlay (configuración de la organización)

El overlay es una carpeta aparte, fuera de este repositorio, con la configuración propia de cada organización. No contiene código. Se monta en el contenedor `web` y se indica con `HOURPLAN_OVERLAY_DIR`.

| Archivo | Para qué sirve |
|---|---|
| `seed.toml` | Catálogos, plantillas de fases, políticas y siglas. Sustituye a la [semilla neutra](../src/hourplan/setup/seed.toml) al ejecutar `make seed`. |
| `import_mapping.toml` | Cómo se leen las columnas de los Excel que se importan. |

## Pasos

1. Crea un repositorio privado con esos archivos (parte de la semilla neutra y edítala).
2. En el servidor, clónalo, por ejemplo en `/opt/hourplan-overlay`.
3. En `.env`: `HOURPLAN_OVERLAY_DIR=/overlay`.
4. En un `compose.override.yaml` monta la carpeta en solo lectura:

```yaml
services:
  web:
    volumes:
      - /opt/hourplan-overlay:/overlay:ro
```

5. `make seed` (es idempotente: se puede repetir al cambiar el overlay).

Nunca pongas secretos en el overlay; van en `.env`, que no se versiona.
