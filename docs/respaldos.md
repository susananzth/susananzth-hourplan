# Respaldos

La base de datos es lo único que hay que respaldar (el código está en Git y la configuración de la organización en el overlay).

- `make backup`: crea `backups/hourplan-AAAAMMDD-HHMMSS.dump` (formato personalizado de `pg_dump`) y conserva los 14 más recientes (`BACKUP_KEEP=30 make backup` para cambiarlo).
- `make restore F=backups/archivo.dump`: detiene `web`, reemplaza los datos con el respaldo y vuelve a levantar `web`.
- Copia la carpeta `backups/` fuera del servidor (otro disco o almacenamiento de la organización); un respaldo en la misma máquina no protege de una falla del disco.

## Programarlo (cron)

```
15 2 * * * cd /ruta/a/hourplan && make backup >> backups/cron.log 2>&1
```

## Probar la restauración

Un respaldo que nunca se restauró no es de fiar. Pruébalo en una instancia aparte (otro `POSTGRES_DB` o un equipo de desarrollo) antes de necesitarlo.
