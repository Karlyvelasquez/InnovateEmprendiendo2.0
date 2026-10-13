from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import pg_compat as sqlite3
import time
import unicodedata
import zipfile
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import partial
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
# Archivos estáticos del frontend (index.html, app.js, styles.css, assets/).
FRONTEND_DIR = ROOT.parent / "frontend"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
TEAMS_CSV_PATH = ROOT / "teams_schedule.csv"
# Tamaño de "bloque" reservado para cada día en display_order. Los equipos de
# un mismo día siempre ocupan un rango contiguo (día 1: 1..N, día 2: N+1..M),
# lo que permite mover un equipo al final de SU día (por inasistencia) sin
# tocar el orden de los equipos del otro día.
SCHEDULE_DAYS_ORDER = ["19 de agosto", "20 de agosto"]
SESSION_COOKIE = "innovate_pitch_session"
SESSION_TTL_HOURS = 24 * 7
PBKDF2_ITERATIONS = 210_000

ROLE_ADMIN = "admin"
ROLE_JURY = "jury"
WINNERS_COUNT = 20
FINALISTS_COUNT = 10
PITCH_ONE = 1
PITCH_TWO = 2
PITCH_THREE = 3
ACTIVE_PITCH_SETTING_KEY = "active_pitch"

def load_seed_users() -> list[dict[str, str]]:
    """Load the admin/jury account list, preferring the USERS_SEED_JSON
    environment variable (handy for cloud deploys like Railway, where you
    paste the JSON directly into the platform's env var settings) and
    falling back to users_seed.json next to this script for local dev.
    Neither the env var value nor users_seed.json should ever be committed
    to git. See users_seed.example.json for the expected format."""
    raw_text = os.environ.get("USERS_SEED_JSON", "").strip()
    source = "la variable de entorno USERS_SEED_JSON"
    if not raw_text:
        seed_path = ROOT / "users_seed.json"
        source = str(seed_path)
        if not seed_path.exists():
            print(
                "ADVERTENCIA: no hay USERS_SEED_JSON configurada ni se encontró "
                "users_seed.json junto a server.py. No se sembrará ningún usuario. "
                "Copia users_seed.example.json a users_seed.json (local) o pega su "
                "contenido en la variable de entorno USERS_SEED_JSON (en la nube)."
            )
            return []
        raw_text = seed_path.read_text(encoding="utf-8")

    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{source} tiene un error de formato JSON: {exc}") from exc
    users: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        try:
            users.append(
                {
                    "name": item["name"],
                    "identifier": item["identifier"],
                    "password": item["password"],
                    "role": item["role"],
                }
            )
        except KeyError as exc:
            raise SystemExit(f"{source}: falta el campo {exc} en la entrada #{index + 1}.") from exc
    return users


SEED_USERS = load_seed_users()

RUBRICS = [
    {
        "key": "problem_score",
        "label": "Identificación del problema",
        "weight": 0.20,
        "description": "Evalúa la claridad con la que se identifica y sustenta el problema, demostrando su relevancia y el impacto que genera en los usuarios.",
    },
    {
        "key": "value_score",
        "label": "Propuesta de valor",
        "weight": 0.25,
        "description": "Evalúa qué tan innovadora, diferenciadora y pertinente es la solución planteada para responder al problema identificado, el valor que aporta a los usuarios y su alineación con las megatendencias.",
    },
    {
        "key": "validation_score",
        "label": "Validación de la solución",
        "weight": 0.20,
        "description": "Evalúa la evidencia que respalda la propuesta, considerando pruebas realizadas, pilotos, entrevistas, retroalimentación de usuarios o resultados obtenidos.",
    },
    {
        "key": "business_score",
        "label": "Modelo de negocio",
        "weight": 0.20,
        "description": "Evalúa la claridad y viabilidad del modelo de negocio propuesto. Considera si la solución genera y captura valor, identifica clientes, fuentes de ingresos, recursos, actividades clave y su potencial de crecimiento.",
    },
    {
        "key": "pitch_score",
        "label": "Calidad del pitch",
        "weight": 0.15,
        "description": "Evalúa la claridad, organización y capacidad de comunicación durante la presentación, el manejo del tiempo, el dominio del tema y la respuesta a las preguntas del jurado.",
    },
]

# Rúbrica del Pitch 2: mismos criterios, con más peso al modelo de negocio.
RUBRICS_PITCH_TWO = [
    {
        "key": "problem_score",
        "label": "Identificación del problema",
        "weight": 0.15,
        "description": "Evalúa la claridad con la que se identifica y sustenta el problema, demostrando su relevancia y el impacto que genera en los usuarios.",
    },
    {
        "key": "value_score",
        "label": "Propuesta de valor",
        "weight": 0.15,
        "description": "Evalúa qué tan innovadora, diferenciadora y pertinente es la solución planteada para responder al problema identificado, así como el valor que aporta a los usuarios.",
    },
    {
        "key": "validation_score",
        "label": "Validación de la solución",
        "weight": 0.15,
        "description": "Evalúa la evidencia que respalda la propuesta, considerando pruebas realizadas, pilotos, entrevistas, retroalimentación de usuarios o resultados obtenidos.",
    },
    {
        "key": "business_score",
        "label": "Modelo de negocio",
        "weight": 0.40,
        "description": "Viabilidad, sostenibilidad y escalabilidad del modelo de negocio, incluyendo propuesta de ingresos, estrategia de implementación, análisis de costos, aliados clave y proyección de crecimiento e impacto a largo plazo.",
    },
    {
        "key": "pitch_score",
        "label": "Calidad del pitch",
        "weight": 0.15,
        "description": "Evalúa la claridad, organización y capacidad de comunicación durante la presentación, el manejo del tiempo, el dominio del tema y la respuesta a las preguntas del jurado.",
    },
]

RUBRIC_KEYS = [item["key"] for item in RUBRICS]


def get_rubrics(pitch_id: int) -> list[dict[str, Any]]:
    """Rúbrica (criterios, pesos y descripciones) de cada Pitch."""
    return RUBRICS_PITCH_TWO if pitch_id == PITCH_TWO else RUBRICS


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Falta la variable de entorno DATABASE_URL de Neon.")
    conn = sqlite3.connect(database_url)
    try:
        # Defensive: if the underlying connection is being reused/shared across
        # requests (as opposed to opened fresh each time), it may be sitting
        # inside a leftover transaction with a stale snapshot of the data.
        # Rolling back here is always safe (there should be no pending writes
        # of ours yet) and guarantees the next queries see current committed data.
        conn.rollback()
    except Exception:
        pass
    return conn


def pbkdf2_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations_int = int(iterations)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_b64.encode("ascii"))
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations_int)
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            identifier TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('admin', 'jury')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS teams (
            id BIGSERIAL PRIMARY KEY,
            display_order INTEGER NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            leader TEXT NOT NULL,
            country TEXT NOT NULL,
            university TEXT NOT NULL,
            filial TEXT,
            theme_line TEXT NOT NULL,
            source_row INTEGER,
            manual_position INTEGER,
            schedule_day TEXT,
            schedule_time TEXT,
            original_order INTEGER,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text)
        );

        CREATE TABLE IF NOT EXISTS evaluations (
            id BIGSERIAL PRIMARY KEY,
            pitch_id INTEGER NOT NULL DEFAULT 1,
            team_id BIGINT NOT NULL,
            juror_id BIGINT NOT NULL,
            problem_score DOUBLE PRECISION NOT NULL,
            value_score DOUBLE PRECISION NOT NULL,
            validation_score DOUBLE PRECISION NOT NULL,
            business_score DOUBLE PRECISION NOT NULL,
            pitch_score DOUBLE PRECISION NOT NULL,
            observations TEXT NOT NULL DEFAULT '',
            final_score_5 DOUBLE PRECISION NOT NULL,
            final_score_100 DOUBLE PRECISION NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            UNIQUE(pitch_id, team_id, juror_id),
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
            FOREIGN KEY(juror_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ranking_history (
            id BIGSERIAL PRIMARY KEY,
            pitch_id INTEGER NOT NULL DEFAULT 1,
            team_id BIGINT NOT NULL,
            evaluation_id BIGINT,
            position INTEGER NOT NULL,
            score_5 DOUBLE PRECISION NOT NULL,
            score_100 DOUBLE PRECISION NOT NULL,
            recorded_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
            FOREIGN KEY(evaluation_id) REFERENCES evaluations(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS no_shows (
            id BIGSERIAL PRIMARY KEY,
            pitch_id INTEGER NOT NULL DEFAULT 1,
            team_id BIGINT NOT NULL,
            juror_id BIGINT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            UNIQUE(pitch_id, team_id, juror_id),
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
            FOREIGN KEY(juror_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ai_improvements (
            id BIGSERIAL PRIMARY KEY,
            pitch_id INTEGER NOT NULL DEFAULT 1,
            team_id BIGINT NOT NULL,
            juror_id BIGINT NOT NULL,
            used_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            UNIQUE(pitch_id, team_id, juror_id),
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE,
            FOREIGN KEY(juror_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id BIGSERIAL PRIMARY KEY,
            token TEXT NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            last_seen_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP::text),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pitch_team_settings (
            pitch_id INTEGER NOT NULL,
            team_id BIGINT NOT NULL,
            sort_order INTEGER,
            deactivated INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (pitch_id, team_id),
            FOREIGN KEY(team_id) REFERENCES teams(id) ON DELETE CASCADE
        );
        """
    )
    # Columnas agregadas después del despliegue inicial: se aplican con
    # IF NOT EXISTS para que sea seguro correr esto sobre una base ya existente.
    conn.executescript(
        """
        ALTER TABLE teams ADD COLUMN IF NOT EXISTS schedule_day TEXT;
        ALTER TABLE teams ADD COLUMN IF NOT EXISTS schedule_time TEXT;
        ALTER TABLE teams ADD COLUMN IF NOT EXISTS original_order INTEGER;
        ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS pitch_id INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE ranking_history ADD COLUMN IF NOT EXISTS pitch_id INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE no_shows ADD COLUMN IF NOT EXISTS pitch_id INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE ai_improvements ADD COLUMN IF NOT EXISTS pitch_id INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE pitch_team_settings ADD COLUMN IF NOT EXISTS manual_position INTEGER;
        """
    )
    conn.execute("ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS evaluations_team_id_juror_id_key")
    conn.execute("ALTER TABLE no_shows DROP CONSTRAINT IF EXISTS no_shows_team_id_juror_id_key")
    conn.execute("ALTER TABLE ai_improvements DROP CONSTRAINT IF EXISTS ai_improvements_team_id_juror_id_key")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS evaluations_pitch_team_juror_key ON evaluations (pitch_id, team_id, juror_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS no_shows_pitch_team_juror_key ON no_shows (pitch_id, team_id, juror_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ai_improvements_pitch_team_juror_key ON ai_improvements (pitch_id, team_id, juror_id)")


def seed_users(conn: sqlite3.Connection) -> None:
    desired_identifiers = {user["identifier"].lower() for user in SEED_USERS}
    for row in conn.execute("SELECT id, identifier FROM users").fetchall():
        if row["identifier"].lower() not in desired_identifiers:
            conn.execute("DELETE FROM users WHERE id = ?", (row["id"],))
    for user in SEED_USERS:
        password_hash = pbkdf2_hash(user["password"])
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(identifier) = ?",
            (user["identifier"].lower(),),
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO users (name, identifier, password_hash, role, active) VALUES (?, ?, ?, ?, 1)",
                (user["name"], user["identifier"], password_hash, user["role"]),
            )
        else:
            conn.execute(
                "UPDATE users SET name = ?, identifier = ?, password_hash = ?, role = ?, active = 1 WHERE id = ?",
                (user["name"], user["identifier"], password_hash, user["role"], existing["id"]),
            )


def parse_teams_csv(csv_path: Path | None = None) -> list[dict[str, Any]]:
    """Lee el cronograma de equipos desde teams_schedule.csv (separado por ';').

    Formato esperado (una fila de encabezado, luego una fila por equipo,
    con el día repetido en cada fila):
    Dia;Nombre del equipo;Horario;Descripcion del proyecto;
    Nombre Completo participante lider;Pais;Universidad/Filial;Linea tematica

    Filas sin nombre de equipo, o con "RECESO" como nombre, se ignoran
    (son recesos del cronograma, no equipos). El orden de los equipos en
    el archivo se conserva tal cual (día 19 primero, luego día 20)."""
    path = csv_path or TEAMS_CSV_PATH
    if not path.exists():
        return []

    import csv as csv_module

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv_module.reader(handle, delimiter=";")
        rows = list(reader)

    if not rows:
        return []

    teams: list[dict[str, Any]] = []
    for source_row, row in enumerate(rows[1:], start=2):
        row = row + [""] * max(0, 8 - len(row))
        team_name = row[1].strip()
        if not team_name or team_name.upper() == "RECESO":
            continue
        teams.append(
            {
                "display_order": len(teams) + 1,
                "name": team_name,
                "schedule_time": row[2].strip(),
                "description": row[3].strip(),
                "leader": row[4].strip(),
                "country": row[5].strip(),
                "university": row[6].strip(),
                "filial": "",
                "theme_line": row[7].strip(),
                "source_row": source_row,
                "schedule_day": row[0].strip(),
            }
        )
    return teams


def seed_teams(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]:
        return
    replace_all_teams(conn, parse_teams_csv())


def replace_all_teams(conn: sqlite3.Connection, teams: list[dict[str, Any]]) -> None:
    """Borra todos los equipos existentes (y todo lo que dependa de ellos:
    evaluaciones, inasistencias, historial de ranking y mejoras de IA) y
    los reemplaza por la lista dada, en el orden recibido. Se usa tanto
    para el primer arranque como para recargar el cronograma desde cero."""
    conn.execute("DELETE FROM ranking_history")
    conn.execute("DELETE FROM ai_improvements")
    conn.execute("DELETE FROM no_shows")
    conn.execute("DELETE FROM evaluations")
    conn.execute("DELETE FROM pitch_team_settings")
    conn.execute("DELETE FROM teams")
    for team in teams:
        conn.execute(
            """
            INSERT INTO teams (
                display_order, name, description, leader, country, university,
                filial, theme_line, source_row, schedule_day, schedule_time, original_order
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                team["display_order"],
                team["name"],
                team["description"],
                team["leader"],
                team["country"],
                team["university"],
                team["filial"],
                team["theme_line"],
                team["source_row"],
                team["schedule_day"],
                team["schedule_time"],
                team["display_order"],
            ),
        )


def average(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def request_cache(conn: sqlite3.Connection) -> dict[Any, Any]:
    """Caché de lecturas válida solo durante el request actual. pg_compat la
    vacía ante cualquier escritura, así que nunca devuelve datos viejos. Evita
    repetir las mismas consultas (cada una es un viaje de ida y vuelta a Neon)."""
    cache = getattr(conn, "cache", None)
    return cache if cache is not None else {}


def get_all_teams(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cache = request_cache(conn)
    if "teams" not in cache:
        cache["teams"] = conn.execute("SELECT * FROM teams ORDER BY display_order ASC, id ASC").fetchall()
    return cache["teams"]


def compute_team_stats(conn: sqlite3.Connection, pitch_id: int = PITCH_ONE, team_ids: set[int] | None = None) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], int]:
    cache = request_cache(conn)
    cache_key = ("team_stats", pitch_id, frozenset(team_ids) if team_ids is not None else None)
    if cache_key in cache:
        return cache[cache_key]
    result = _compute_team_stats(conn, pitch_id, team_ids)
    cache[cache_key] = result
    return result


def _compute_team_stats(conn: sqlite3.Connection, pitch_id: int, team_ids: set[int] | None) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], dict[int, list[dict[str, Any]]], int]:
    teams = get_all_teams(conn)
    if team_ids is not None:
        teams = [team for team in teams if team["id"] in team_ids]
    users = conn.execute("SELECT * FROM users WHERE active = 1").fetchall()
    jurors = [user for user in users if user["role"] == ROLE_JURY]
    eval_rows = conn.execute(
        """
        SELECT e.*, u.name AS juror_name, u.identifier AS juror_identifier
        FROM evaluations e
        INNER JOIN users u ON u.id = e.juror_id
        WHERE e.pitch_id = ?
        ORDER BY e.updated_at DESC, e.id DESC
        """
        , (pitch_id,)
    ).fetchall()

    settings = get_pitch_team_settings(conn, pitch_id)
    no_show_rows = conn.execute("SELECT team_id, COUNT(*) AS cnt FROM no_shows WHERE pitch_id = ? GROUP BY team_id", (pitch_id,)).fetchall()
    no_show_counts: dict[int, int] = {row["team_id"]: row["cnt"] for row in no_show_rows}

    evaluations_by_team: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for row in eval_rows:
        evaluations_by_team[row["team_id"]].append(row)
    active_juror_ids = {juror["id"] for juror in jurors}

    teams_stats: list[dict[str, Any]] = []
    stats_by_id: dict[int, dict[str, Any]] = {}
    evaluations_by_team_serialized: dict[int, list[dict[str, Any]]] = {}

    for team in teams:
        team_evaluations = evaluations_by_team.get(team["id"], [])
        rubric_averages = {
            rubric["key"]: average([float(item[rubric["key"]]) for item in team_evaluations])
            for rubric in RUBRICS
        }
        final_5_values = [float(item["final_score_5"]) for item in team_evaluations]
        final_100_values = [float(item["final_score_100"]) for item in team_evaluations]
        average_5 = average(final_5_values)
        average_100 = average(final_100_values)
        evaluation_count = len(team_evaluations)
        # El estado se mide contra los jurados ACTIVOS: si el admin desactiva
        # a un jurado que no asistió, ya no se espera su calificación. Sus
        # calificaciones ya guardadas siguen contando en los promedios.
        active_evaluation_count = len([item for item in team_evaluations if item["juror_id"] in active_juror_ids])
        no_show_count = no_show_counts.get(team["id"], 0)
        deactivated = bool(settings.get(team["id"], {}).get("deactivated"))
        if deactivated:
            status = "Desactivado"
        elif evaluation_count == 0 and jurors and no_show_count >= len(jurors):
            status = "No asistió"
        elif evaluation_count == 0:
            status = "Pendiente"
        elif active_evaluation_count < len(jurors):
            status = "En progreso"
        else:
            status = "Evaluado"

        serialized_evaluations = [
            {
                "id": item["id"],
                "juror_id": item["juror_id"],
                "juror_name": item["juror_name"],
                "juror_identifier": item["juror_identifier"],
                "problem_score": item["problem_score"],
                "value_score": item["value_score"],
                "validation_score": item["validation_score"],
                "business_score": item["business_score"],
                "pitch_score": item["pitch_score"],
                "observations": item["observations"],
                "final_score_5": round(float(item["final_score_5"]), 4),
                "final_score_100": round(float(item["final_score_100"]), 4),
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
            for item in team_evaluations
        ]

        stats = {
            "id": team["id"],
            "display_order": team["display_order"],
            "name": team["name"],
            "description": team["description"],
            "leader": team["leader"],
            "country": team["country"],
            "university": team["university"],
            "filial": team["filial"] or "",
            "theme_line": team["theme_line"],
            "source_row": team["source_row"],
            "schedule_day": team["schedule_day"],
            "schedule_time": team["schedule_time"],
            # Pitch 1: deliberación guardada en teams (como siempre).
            # Otros Pitch: deliberación propia en pitch_team_settings.
            "manual_position": (
                team["manual_position"]
                if pitch_id == PITCH_ONE
                else settings.get(team["id"], {}).get("manual_position")
            ),
            "evaluation_count": evaluation_count,
            "active_evaluation_count": active_evaluation_count,
            "no_show_count": no_show_count,
            "deactivated": deactivated,
            "average_5": round(average_5, 4),
            "average_100": round(average_100, 4),
            "rubric_averages": {key: round(value, 4) for key, value in rubric_averages.items()},
            "status": status,
            "evaluations": serialized_evaluations,
        }
        teams_stats.append(stats)
        stats_by_id[team["id"]] = stats
        evaluations_by_team_serialized[team["id"]] = serialized_evaluations

    def ranking_sort_key(item: dict[str, Any]) -> tuple:
        has_manual = item["manual_position"] is not None
        return (
            # Los equipos desactivados por el admin siempre quedan al final.
            1 if item["deactivated"] else 0,
            0 if has_manual else 1,
            item["manual_position"] if has_manual else 0,
            -item["average_5"],
            -item["evaluation_count"],
            item["display_order"],
            item["name"].lower(),
        )

    ranking = sorted(teams_stats, key=ranking_sort_key)
    for index, item in enumerate(ranking, start=1):
        item["position"] = index
        stats_by_id[item["id"]]["position"] = index

    return ranking, stats_by_id, evaluations_by_team_serialized, len(jurors)


def record_ranking_history(conn: sqlite3.Connection, pitch_id: int = PITCH_ONE, evaluation_id: int | None = None, team_ids: set[int] | None = None) -> None:
    ranking, _, _, _ = compute_team_stats(conn, pitch_id, team_ids)
    if not ranking:
        return
    # Un solo INSERT para todos los equipos en vez de uno por equipo: cada
    # consulta es un viaje de ida y vuelta a Neon.
    conn.execute(
        """
        INSERT INTO ranking_history (pitch_id, team_id, evaluation_id, position, score_5, score_100, recorded_at)
        SELECT ?, t.team_id, CAST(? AS BIGINT), t.position, t.score_5, t.score_100, ?
        FROM unnest(?::bigint[], ?::integer[], ?::double precision[], ?::double precision[])
            AS t(team_id, position, score_5, score_100)
        """,
        (
            pitch_id,
            evaluation_id,
            iso_now(),
            [item["id"] for item in ranking],
            [item["position"] for item in ranking],
            [item["average_5"] for item in ranking],
            [item["average_100"] for item in ranking],
        ),
    )


def seed_initial_history(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT COUNT(*) FROM ranking_history").fetchone()[0]:
        return
    record_ranking_history(conn, PITCH_ONE, None)


def initialize_database() -> None:
    # Neon is persistent, so initialization must be idempotent and must never
    # delete existing production data on a cold start.
    with db_connect() as conn:
        ensure_schema(conn)
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            seed_users(conn)
        if conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0:
            seed_teams(conn)
        if conn.execute("SELECT COUNT(*) FROM ranking_history").fetchone()[0] == 0:
            seed_initial_history(conn)
        conn.commit()


def parse_cookie(header_value: str | None) -> SimpleCookie:
    cookie = SimpleCookie()
    if header_value:
        cookie.load(header_value)
    return cookie


def get_session_user(conn: sqlite3.Connection, headers: dict[str, str]) -> sqlite3.Row | None:
    cookie = parse_cookie(headers.get("Cookie"))
    morsel = cookie.get(SESSION_COOKIE)
    if morsel is None:
        return None
    token = morsel.value
    row = conn.execute(
        """
        SELECT u.*
        FROM sessions s
        INNER JOIN users u ON u.id = s.user_id
        WHERE s.token = ? AND s.expires_at > ? AND u.active = 1
        """,
        (token, iso_now()),
    ).fetchone()
    if row is None:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None
    conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (iso_now(), token))
    conn.commit()
    return row


def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (utc_now() + timedelta(hours=SESSION_TTL_HOURS)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions (token, user_id, expires_at, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
        (token, user_id, expires_at, iso_now(), iso_now()),
    )
    conn.commit()
    return token


def clear_session(conn: sqlite3.Connection, token: str | None) -> None:
    if not token:
        return
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()


def load_json(request: SimpleHTTPRequestHandler) -> dict[str, Any]:
    length = int(request.headers.get("Content-Length", "0") or 0)
    if length <= 0:
        return {}
    raw = request.rfile.read(length)
    if not raw:
        return {}
    for encoding in ("utf-8", "utf-8-sig", "utf-16-le", "utf-16"):
        try:
            return json.loads(raw.decode(encoding))
        except UnicodeDecodeError:
            continue
        except json.JSONDecodeError:
            continue
    raise ValueError("El cuerpo de la solicitud no es válido.")


def send_json(handler: SimpleHTTPRequestHandler, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, extra_headers: dict[str, str] | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if extra_headers:
        for key, value in extra_headers.items():
            handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def send_error_json(handler: SimpleHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
    send_json(handler, {"ok": False, "error": message}, status=status)


def require_user(handler: "InnovatePitchHandler", role: str | None = None) -> sqlite3.Row | None:
    with db_connect() as conn:
        user = get_session_user(conn, handler.headers)
        if user is None:
            send_error_json(handler, HTTPStatus.UNAUTHORIZED, "Sesión no iniciada.")
            return None
        if role and user["role"] != role:
            send_error_json(handler, HTTPStatus.FORBIDDEN, "No tiene permisos para acceder a este recurso.")
            return None
        return user


def serialize_user(user: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": user["id"],
        "name": user["name"],
        "identifier": user["identifier"],
        "role": user["role"],
        "active": bool(user["active"]),
        "created_at": user["created_at"],
    }


def parse_pitch_id(params: dict[str, list[str]]) -> int:
    value = params.get("pitch", [str(PITCH_ONE)])[0]
    return int(value) if value in {str(PITCH_ONE), str(PITCH_TWO)} else PITCH_ONE


def serialize_team(team: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": team["id"],
        "display_order": team["display_order"],
        "name": team["name"],
        "description": team["description"],
        "leader": team["leader"],
        "country": team["country"],
        "university": team["university"],
        "filial": team["filial"],
        "theme_line": team["theme_line"],
        "source_row": team["source_row"],
        "schedule_day": team.get("schedule_day"),
        "schedule_time": team.get("schedule_time"),
        "manual_position": team.get("manual_position"),
        "evaluation_count": team["evaluation_count"],
        "active_evaluation_count": team.get("active_evaluation_count", team["evaluation_count"]),
        "no_show_count": team.get("no_show_count", 0),
        "deactivated": team.get("deactivated", False),
        "average_5": team["average_5"],
        "average_100": team["average_100"],
        "rubric_averages": team["rubric_averages"],
        "status": team["status"],
        "position": team.get("position"),
    }


def get_team_detail(conn: sqlite3.Connection, team_id: int, pitch_id: int = PITCH_ONE, team_ids: set[int] | None = None) -> dict[str, Any] | None:
    ranking, stats_by_id, _, juror_count = compute_team_stats(conn, pitch_id, team_ids)
    team = stats_by_id.get(team_id)
    if team is None:
        return None
    history_rows = conn.execute(
        """
        SELECT rh.position, rh.score_5, rh.score_100, rh.recorded_at, e.updated_at AS evaluation_updated_at
        FROM ranking_history rh
        LEFT JOIN evaluations e ON e.id = rh.evaluation_id
        WHERE rh.team_id = ? AND rh.pitch_id = ?
        ORDER BY rh.recorded_at ASC, rh.id ASC
        """,
        (team_id, pitch_id),
    ).fetchall()
    team_history = [
        {
            "position": row["position"],
            "score_5": round(float(row["score_5"]), 4),
            "score_100": round(float(row["score_100"]), 4),
            "recorded_at": row["recorded_at"],
            "evaluation_updated_at": row["evaluation_updated_at"],
        }
        for row in history_rows
    ]
    juror_progress = {
        "total_jurors": juror_count,
        "evaluations_count": team["evaluation_count"],
        "pending_count": max(juror_count - team["active_evaluation_count"], 0),
        "status": team["status"],
    }
    evaluations = team["evaluations"]
    return {
        "team": serialize_team(team),
        "history": team_history,
        "juror_progress": juror_progress,
        "evaluations": evaluations,
        "ranking": ranking,
    }


def get_evaluated_team_ids(conn: sqlite3.Connection, juror_id: int, pitch_id: int = PITCH_ONE) -> set[int]:
    rows = conn.execute("SELECT team_id FROM evaluations WHERE juror_id = ? AND pitch_id = ?", (juror_id, pitch_id)).fetchall()
    return {row["team_id"] for row in rows}


def get_pitch_team_settings(conn: sqlite3.Connection, pitch_id: int) -> dict[int, dict[str, Any]]:
    """Orden de presentación y estado (activo/desactivado) que el admin
    configuró para cada equipo dentro de un Pitch."""
    cache = request_cache(conn)
    cache_key = ("pitch_settings", pitch_id)
    if cache_key not in cache:
        rows = conn.execute(
            "SELECT team_id, sort_order, deactivated, manual_position FROM pitch_team_settings WHERE pitch_id = ?",
            (pitch_id,),
        ).fetchall()
        cache[cache_key] = {
            row["team_id"]: {
                "sort_order": row["sort_order"],
                "deactivated": bool(row["deactivated"]),
                "manual_position": row["manual_position"],
            }
            for row in rows
        }
    return cache[cache_key]


def get_pitch_presentation_rows(
    conn: sqlite3.Connection,
    pitch_id: int,
    team_ids: set[int] | None,
    include_deactivated: bool = False,
) -> list[sqlite3.Row]:
    """Equipos del Pitch en el orden en que los ven los jurados. Los equipos
    sin orden manual van después de los ordenados, según display_order."""
    rows = get_all_teams(conn)
    if team_ids is not None:
        rows = [row for row in rows if row["id"] in team_ids]
    settings = get_pitch_team_settings(conn, pitch_id)
    if not include_deactivated:
        rows = [row for row in rows if not settings.get(row["id"], {}).get("deactivated")]

    def sort_key(row: sqlite3.Row) -> tuple:
        sort_order = settings.get(row["id"], {}).get("sort_order")
        return (sort_order is None, sort_order or 0, row["display_order"], row["id"])

    return sorted(rows, key=sort_key)


def save_pitch_presentation_order(conn: sqlite3.Connection, pitch_id: int, ordered_ids: list[int]) -> None:
    if not ordered_ids:
        return
    conn.execute(
        "INSERT INTO pitch_team_settings (pitch_id, team_id, sort_order) "
        "SELECT ?, t.team_id, t.sort_order FROM unnest(?::bigint[], ?::integer[]) AS t(team_id, sort_order) "
        "ON CONFLICT (pitch_id, team_id) DO UPDATE SET sort_order = EXCLUDED.sort_order",
        (pitch_id, ordered_ids, list(range(1, len(ordered_ids) + 1))),
    )


def set_pitch_team_deactivated(conn: sqlite3.Connection, pitch_id: int, team_id: int, deactivated: bool) -> None:
    conn.execute(
        "INSERT INTO pitch_team_settings (pitch_id, team_id, deactivated) VALUES (?, ?, ?) "
        "ON CONFLICT (pitch_id, team_id) DO UPDATE SET deactivated = EXCLUDED.deactivated",
        (pitch_id, team_id, 1 if deactivated else 0),
    )


def _call_gemini_once(api_key: str, original_text: str) -> str:
    """One attempt at the Gemini call. Raises _GeminiRetryable for transient
    errors (429/503) so the caller can retry, or ValueError for anything
    else that shouldn't be retried."""
    body = json.dumps(
        {
            "system_instruction": {"parts": [{"text": GEMINI_SYSTEM_INSTRUCTION}]},
            "contents": [{"parts": [{"text": original_text}]}],
            "generationConfig": {"temperature": 0.3},
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{GEMINI_API_URL}?key={api_key}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        print(f"[Gemini API error] HTTP {exc.code} calling model '{GEMINI_MODEL}': {detail}")
        if exc.code == 404:
            raise ValueError(
                f"La IA no pudo procesar el texto (error 404: el modelo '{GEMINI_MODEL}' no está "
                "disponible). Es posible que Google haya retirado este modelo; revisa GEMINI_MODEL "
                "en server.py."
            ) from exc
        if exc.code in (429, 503):
            raise _GeminiRetryable(exc.code) from exc
        raise ValueError(f"La IA no pudo procesar el texto (error {exc.code}). Intenta de nuevo.") from exc
    except urllib.error.URLError as exc:
        raise ValueError("No se pudo conectar con el servicio de IA. Intenta de nuevo.") from exc
    except (TimeoutError, OSError) as exc:
        raise ValueError("El servicio de IA tardó demasiado en responder. Intenta de nuevo.") from exc

    try:
        candidates = payload.get("candidates") or []
        parts = candidates[0]["content"]["parts"]
        improved = "".join(part.get("text", "") for part in parts).strip()
    except (KeyError, IndexError, TypeError):
        improved = ""

    if not improved:
        raise ValueError("La IA no devolvió ningún texto. Intenta de nuevo.")
    return improved


class _GeminiRetryable(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"Gemini transient error {status_code}")
        self.status_code = status_code


def improve_text_with_gemini(original_text: str) -> str:
    """Send the juror's observation text to Gemini for a light grammar/tone
    pass. Retries automatically (short backoff) on transient 429/503
    "overloaded" responses, which are common on the free tier. Raises
    ValueError with a user-facing message on any non-recoverable failure."""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("La mejora con IA no está configurada en el servidor (falta GEMINI_API_KEY).")

    delays = [1, 2]  # seconds between attempts (2 retries after the first try)
    last_status = None
    for attempt, delay in enumerate([0] + delays):
        if delay:
            time.sleep(delay)
        try:
            return _call_gemini_once(api_key, original_text)
        except _GeminiRetryable as exc:
            last_status = exc.status_code
            continue

    raise ValueError(
        f"La IA está saturada en este momento (error {last_status}). Espera unos segundos e intenta de nuevo."
    )


def get_next_pending_team_id(
    conn: sqlite3.Connection,
    juror_id: int,
    team_rows: list[sqlite3.Row],
    after_team_id: int | None = None,
    pitch_id: int = PITCH_ONE,
) -> int | None:
    """Return the next team (in the admin's presentation order) this juror
    should evaluate, skipping teams the juror has already evaluated."""
    if not team_rows:
        return None
    evaluated_ids = get_evaluated_team_ids(conn, juror_id, pitch_id)
    ids = [row["id"] for row in team_rows]

    start_index = 0
    if after_team_id is not None and after_team_id in ids:
        start_index = ids.index(after_team_id) + 1
    ordered_indices = list(range(start_index, len(ids))) + list(range(0, start_index))

    for idx in ordered_indices:
        candidate = ids[idx]
        if candidate in evaluated_ids:
            continue
        return candidate
    return None


def get_pitch_team_ids(conn: sqlite3.Connection, pitch_id: int) -> set[int] | None:
    if pitch_id == PITCH_ONE:
        return None
    if pitch_id == PITCH_TWO:
        ranking, _, _, _ = compute_team_stats(conn, PITCH_ONE)
        return {team["id"] for team in ranking[:WINNERS_COUNT]}
    return set()


def winners_count_for_pitch(pitch_id: int) -> int:
    """Cuántos equipos clasifican en cada Pitch: del Pitch 1 pasan 20 al
    Pitch 2, y del Pitch 2 salen los 10 finalistas."""
    return FINALISTS_COUNT if pitch_id == PITCH_TWO else WINNERS_COUNT


def get_active_pitch(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT value FROM app_settings WHERE key = ?", (ACTIVE_PITCH_SETTING_KEY,)
    ).fetchone()
    if row is None:
        return PITCH_ONE
    try:
        value = int(row["value"])
    except (TypeError, ValueError):
        return PITCH_ONE
    return value if value in (PITCH_ONE, PITCH_TWO, PITCH_THREE) else PITCH_ONE


def set_active_pitch(conn: sqlite3.Connection, pitch_id: int) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        (ACTIVE_PITCH_SETTING_KEY, str(pitch_id)),
    )


def get_jury_dashboard(conn: sqlite3.Connection, juror_id: int, team_id: int | None = None, pitch_id: int = PITCH_ONE) -> dict[str, Any]:
    team_ids = get_pitch_team_ids(conn, pitch_id)
    ranking, stats_by_id, _, juror_count = compute_team_stats(conn, pitch_id, team_ids)
    team_rows = get_pitch_presentation_rows(conn, pitch_id, team_ids)
    team_summary = [serialize_team(stats_by_id[row["id"]]) for row in team_rows]
    if team_id is not None and team_id not in {row["id"] for row in team_rows}:
        team_id = None
    if team_id is None:
        team_id = get_next_pending_team_id(conn, juror_id, team_rows, pitch_id=pitch_id)
    team_detail = get_team_detail(conn, team_id, pitch_id, team_ids) if team_id is not None else None
    current_eval = None
    if team_id is not None:
        current_eval_row = conn.execute(
            """
            SELECT *
            FROM evaluations
            WHERE team_id = ? AND juror_id = ? AND pitch_id = ?
            """,
            (team_id, juror_id, pitch_id),
        ).fetchone()
        if current_eval_row is not None:
            current_eval = {
                "id": current_eval_row["id"],
                "team_id": current_eval_row["team_id"],
                "juror_id": current_eval_row["juror_id"],
                "problem_score": current_eval_row["problem_score"],
                "value_score": current_eval_row["value_score"],
                "validation_score": current_eval_row["validation_score"],
                "business_score": current_eval_row["business_score"],
                "pitch_score": current_eval_row["pitch_score"],
                "observations": current_eval_row["observations"],
                "final_score_5": round(float(current_eval_row["final_score_5"]), 4),
                "final_score_100": round(float(current_eval_row["final_score_100"]), 4),
                "created_at": current_eval_row["created_at"],
                "updated_at": current_eval_row["updated_at"],
            }
    visible_ids = {row["id"] for row in team_rows}
    completed_count = len(get_evaluated_team_ids(conn, juror_id, pitch_id) & visible_ids)
    current_index = 0
    if team_id is not None:
        for index, row in enumerate(team_rows):
            if row["id"] == team_id:
                current_index = index
                break
    return {
        "teams": team_summary,
        "current_team_id": team_id,
        "current_index": current_index,
        "current_team": team_detail["team"] if team_detail else None,
        "current_team_detail": team_detail,
        "current_evaluation": current_eval,
        "progress": {
            "assigned": len(team_rows),
            "completed": completed_count,
            "pending": max(len(team_rows) - completed_count, 0),
            "percent": round((completed_count / len(team_rows) * 100) if team_rows else 0, 1),
        },
        "ranking": ranking,
        "juror_count": juror_count,
        "pitch_id": pitch_id,
        "rubrics": get_rubrics(pitch_id),
    }


def pdf_escape(text: str) -> bytes:
    """Escape a string for use inside a PDF literal string, encoding with
    cp1252 (practically identical to WinAnsiEncoding, which covers Spanish
    accented characters) so Helvetica can render it without embedding fonts."""
    encoded = text.encode("cp1252", errors="replace")
    result = bytearray()
    for byte in encoded:
        ch = bytes([byte])
        if ch in (b"(", b")", b"\\"):
            result.extend(b"\\" + ch)
        else:
            result.extend(ch)
    return bytes(result)


def pdf_wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if word == "":
            continue
        candidate = (current + " " + word).strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            while len(word) > max_chars:
                lines.append(word[:max_chars])
                word = word[max_chars:]
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def build_all_teams_observations_pdf(teams: list[dict[str, Any]]) -> bytes:
    """Build a single multi-page PDF (pure stdlib, no third-party deps) that
    lists, for every team, only its name and each juror's observations.
    Each team starts on its own page so the document reads as one section
    per team."""
    page_width, page_height = 612, 792
    margin_left = margin_right = margin_top = margin_bottom = 56
    usable_width = page_width - margin_left - margin_right

    title_size = 18
    heading_size = 12
    body_size = 10.5
    leading = 14.5
    heading_leading = 18

    body_max_chars = max(int(usable_width / (body_size * 0.5)), 20)

    # Each instruction: (text, font, size, leading, force_page_break_before)
    instructions: list[tuple[str, str, float, float, bool]] = []

    for team_index, team in enumerate(teams):
        team_name = team.get("name") or "Equipo sin nombre"
        evaluations = team.get("evaluations") or []
        instructions.append((team_name, "F2", title_size, title_size + 10, team_index > 0))
        instructions.append(("Observaciones de los jurados", "F1", 11, 24, False))

        if not evaluations:
            instructions.append(("Aun no hay observaciones registradas para este equipo.", "F1", body_size, leading, False))
        else:
            for index, item in enumerate(evaluations):
                juror_name = item.get("juror_name") or "Jurado"
                observations = str(item.get("observations") or "").strip()
                gap = heading_leading if index == 0 else heading_leading + 8
                instructions.append((f"Jurado: {juror_name}", "F2", heading_size, gap, False))
                if not observations:
                    instructions.append(("(Sin observaciones)", "F1", body_size, leading, False))
                    continue
                for paragraph in observations.replace("\r\n", "\n").split("\n"):
                    if paragraph.strip() == "":
                        instructions.append(("", "F1", body_size, leading, False))
                        continue
                    for line in pdf_wrap_text(paragraph, body_max_chars):
                        instructions.append((line, "F1", body_size, leading, False))

    if not instructions:
        instructions.append(("No hay equipos cargados.", "F1", body_size, leading, False))

    pages: list[list[tuple[str, str, float, float]]] = []
    current_page: list[tuple[str, str, float, float]] = []
    y = page_height - margin_top
    for text, font, size, entry_leading, force_break in instructions:
        needs_break = force_break and current_page
        fits = y - entry_leading >= margin_bottom
        if needs_break or not fits:
            pages.append(current_page)
            current_page = []
            y = page_height - margin_top
        current_page.append((text, font, size, entry_leading))
        y -= entry_leading
    pages.append(current_page)

    content_streams: list[bytes] = []
    for page_entries in pages:
        parts = [b"BT"]
        y = page_height - margin_top
        current_font, current_size = None, None
        for text, font, size, entry_leading in page_entries:
            if font != current_font or size != current_size:
                parts.append(f"/{font} {size:.1f} Tf".encode("ascii"))
                current_font, current_size = font, size
            parts.append(f"1 0 0 1 {margin_left} {y:.2f} Tm".encode("ascii"))
            parts.append(b"(" + pdf_escape(text) + b") Tj")
            y -= entry_leading
        parts.append(b"ET")
        content_streams.append(b"\n".join(parts))

    objects: list[bytes] = []

    def add_object(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add_object(b"")  # 1: Catalog placeholder
    add_object(b"")  # 2: Pages placeholder
    font_regular_num = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    font_bold_num = add_object(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")

    page_obj_nums: list[tuple[int, int]] = []
    for stream in content_streams:
        content_num = add_object(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")
        page_num = add_object(b"")
        page_obj_nums.append((page_num, content_num))

    kids = " ".join(f"{num} 0 R" for num, _ in page_obj_nums)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_nums)} >>".encode("ascii")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    for page_num, content_num in page_obj_nums:
        objects[page_num - 1] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 {font_regular_num} 0 R /F2 {font_bold_num} 0 R >> >> "
            f"/Contents {content_num} 0 R >>"
        ).encode("ascii")

    buffer = bytearray()
    buffer += b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets = [0] * (len(objects) + 1)
    for idx, body in enumerate(objects, start=1):
        offsets[idx] = len(buffer)
        buffer += f"{idx} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(buffer)
    buffer += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    buffer += b"0000000000 65535 f \n"
    for idx in range(1, len(objects) + 1):
        buffer += f"{offsets[idx]:010d} 00000 n \n".encode("ascii")
    buffer += b"trailer\n" + f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii")
    buffer += b"startxref\n" + f"{xref_offset}\n".encode("ascii") + b"%%EOF"

    return bytes(buffer)


def safe_pdf_filename(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in normalized)
    cleaned = "_".join(filter(None, cleaned.split("_")))
    return (cleaned or "equipo").strip("_")


def xlsx_col_letter(idx: int) -> str:
    letters = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def build_xlsx(headers: list[str], rows: list[list[Any]], sheet_name: str = "Hoja1") -> bytes:
    """Build a minimal but valid .xlsx workbook using only the standard
    library (zipfile + hand-written XML), matching this project's existing
    no-third-party-dependencies approach."""
    sheet_name = (sheet_name or "Hoja1")[:31]

    def cell_xml(col_idx: int, row_idx: int, value: Any, is_header: bool) -> str:
        ref = f"{xlsx_col_letter(col_idx)}{row_idx}"
        style_attr = ' s="1"' if is_header else ""
        if isinstance(value, bool):
            value = str(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
        text = "" if value is None else str(value)
        text = xml_escape(text)
        return f'<c r="{ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    row_parts = []
    row_idx = 1
    header_cells = "".join(cell_xml(i + 1, row_idx, h, True) for i, h in enumerate(headers))
    row_parts.append(f'<row r="{row_idx}">{header_cells}</row>')
    for row in rows:
        row_idx += 1
        cells = "".join(cell_xml(i + 1, row_idx, v, False) for i, v in enumerate(row))
        row_parts.append(f'<row r="{row_idx}">{cells}</row>')
    sheet_data = "".join(row_parts)

    col_widths = "".join(
        f'<col min="{i + 1}" max="{i + 1}" width="26" customWidth="1"/>' for i in range(len(headers))
    )

    sheet1_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<cols>{col_widths}</cols>'
        f'<sheetData>{sheet_data}</sheetData>'
        '</worksheet>'
    )

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        '</Relationships>'
    )

    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF117A43"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="2">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet1_xml)
    return buffer.getvalue()


def send_xlsx(handler: SimpleHTTPRequestHandler, data: bytes, filename: str) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def send_pdf(handler: SimpleHTTPRequestHandler, data: bytes, filename: str) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)


def get_admin_dashboard(conn: sqlite3.Connection, selected_team_id: int | None = None, pitch_id: int = PITCH_ONE) -> dict[str, Any]:
    team_ids = get_pitch_team_ids(conn, pitch_id)
    ranking, stats_by_id, _, juror_count = compute_team_stats(conn, pitch_id, team_ids)
    if selected_team_id is None and ranking:
        selected_team_id = ranking[0]["id"]
    team_detail = get_team_detail(conn, selected_team_id, pitch_id, team_ids) if selected_team_id is not None else None
    jurors = [serialize_user(user) for user in conn.execute("SELECT * FROM users WHERE role = ? ORDER BY name ASC", (ROLE_JURY,)).fetchall()]
    counts_by_juror: dict[int, dict[str, int]] = defaultdict(dict)
    for row in conn.execute("SELECT juror_id, pitch_id, COUNT(*) AS cnt FROM evaluations GROUP BY juror_id, pitch_id").fetchall():
        counts_by_juror[row["juror_id"]][str(row["pitch_id"])] = row["cnt"]
    for juror in jurors:
        juror["evaluations_by_pitch"] = counts_by_juror.get(juror["id"], {})
    admins = [serialize_user(user) for user in conn.execute("SELECT * FROM users WHERE role = ? ORDER BY name ASC", (ROLE_ADMIN,)).fetchall()]
    presentation_rows = get_pitch_presentation_rows(conn, pitch_id, team_ids, include_deactivated=True)
    presentation = [
        {
            "id": row["id"],
            "name": row["name"],
            "schedule_day": row["schedule_day"],
            "schedule_time": row["schedule_time"],
            "evaluation_count": stats_by_id[row["id"]]["evaluation_count"],
            "active_evaluation_count": stats_by_id[row["id"]]["active_evaluation_count"],
            "status": stats_by_id[row["id"]]["status"],
            "deactivated": stats_by_id[row["id"]]["deactivated"],
        }
        for row in presentation_rows
    ]
    active_ranking = [item for item in ranking if not item["deactivated"]]
    return {
        "ranking": ranking,
        "selected_team": team_detail,
        "jurors": jurors,
        "admins": admins,
        "presentation": presentation,
        "summary": {
            "teams": len(active_ranking),
            "evaluated": len([item for item in active_ranking if item["evaluation_count"] > 0]),
            "pending": len([item for item in active_ranking if item["evaluation_count"] == 0]),
            "jurors": juror_count,
        },
        "pitch_id": pitch_id,
        "active_pitch": get_active_pitch(conn),
        "winners_count": winners_count_for_pitch(pitch_id),
        "rubrics": get_rubrics(pitch_id),
    }


def validate_scores(payload: dict[str, Any]) -> tuple[dict[str, float], str | None]:
    scores: dict[str, float] = {}
    for rubric in RUBRIC_KEYS:
        value = payload.get(rubric)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return {}, f"La rúbrica {rubric} debe ser un número entre 1.0 y 5.0 en pasos de 0.5."
        value = float(value)
        doubled = value * 2
        rounded_doubled = round(doubled)
        if abs(doubled - rounded_doubled) > 1e-6 or rounded_doubled < 2 or rounded_doubled > 10:
            return {}, f"La rúbrica {rubric} debe ser un valor entre 1.0 y 5.0 en pasos de 0.5 (1.0, 1.5, 2.0 ... 5.0)."
        scores[rubric] = rounded_doubled / 2
    return scores, None


def store_evaluation(conn: sqlite3.Connection, team_id: int, juror_id: int, payload: dict[str, Any], pitch_id: int = PITCH_ONE) -> dict[str, Any]:
    scores, error = validate_scores(payload)
    if error:
        raise ValueError(error)
    observations = str(payload.get("observations", "")).strip()
    if not observations:
        raise ValueError("Debe escribir observaciones antes de guardar la evaluación.")
    # Pesos de la rúbrica del Pitch que se está calificando (el Pitch 1
    # conserva sus pesos originales: 20/25/20/20/15).
    weights = {rubric["key"]: rubric["weight"] for rubric in get_rubrics(pitch_id)}
    final_score_5 = round(sum(scores[key] * weights[key] for key in RUBRIC_KEYS), 4)
    final_score_100 = round(final_score_5 * 20, 4)
    timestamp = iso_now()
    existing = conn.execute(
        "SELECT id, created_at FROM evaluations WHERE team_id = ? AND juror_id = ? AND pitch_id = ?",
        (team_id, juror_id, pitch_id),
    ).fetchone()
    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO evaluations (
                pitch_id, team_id, juror_id, problem_score, value_score, validation_score, business_score,
                pitch_score, observations, final_score_5, final_score_100, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pitch_id,
                team_id,
                juror_id,
                scores["problem_score"],
                scores["value_score"],
                scores["validation_score"],
                scores["business_score"],
                scores["pitch_score"],
                observations,
                final_score_5,
                final_score_100,
                timestamp,
                timestamp,
            ),
        )
        evaluation_id = cursor.lastrowid
    else:
        evaluation_id = existing["id"]
        conn.execute(
            """
            UPDATE evaluations
            SET problem_score = ?, value_score = ?, validation_score = ?, business_score = ?, pitch_score = ?,
                observations = ?, final_score_5 = ?, final_score_100 = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                scores["problem_score"],
                scores["value_score"],
                scores["validation_score"],
                scores["business_score"],
                scores["pitch_score"],
                observations,
                final_score_5,
                final_score_100,
                timestamp,
                evaluation_id,
            ),
        )
    record_ranking_history(conn, pitch_id, evaluation_id, get_pitch_team_ids(conn, pitch_id))
    conn.commit()
    saved = conn.execute(
        """
        SELECT e.*, u.name AS juror_name, u.identifier AS juror_identifier
        FROM evaluations e
        INNER JOIN users u ON u.id = e.juror_id
        WHERE e.team_id = ? AND e.juror_id = ? AND e.pitch_id = ?
        """,
        (team_id, juror_id, pitch_id),
    ).fetchone()
    return {
        "id": saved["id"],
        "team_id": saved["team_id"],
        "juror_id": saved["juror_id"],
        "problem_score": saved["problem_score"],
        "value_score": saved["value_score"],
        "validation_score": saved["validation_score"],
        "business_score": saved["business_score"],
        "pitch_score": saved["pitch_score"],
        "observations": saved["observations"],
        "final_score_5": round(float(saved["final_score_5"]), 4),
        "final_score_100": round(float(saved["final_score_100"]), 4),
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
        "juror_name": saved["juror_name"],
        "juror_identifier": saved["juror_identifier"],
    }


class InnovatePitchHandler(SimpleHTTPRequestHandler):
    server_version = "InnovatePitch/1.0"

    def __init__(self, *args: Any, directory: str | None = None, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(FRONTEND_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/bootstrap":
            self.handle_bootstrap()
            return
        if parsed.path == "/api/admin/dashboard":
            self.handle_admin_dashboard(parsed)
            return
        if parsed.path == "/api/admin/export-pdf":
            self.handle_admin_export_pdf(parsed)
            return
        if parsed.path == "/api/admin/export-winners":
            self.handle_admin_export_winners(parsed)
            return
        if parsed.path.startswith("/api/admin/team/"):
            self.handle_admin_team(parsed)
            return
        if parsed.path == "/api/jury/dashboard":
            self.handle_jury_dashboard(parsed)
            return
        if parsed.path.startswith("/api/jury/team/"):
            self.handle_jury_team(parsed)
            return
        if parsed.path == "/api/logout":
            self.handle_logout()
            return
        if parsed.path in {"/", "/index.html"}:
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/login":
            self.handle_login()
            return
        if parsed.path == "/api/logout":
            self.handle_logout()
            return
        if parsed.path.startswith("/api/admin/jurors/") and parsed.path.rstrip("/").endswith(("/activate", "/deactivate")):
            self.handle_admin_juror_set_active(parsed)
            return
        if parsed.path.startswith("/api/admin/team/") and parsed.path.rstrip("/").endswith("/move-to-end"):
            self.handle_admin_team_move_to_end(parsed)
            return
        if parsed.path.startswith("/api/admin/team/") and parsed.path.rstrip("/").endswith(("/deactivate", "/reactivate")):
            self.handle_admin_team_set_active(parsed)
            return
        send_error_json(self, HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/admin/ranking":
            self.handle_save_ranking(parsed)
            return
        if parsed.path == "/api/admin/active-pitch":
            self.handle_set_active_pitch(parsed)
            return
        if parsed.path == "/api/admin/presentation-order":
            self.handle_save_presentation_order(parsed)
            return
        if parsed.path.startswith("/api/jury/evaluation/"):
            self.handle_save_evaluation(parsed)
            return
        send_error_json(self, HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

    def do_DELETE(self) -> None:
        send_error_json(self, HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

    def handle_bootstrap(self) -> None:
        with db_connect() as conn:
            user = get_session_user(conn, self.headers)
            if user is None:
                send_json(self, {"authenticated": False, "rubrics": RUBRICS})
                return
            payload: dict[str, Any] = {
                "authenticated": True,
                "user": serialize_user(user),
                "rubrics": RUBRICS,
            }
            if user["role"] == ROLE_ADMIN:
                payload["dashboard"] = get_admin_dashboard(conn)
            else:
                payload["dashboard"] = get_jury_dashboard(conn, user["id"], None, get_active_pitch(conn))
            send_json(self, payload)

    def handle_login(self) -> None:
        try:
            payload = load_json(self)
        except Exception:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El cuerpo de la solicitud no es válido.")
            return
        identifier = str(payload.get("identifier", "")).strip().lower()
        password = str(payload.get("password", ""))
        if not identifier or not password:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Debe indicar usuario y contraseña.")
            return
        with db_connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE LOWER(identifier) = ? AND active = 1",
                (identifier,),
            ).fetchone()
            if user is None or not verify_password(password, user["password_hash"]):
                send_error_json(self, HTTPStatus.UNAUTHORIZED, "Credenciales inválidas.")
                return
            token = create_session(conn, user["id"])
        cookie_value = f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"
        if self.request_version >= "HTTP/1.1":
            cookie_value += "; Max-Age=" + str(SESSION_TTL_HOURS * 3600)
        send_json(
            self,
            {"ok": True, "user": serialize_user(user)},
            extra_headers={"Set-Cookie": cookie_value},
        )

    def handle_logout(self) -> None:
        cookie = parse_cookie(self.headers.get("Cookie"))
        token = cookie.get(SESSION_COOKIE).value if cookie.get(SESSION_COOKIE) else None
        with db_connect() as conn:
            clear_session(conn, token)
        expired_cookie = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        send_json(self, {"ok": True}, extra_headers={"Set-Cookie": expired_cookie})

    def handle_set_active_pitch(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        try:
            payload = load_json(self)
        except Exception:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El cuerpo de la solicitud no es válido.")
            return
        try:
            pitch_id = int(payload.get("pitch"))
        except (TypeError, ValueError):
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Debe indicar un pitch válido.")
            return
        if pitch_id not in (PITCH_ONE, PITCH_TWO, PITCH_THREE):
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El pitch debe ser 1, 2 o 3.")
            return
        with db_connect() as conn:
            set_active_pitch(conn, pitch_id)
            conn.commit()
        send_json(
            self,
            {
                "ok": True,
                "message": f"El Pitch {pitch_id} ahora está activo para los jurados.",
                "active_pitch": pitch_id,
            },
        )

    def handle_admin_dashboard(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        params = parse_qs(parsed.query)
        pitch_id = parse_pitch_id(params)
        team_id = params.get("team_id", [None])[0]
        team_id_int = int(team_id) if team_id and str(team_id).isdigit() else None
        with db_connect() as conn:
            send_json(self, {"ok": True, "user": serialize_user(user), "dashboard": get_admin_dashboard(conn, team_id_int, pitch_id)})

    def handle_admin_team(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        team_id_text = parsed.path.rsplit("/", 1)[-1]
        if not team_id_text.isdigit():
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Equipo inválido.")
            return
        pitch_id = parse_pitch_id(parse_qs(urlparse(self.path).query))
        with db_connect() as conn:
            team_ids = get_pitch_team_ids(conn, pitch_id)
            detail = get_team_detail(conn, int(team_id_text), pitch_id, team_ids)
            if detail is None:
                send_error_json(self, HTTPStatus.NOT_FOUND, "Equipo no encontrado.")
                return
            send_json(self, {"ok": True, "team": detail})

    def handle_admin_export_pdf(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        pitch_id = parse_pitch_id(parse_qs(parsed.query))
        with db_connect() as conn:
            team_rows = conn.execute("SELECT id FROM teams ORDER BY display_order ASC, id ASC").fetchall()
            team_ids = get_pitch_team_ids(conn, pitch_id)
            if team_ids is not None:
                team_rows = [row for row in team_rows if row["id"] in team_ids]
            teams = []
            for row in team_rows:
                detail = get_team_detail(conn, row["id"], pitch_id, team_ids)
                if detail is not None:
                    teams.append({"name": detail["team"]["name"], "evaluations": detail["evaluations"]})
        pdf_bytes = build_all_teams_observations_pdf(teams)
        send_pdf(self, pdf_bytes, "observaciones_todos_los_equipos.pdf")

    def handle_admin_export_winners(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        pitch_id = parse_pitch_id(parse_qs(parsed.query))
        with db_connect() as conn:
            team_ids = get_pitch_team_ids(conn, pitch_id)
            ranking, _, _, _ = compute_team_stats(conn, pitch_id, team_ids)

        winners_count = winners_count_for_pitch(pitch_id)
        top_teams = ranking[:winners_count]
        headers = [
            "Posición",
            "Equipo",
            "Puntaje Final Equipo /5",
            "Puntaje Final Equipo /100",
            "Jurado",
            "Puntaje del Jurado /5",
            "Observaciones del Jurado",
        ]
        rows: list[list[Any]] = []
        for team in top_teams:
            evaluations = team.get("evaluations") or []
            if not evaluations:
                rows.append(
                    [
                        team["position"],
                        team["name"],
                        team["average_5"],
                        team["average_100"],
                        "",
                        "",
                        "Sin evaluaciones registradas",
                    ]
                )
                continue
            for evaluation in evaluations:
                rows.append(
                    [
                        team["position"],
                        team["name"],
                        team["average_5"],
                        team["average_100"],
                        evaluation.get("juror_name") or "",
                        evaluation.get("final_score_5"),
                        evaluation.get("observations") or "",
                    ]
                )

        xlsx_bytes = build_xlsx(headers, rows, sheet_name=f"Ganadores Top {winners_count}")
        send_xlsx(self, xlsx_bytes, f"ganadores_pitch{pitch_id}_top{winners_count}.xlsx")

    def handle_save_ranking(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        pitch_id = parse_pitch_id(parse_qs(parsed.query))
        try:
            payload = load_json(self)
        except Exception:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El cuerpo de la solicitud no es válido.")
            return
        order = payload.get("order")
        if not isinstance(order, list) or not order:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Debe enviar el orden deliberado de los equipos.")
            return
        try:
            order_ids = [int(team_id) for team_id in order]
        except (TypeError, ValueError):
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El orden de equipos es inválido.")
            return
        with db_connect() as conn:
            if pitch_id == PITCH_ONE:
                existing_ids = {row["id"] for row in conn.execute("SELECT id FROM teams").fetchall()}
            else:
                existing_ids = get_pitch_team_ids(conn, pitch_id) or set()
            if set(order_ids) != existing_ids or len(order_ids) != len(existing_ids):
                send_error_json(self, HTTPStatus.BAD_REQUEST, "El orden debe incluir cada equipo exactamente una vez.")
                return
            if pitch_id == PITCH_ONE:
                for position, team_id in enumerate(order_ids, start=1):
                    conn.execute("UPDATE teams SET manual_position = ? WHERE id = ?", (position, team_id))
            else:
                # Solo escribe la posición deliberada de este Pitch; no toca
                # calificaciones, el orden de presentación ni el Pitch 1.
                conn.execute(
                    "INSERT INTO pitch_team_settings (pitch_id, team_id, manual_position) "
                    "SELECT ?, t.team_id, t.position FROM unnest(?::bigint[], ?::integer[]) AS t(team_id, position) "
                    "ON CONFLICT (pitch_id, team_id) DO UPDATE SET manual_position = EXCLUDED.manual_position",
                    (pitch_id, order_ids, list(range(1, len(order_ids) + 1))),
                )
            conn.commit()
            dashboard = get_admin_dashboard(conn, None, pitch_id)
        send_json(self, {"ok": True, "message": "Deliberación guardada. El ranking quedó fijado en el orden elegido.", "dashboard": dashboard})

    def handle_jury_dashboard(self, parsed) -> None:
        user = require_user(self, ROLE_JURY)
        if user is None:
            return
        params = parse_qs(parsed.query)
        team_id = params.get("team_id", [None])[0]
        team_id_int = int(team_id) if team_id and str(team_id).isdigit() else None
        with db_connect() as conn:
            pitch_id = get_active_pitch(conn)
            send_json(self, {"ok": True, "user": serialize_user(user), "dashboard": get_jury_dashboard(conn, user["id"], team_id_int, pitch_id)})

    def handle_jury_team(self, parsed) -> None:
        user = require_user(self, ROLE_JURY)
        if user is None:
            return
        team_id_text = parsed.path.rsplit("/", 1)[-1]
        if not team_id_text.isdigit():
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Equipo inválido.")
            return
        with db_connect() as conn:
            pitch_id = get_active_pitch(conn)
            team_ids = get_pitch_team_ids(conn, pitch_id)
            detail = get_team_detail(conn, int(team_id_text), pitch_id, team_ids)
            if detail is None or detail["team"]["deactivated"]:
                send_error_json(self, HTTPStatus.NOT_FOUND, "Equipo no encontrado.")
                return
            current = conn.execute(
                "SELECT * FROM evaluations WHERE team_id = ? AND juror_id = ? AND pitch_id = ?",
                (int(team_id_text), user["id"], pitch_id),
            ).fetchone()
            evaluation = None
            if current is not None:
                evaluation = {
                    "id": current["id"],
                    "team_id": current["team_id"],
                    "juror_id": current["juror_id"],
                    "problem_score": current["problem_score"],
                    "value_score": current["value_score"],
                    "validation_score": current["validation_score"],
                    "business_score": current["business_score"],
                    "pitch_score": current["pitch_score"],
                    "observations": current["observations"],
                    "final_score_5": round(float(current["final_score_5"]), 4),
                    "final_score_100": round(float(current["final_score_100"]), 4),
                    "created_at": current["created_at"],
                    "updated_at": current["updated_at"],
                }
            send_json(self, {"ok": True, "team": detail, "evaluation": evaluation})

    def handle_save_presentation_order(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        pitch_id = parse_pitch_id(parse_qs(parsed.query))
        try:
            payload = load_json(self)
            order_ids = [int(team_id) for team_id in payload.get("order") or []]
        except Exception:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El orden de equipos es inválido.")
            return
        if not order_ids or len(order_ids) != len(set(order_ids)):
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El orden debe incluir cada equipo una sola vez.")
            return
        with db_connect() as conn:
            team_ids = get_pitch_team_ids(conn, pitch_id)
            current_ids = [row["id"] for row in get_pitch_presentation_rows(conn, pitch_id, team_ids, include_deactivated=True)]
            if not set(order_ids) <= set(current_ids):
                send_error_json(self, HTTPStatus.BAD_REQUEST, "El orden incluye equipos que no pertenecen a este Pitch.")
                return
            # Los equipos que no vinieron en la lista (p. ej. desactivados)
            # conservan su orden relativo, después de los enviados.
            listed = set(order_ids)
            full_order = order_ids + [team_id for team_id in current_ids if team_id not in listed]
            save_pitch_presentation_order(conn, pitch_id, full_order)
            conn.commit()
        send_json(self, {"ok": True, "message": "Orden de presentación guardado. Los jurados verán los equipos en este orden."})

    def handle_admin_juror_set_active(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        parts = [part for part in parsed.path.split("/") if part]
        # parts look like: ['api', 'admin', 'jurors', '<id>', 'activate' | 'deactivate']
        if len(parts) != 5 or not parts[3].isdigit():
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Jurado inválido.")
            return
        juror_id = int(parts[3])
        activate = parts[4] == "activate"
        with db_connect() as conn:
            juror = conn.execute("SELECT id, name, role FROM users WHERE id = ?", (juror_id,)).fetchone()
            # Solo se pueden activar/desactivar jurados, nunca administradores.
            if juror is None or juror["role"] != ROLE_JURY:
                send_error_json(self, HTTPStatus.NOT_FOUND, "Jurado no encontrado.")
                return
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (1 if activate else 0, juror_id))
            if not activate:
                # Cierra sus sesiones abiertas: un jurado inactivo no puede calificar.
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (juror_id,))
            conn.commit()
        message = (
            f"{juror['name']} fue activado y puede volver a calificar."
            if activate
            else f"{juror['name']} fue desactivado: ya no puede calificar ni se espera su evaluación."
        )
        send_json(self, {"ok": True, "message": message})

    def parse_admin_team_action(self, parsed) -> tuple[int, int] | None:
        parts = [part for part in parsed.path.split("/") if part]
        # parts look like: ['api', 'admin', 'team', '<id>', '<action>']
        if len(parts) != 5 or not parts[3].isdigit():
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Equipo inválido.")
            return None
        return int(parts[3]), parse_pitch_id(parse_qs(parsed.query))

    def handle_admin_team_move_to_end(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        target = self.parse_admin_team_action(parsed)
        if target is None:
            return
        team_id, pitch_id = target
        with db_connect() as conn:
            team_ids = get_pitch_team_ids(conn, pitch_id)
            rows = get_pitch_presentation_rows(conn, pitch_id, team_ids, include_deactivated=True)
            current_ids = [row["id"] for row in rows]
            if team_id not in current_ids:
                send_error_json(self, HTTPStatus.NOT_FOUND, "El equipo no pertenece a este Pitch.")
                return
            name = next(row["name"] for row in rows if row["id"] == team_id)
            save_pitch_presentation_order(conn, pitch_id, [tid for tid in current_ids if tid != team_id] + [team_id])
            conn.commit()
        send_json(self, {"ok": True, "message": f"{name} se movió al final de la lista de presentación."})

    def handle_admin_team_set_active(self, parsed) -> None:
        user = require_user(self, ROLE_ADMIN)
        if user is None:
            return
        target = self.parse_admin_team_action(parsed)
        if target is None:
            return
        team_id, pitch_id = target
        deactivate = parsed.path.rstrip("/").endswith("/deactivate")
        with db_connect() as conn:
            team_ids = get_pitch_team_ids(conn, pitch_id)
            team = conn.execute("SELECT id, name FROM teams WHERE id = ?", (team_id,)).fetchone()
            if team is None or (team_ids is not None and team_id not in team_ids):
                send_error_json(self, HTTPStatus.NOT_FOUND, "El equipo no pertenece a este Pitch.")
                return
            set_pitch_team_deactivated(conn, pitch_id, team_id, deactivate)
            conn.commit()
        message = (
            f"{team['name']} fue desactivado: los jurados ya no lo verán en este Pitch."
            if deactivate
            else f"{team['name']} fue reactivado y vuelve a aparecer para los jurados."
        )
        send_json(self, {"ok": True, "message": message})

    def handle_save_evaluation(self, parsed) -> None:
        user = require_user(self, ROLE_JURY)
        if user is None:
            return
        team_id_text = parsed.path.rsplit("/", 1)[-1]
        if not team_id_text.isdigit():
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Equipo inválido.")
            return
        try:
            payload = load_json(self)
        except Exception:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "El cuerpo de la solicitud no es válido.")
            return
        missing = [key for key in RUBRIC_KEYS if key not in payload]
        if missing:
            send_error_json(self, HTTPStatus.BAD_REQUEST, "Debe completar las cinco rúbricas antes de guardar.")
            return
        try:
            with db_connect() as conn:
                pitch_id = get_active_pitch(conn)
                team_exists = conn.execute("SELECT id FROM teams WHERE id = ?", (int(team_id_text),)).fetchone()
                if team_exists is None:
                    send_error_json(self, HTTPStatus.NOT_FOUND, "Equipo no encontrado.")
                    return
                team_ids = get_pitch_team_ids(conn, pitch_id)
                if team_ids is not None and int(team_id_text) not in team_ids:
                    send_error_json(self, HTTPStatus.FORBIDDEN, "El equipo no pertenece a este Pitch.")
                    return
                if get_pitch_team_settings(conn, pitch_id).get(int(team_id_text), {}).get("deactivated"):
                    send_error_json(self, HTTPStatus.FORBIDDEN, "Este equipo fue desactivado por el administrador y ya no se puede calificar.")
                    return
                saved = store_evaluation(conn, int(team_id_text), user["id"], payload, pitch_id)
                team_rows = get_pitch_presentation_rows(conn, pitch_id, team_ids)
                next_team_id = get_next_pending_team_id(conn, user["id"], team_rows, int(team_id_text), pitch_id)
                dashboard = get_jury_dashboard(conn, user["id"], next_team_id, pitch_id)
            all_done = dashboard["progress"]["pending"] == 0
            message = (
                "¡Evaluación guardada! Ya calificaste todos los equipos."
                if all_done
                else "Evaluación guardada correctamente. Mostrando el siguiente equipo."
            )
            send_json(self, {"ok": True, "message": message, "evaluation": saved, "dashboard": dashboard})
        except ValueError as exc:
            send_error_json(self, HTTPStatus.BAD_REQUEST, str(exc))
        except sqlite3.IntegrityError:
            send_error_json(self, HTTPStatus.CONFLICT, "Ya existe una evaluación para este equipo y jurado.")


def load_dotenv_if_present(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a .env file next to this script, without
    overwriting variables already set in the real environment. No third-party
    dependency needed (keeps this project's zero-dependency approach)."""
    env_path = ROOT / path
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    load_dotenv_if_present()
    initialize_database()

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))

    server = ThreadingHTTPServer((host, port), InnovatePitchHandler)
    print(f"Innovate Pitch server running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()