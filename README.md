# Innóvate Pitch — Evaluación de Pitch

Aplicación web para que jurados evalúen los pitch de los equipos y los administradores gestionen ranking, ganadores y exportaciones (PDF / Excel).

- **Backend:** Python 3.13 (servidor HTTP de la librería estándar) + PostgreSQL en Neon (`psycopg`).
- **Frontend:** HTML, CSS y JavaScript sin dependencias, servidos por el mismo backend.
- **Despliegue:** Vercel (función Python) + Neon.

## Estructura

```text
App Pitch/
├── backend/
│   ├── api/index.py               # Entrypoint de Vercel
│   ├── server.py                  # Servidor HTTP, API /api/* y archivos estáticos
│   ├── pg_compat.py               # Capa de compatibilidad sqlite3 → PostgreSQL
│   ├── teams_schedule.csv         # Cronograma de equipos (semilla inicial, solo local)
│   ├── users_seed.example.json    # Formato de ejemplo para usuarios
│   └── migrate_sqlite_to_neon.py  # Script de mantenimiento: migración SQLite → Neon
├── frontend/
│   ├── index.html
│   ├── app.js
│   ├── styles.css
│   └── assets/                    # fondo.png, logos EPM
├── requirements.txt               # Dependencias Python (Vercel lo lee desde la raíz)
├── vercel.json
├── .python-version
├── Procfile
└── README.md
```

El backend sirve la carpeta `frontend/` como archivos estáticos y atiende la API en `/api/...`. Como el frontend usa rutas relativas (`/api/...`), ambos se comunican en el mismo origen sin configuración adicional.

## Configuración local

1. Crear el entorno e instalar dependencias (desde la raíz):

   ```powershell
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. Crear `backend/.env` (no se sube a git):

   ```text
   DATABASE_URL=postgresql://...
   GEMINI_API_KEY=...
   ```

3. Usuarios iniciales: copiar `backend/users_seed.example.json` a `backend/users_seed.json` y completarlo, o definir la variable `USERS_SEED_JSON` con el mismo contenido.

4. Ejecutar:

   ```powershell
   python backend/server.py
   ```

   Abrir <http://127.0.0.1:8000>. Se pueden cambiar `HOST` y `PORT` con variables de entorno.

## Despliegue en Vercel

Variables de entorno necesarias en Vercel:

```text
DATABASE_URL=...
GEMINI_API_KEY=...
USERS_SEED_JSON=...   (opcional, solo para sembrar usuarios)
```

`vercel.json` enruta todas las peticiones a `backend/api/index.py`, que sirve tanto la API como el frontend. En Vercel no se ejecuta `serve_forever()`; la inicialización de la base de datos es idempotente y nunca borra datos existentes.

## Script de mantenimiento

Ejecutar desde la carpeta `backend/`:

```powershell
cd backend

# Migrar una base SQLite local a una base Neon nueva/vacía
$env:SQLITE_PATH="innovate_pitch.db"
$env:DATABASE_URL="TU_CONNECTION_STRING_DE_NEON"
python migrate_sqlite_to_neon.py
```

## Archivos que no se suben al repositorio

Están en `.gitignore`:

- **Secretos:** `.env` (conexión a Neon y clave de Gemini), `users_seed.json` (usuarios y contraseñas), llaves y credenciales (`*.pem`, `*.key`, etc.).
- **Datos personales:** `teams_schedule.csv` (nombres completos de los participantes). Solo se usa para sembrar una base vacía; si falta, el servidor arranca igual.
- **Datos locales y exportes:** bases SQLite (`*.db`, `*.db-shm`, `*.db-wal`) y archivos exportados con resultados (`*.xlsx`, `*.pdf`).
- **Generados:** `.venv/`, `__pycache__/`, `.vscode/`, logs y temporales.
