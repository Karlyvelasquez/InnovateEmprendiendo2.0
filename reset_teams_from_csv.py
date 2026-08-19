"""Reemplaza TODOS los equipos en la base de datos (Neon) por los que están
en teams_schedule.csv, en el orden exacto del archivo.

Qué hace:
  1. Borra evaluaciones, inasistencias ("no asistió"), historial de ranking,
     usos de "mejorar con IA" y equipos actuales.
  2. Inserta los equipos de teams_schedule.csv en orden, guardando el día
     (19 o 20 de agosto) y el horario de cada uno.
  3. Dado que los equipos viejos ya no existen, cualquier evaluación previa
     asociada a ellos también se borra (no tendría a qué equipo pertenecer).

Qué NO toca:
  - La tabla de usuarios (jurados / administradores) se mantiene intacta.
  - Las sesiones activas de jurados/administradores tampoco se tocan.

Uso:
  DATABASE_URL="postgresql://..." python reset_teams_from_csv.py

Requiere que teams_schedule.csv esté en la misma carpeta que este script
(o pasa la ruta con la variable de entorno TEAMS_CSV_PATH).
"""
from __future__ import annotations

import os

import server


def main() -> None:
    if not server.DATABASE_URL:
        raise SystemExit("Falta la variable de entorno DATABASE_URL de Neon.")

    csv_override = os.environ.get("TEAMS_CSV_PATH", "").strip()
    csv_path = server.Path(csv_override) if csv_override else server.TEAMS_CSV_PATH
    teams = server.parse_teams_csv(csv_path)
    if not teams:
        raise SystemExit(f"No se encontraron equipos en {csv_path}. Nada que hacer.")

    from collections import Counter

    per_day = Counter(team["schedule_day"] or "(sin día)" for team in teams)
    print(f"Se leyeron {len(teams)} equipos de {csv_path}:")
    for day, count in per_day.items():
        print(f"  - {day}: {count} equipos")

    confirm = os.environ.get("CONFIRM", "").strip().lower()
    if confirm != "si":
        answer = input(
            "\nEsto BORRARÁ todos los equipos, evaluaciones, inasistencias e "
            "historial de ranking actuales en la base de datos y los "
            "reemplazará por la lista de arriba. Los usuarios (jurados/admins) "
            "NO se tocan.\n¿Continuar? (escribe 'si' para confirmar): "
        )
        if answer.strip().lower() != "si":
            print("Cancelado. No se hizo ningún cambio.")
            return

    with server.db_connect() as conn:
        server.ensure_schema(conn)
        server.replace_all_teams(conn, teams)
        conn.commit()
        server.seed_initial_history(conn)
        conn.commit()

    print("\nListo. La base de datos quedó con el nuevo cronograma de equipos.")
    print("Las evaluaciones, inasistencias e historial de ranking anteriores fueron eliminados.")


if __name__ == "__main__":
    main()
