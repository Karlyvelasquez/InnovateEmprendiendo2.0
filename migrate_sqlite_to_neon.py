from __future__ import annotations

import os
import sqlite3
import psycopg

TABLES = [
    ("users", ["id", "name", "identifier", "password_hash", "role", "active", "created_at"]),
    ("teams", [
        "id", "display_order", "name", "description", "leader", "country",
        "university", "filial", "theme_line", "source_row", "created_at",
        "manual_position",
    ]),
    ("evaluations", [
        "id", "team_id", "juror_id", "problem_score", "value_score",
        "validation_score", "business_score", "pitch_score", "observations",
        "final_score_5", "final_score_100", "created_at", "updated_at",
    ]),
    ("ranking_history", [
        "id", "team_id", "evaluation_id", "position", "score_5",
        "score_100", "recorded_at",
    ]),
    ("no_shows", ["id", "team_id", "juror_id", "created_at"]),
    ("ai_improvements", ["id", "team_id", "juror_id", "used_at"]),
]

def main() -> None:
    sqlite_path = os.environ.get("SQLITE_PATH", "innovate_pitch.db")
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("Falta DATABASE_URL.")
    if not os.path.exists(sqlite_path):
        raise SystemExit(f"No existe SQLITE_PATH: {sqlite_path}")

    source = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row

    import server
    with server.db_connect() as target:
        server.ensure_schema(target)

        # Ejecutar únicamente contra una BD Neon nueva/vacía.
        for table, _ in reversed(TABLES):
            target.execute(f"DELETE FROM {table}")

        for table, columns in TABLES:
            available = {
                row["name"]
                for row in source.execute(f"PRAGMA table_info({table})").fetchall()
            }
            selected = [c for c in columns if c in available]
            if not selected:
                continue

            rows = source.execute(
                f"SELECT {', '.join(selected)} FROM {table} ORDER BY id"
            ).fetchall()
            placeholders = ", ".join(["%s"] * len(selected))
            column_sql = ", ".join(selected)

            for row in rows:
                target._conn.execute(
                    f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",
                    tuple(row[c] for c in selected),
                )

        # No trasladar sesiones locales: obliga a iniciar sesión de nuevo en producción.
        target.execute("DELETE FROM sessions")

        for table, _ in TABLES:
            target._conn.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE((SELECT MAX(id) FROM {table}), 1),
                    (SELECT COUNT(*) > 0 FROM {table})
                )
                """
            )
        target.commit()

    source.close()
    print("Migración SQLite → Neon completada.")
    print("Las sesiones locales NO fueron migradas.")

if __name__ == "__main__":
    main()
