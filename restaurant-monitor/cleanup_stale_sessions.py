"""
cleanup_stale_sessions.py
---------------------------
Closes out customer_sessions rows stuck at status=ACTIVE from today's many
abrupt run_live.py/server restarts (each restart could interrupt a session
before its real CUSTOMER_LEFT event ever fired).

Run from the project root, using the SAME venv the backend already uses
(it reads DB connection details straight from your .env, same as the app
and alembic do).
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

import psycopg2  # noqa: E402
from app.core.config import settings  # noqa: E402


def main() -> None:
    # SYNC_DATABASE_URL is postgresql+psycopg2://..., psycopg2.connect()
    # doesn't understand the "+psycopg2" driver suffix -- strip it.
    dsn = settings.SYNC_DATABASE_URL.replace("postgresql+psycopg2://", "postgresql://")

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM customer_sessions WHERE status = 'ACTIVE'")
            before = cur.fetchone()[0]
            print(f"customer_sessions currently ACTIVE: {before}")

            if before == 0:
                print("Nothing to clean up.")
                return

            cur.execute(
                """
                UPDATE customer_sessions
                SET status = 'COMPLETED', left_at = NOW()
                WHERE status = 'ACTIVE'
                """
            )
            conn.commit()
            print(f"Closed out {cur.rowcount} stale ACTIVE session(s) -> COMPLETED.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
