# Innovate PITCH — Vercel + Neon

## Base SQLite entregada y verificada

La base `innovate_pitch.db` suministrada fue revisada y está íntegra:

- 11 usuarios
- 66 equipos
- 0 evaluaciones
- 66 registros de historial de ranking
- 0 no-shows
- 0 registros de mejoras IA
- 1 sesión local
- SQLite `integrity_check`: `ok`
- SQLite usa WAL

La migración incluye `manual_position` de `teams`, que existe en tu BD actual.

Las sesiones locales no se migran; producción pedirá login nuevamente.

## Archivos que debes añadir/reemplazar

```text
api/index.py
pg_compat.py
migrate_sqlite_to_neon.py
vercel.json
requirements.txt
server.py
```

Conserva tu frontend actual:

```text
index.html
app.js
styles.css
fondo.png
EPM-logo-transaparent.png
users_seed.json
```

No subas a GitHub:

```text
.env
innovate_pitch.db
innovate_pitch.db-shm
innovate_pitch.db-wal
users_seed.json
```

## Migración

Haz una copia de seguridad de `innovate_pitch.db`.

En PowerShell:

```powershell
pip install "psycopg[binary]>=3.2,<4"
$env:SQLITE_PATH="innovate_pitch.db"
$env:DATABASE_URL="TU_CONNECTION_STRING_DE_NEON"
python migrate_sqlite_to_neon.py
```

Haz esto sobre una base Neon nueva/vacía.

Después comprueba en Neon:

```text
users              11
teams              66
evaluations         0
ranking_history    66
no_shows            0
ai_improvements     0
sessions            0
```

## Vercel

Configura:

```text
DATABASE_URL=...
GEMINI_API_KEY=...
```

El entrypoint es:

```text
api/index.py
```

No se ejecuta `ThreadingHTTPServer(...).serve_forever()` en Vercel.

## Frontend

Las llamadas del `app.js` deben ser relativas:

```text
/api/...
```

No deben apuntar a:

```text
http://127.0.0.1:8000/...
```

## Prueba final

1. Abrir la página.
2. Login.
3. Cargar equipos.
4. Abrir un equipo.
5. Crear una evaluación de prueba.
6. Confirmar la evaluación en Neon.
7. Revisar ranking.
8. Cerrar sesión.
9. Volver a iniciar sesión.
10. Probar dashboards de jurado y admin.

No borres la BD local hasta completar las pruebas.
