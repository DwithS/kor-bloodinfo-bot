import json
import sqlite3
from datetime import datetime
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id INTEGER PRIMARY KEY,
                bloodinfo_id_enc TEXT NOT NULL,
                bloodinfo_pw_enc TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                target_date TEXT NOT NULL,
                donation_types_json TEXT NOT NULL,
                site_codes_json TEXT NOT NULL,
                time_from INTEGER,
                time_to INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                notified_at TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorite_sites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER NOT NULL,
                sitecode TEXT NOT NULL,
                sitename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id),
                UNIQUE(telegram_user_id, sitecode)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_favorites_user ON favorite_sites (telegram_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active ON subscriptions (telegram_user_id, is_active)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subscriptions_date_active ON subscriptions (target_date, is_active)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sites (
                sitecode TEXT PRIMARY KEY,
                sitename TEXT NOT NULL,
                orgname TEXT,
                address TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS site_cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sites_sitename ON sites (sitename)"
        )

        # Backward-compatible migration for old DB files.
        subscription_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()
        }
        if "time_from" not in subscription_columns:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN time_from INTEGER")
        if "time_to" not in subscription_columns:
            conn.execute("ALTER TABLE subscriptions ADD COLUMN time_to INTEGER")


def upsert_user_credentials(db_path: str, telegram_user_id: int, bloodinfo_id_enc: str, bloodinfo_pw_enc: str) -> None:
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_user_id, bloodinfo_id_enc, bloodinfo_pw_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id)
            DO UPDATE SET
                bloodinfo_id_enc = excluded.bloodinfo_id_enc,
                bloodinfo_pw_enc = excluded.bloodinfo_pw_enc,
                updated_at = excluded.updated_at
            """,
            (telegram_user_id, bloodinfo_id_enc, bloodinfo_pw_enc, now, now),
        )


def get_user_credentials(db_path: str, telegram_user_id: int) -> dict[str, str] | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT bloodinfo_id_enc, bloodinfo_pw_enc FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "bloodinfo_id_enc": row["bloodinfo_id_enc"],
        "bloodinfo_pw_enc": row["bloodinfo_pw_enc"],
    }


def deactivate_duplicate_subscriptions(
    db_path: str,
    telegram_user_id: int,
    target_date: str,
    donation_types: list[str],
) -> int:
    donation_types_json = json.dumps(sorted(donation_types), ensure_ascii=False)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, donation_types_json FROM subscriptions
            WHERE telegram_user_id = ? AND target_date = ? AND is_active = 1
            """,
            (telegram_user_id, target_date),
        ).fetchall()
        deactivated = 0
        for row in rows:
            existing_types = json.dumps(sorted(json.loads(row["donation_types_json"])), ensure_ascii=False)
            if existing_types == donation_types_json:
                conn.execute(
                    "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
                    (row["id"],),
                )
                deactivated += 1
        return deactivated


def add_subscription(
    db_path: str,
    telegram_user_id: int,
    target_date: str,
    donation_types: list[str],
    site_codes: list[str],
    time_from: int | None = None,
    time_to: int | None = None,
) -> int:
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO subscriptions (
                telegram_user_id, target_date, donation_types_json, site_codes_json,
                time_from, time_to, is_active, notified_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?)
            """,
            (
                telegram_user_id,
                target_date,
                json.dumps(donation_types, ensure_ascii=False),
                json.dumps(site_codes, ensure_ascii=False),
                time_from,
                time_to,
                now,
            ),
        )
        return int(cursor.lastrowid)


def list_subscriptions(db_path: str, telegram_user_id: int) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, target_date, donation_types_json, site_codes_json,
                   time_from, time_to,
                   is_active, notified_at, created_at
            FROM subscriptions
            WHERE telegram_user_id = ?
            ORDER BY id DESC
            """,
            (telegram_user_id,),
        ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "target_date": row["target_date"],
                "donation_types": json.loads(row["donation_types_json"]),
                "site_codes": json.loads(row["site_codes_json"]),
                "time_from": row["time_from"],
                "time_to": row["time_to"],
                "is_active": bool(row["is_active"]),
                "notified_at": row["notified_at"],
                "created_at": row["created_at"],
            }
        )
    return result


def cancel_subscription(db_path: str, telegram_user_id: int, subscription_id: int) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            """
            UPDATE subscriptions
            SET is_active = 0
            WHERE id = ? AND telegram_user_id = ? AND is_active = 1
            """,
            (subscription_id, telegram_user_id),
        )
        return cursor.rowcount > 0


def mark_subscription_notified(db_path: str, subscription_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE subscriptions
            SET is_active = 0, notified_at = ?
            WHERE id = ?
            """,
            (now, subscription_id),
        )


def get_active_subscriptions(db_path: str) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.telegram_user_id, s.target_date, s.donation_types_json, s.site_codes_json,
                   s.time_from, s.time_to,
                   u.bloodinfo_id_enc, u.bloodinfo_pw_enc
            FROM subscriptions s
            INNER JOIN users u ON u.telegram_user_id = s.telegram_user_id
            WHERE s.is_active = 1
            ORDER BY s.id ASC
            """
        ).fetchall()

    result = []
    for row in rows:
        result.append(
            {
                "id": row["id"],
                "telegram_user_id": row["telegram_user_id"],
                "target_date": row["target_date"],
                "donation_types": json.loads(row["donation_types_json"]),
                "site_codes": json.loads(row["site_codes_json"]),
                "time_from": row["time_from"],
                "time_to": row["time_to"],
                "bloodinfo_id_enc": row["bloodinfo_id_enc"],
                "bloodinfo_pw_enc": row["bloodinfo_pw_enc"],
            }
        )
    return result


def add_favorite_site(db_path: str, telegram_user_id: int, sitecode: str, sitename: str) -> bool:
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO favorite_sites (telegram_user_id, sitecode, sitename, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_user_id, sitecode, sitename, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_favorite_site(db_path: str, telegram_user_id: int, sitecode: str) -> bool:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "DELETE FROM favorite_sites WHERE telegram_user_id = ? AND sitecode = ?",
            (telegram_user_id, sitecode),
        )
        return cursor.rowcount > 0


def list_favorite_sites(db_path: str, telegram_user_id: int) -> list[dict[str, str]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT sitecode, sitename FROM favorite_sites WHERE telegram_user_id = ? ORDER BY sitename ASC",
            (telegram_user_id,),
        ).fetchall()
    return [{"sitecode": row["sitecode"], "sitename": row["sitename"]} for row in rows]


def get_site_cache_refresh_date(db_path: str) -> str | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM site_cache_meta WHERE key = 'last_refresh_date'"
        ).fetchone()
    if row is None:
        return None
    return row["value"]


def refresh_sites_cache(db_path: str, sites: list[dict[str, Any]], refresh_date: str) -> None:
    now = datetime.utcnow().isoformat()
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM sites")
        for site in sites:
            conn.execute(
                """
                INSERT INTO sites (sitecode, sitename, orgname, address, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(site.get("sitecode", "")).strip(),
                    str(site.get("sitename", "")).strip(),
                    str(site.get("orgname", "")).strip(),
                    str(site.get("address", "")).strip(),
                    now,
                ),
            )
        conn.execute(
            """
            INSERT INTO site_cache_meta (key, value)
            VALUES ('last_refresh_date', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (refresh_date,),
        )


def search_sites_by_region(db_path: str, region: str, name_keyword: str = "", limit: int = 100) -> list[dict[str, str]]:
    region_like = f"%{region.strip()}%"
    keyword_like = f"%{name_keyword.strip()}%"

    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT sitecode, sitename, orgname, address
            FROM sites
            WHERE (address LIKE ? OR orgname LIKE ?)
              AND sitename LIKE ?
            ORDER BY sitename ASC
            LIMIT ?
            """,
            (region_like, region_like, keyword_like, limit),
        ).fetchall()

    return [
        {
            "sitecode": row["sitecode"],
            "sitename": row["sitename"],
            "orgname": row["orgname"],
            "address": row["address"],
        }
        for row in rows
    ]


def resolve_site_codes_by_names(db_path: str, site_names: list[str]) -> tuple[list[str], list[str]]:
    codes: list[str] = []
    not_found: list[str] = []

    with _connect(db_path) as conn:
        for site_name in site_names:
            row = conn.execute(
                "SELECT sitecode FROM sites WHERE sitename = ? LIMIT 1",
                (site_name.strip(),),
            ).fetchone()
            if row is None:
                not_found.append(site_name)
                continue
            code = row["sitecode"]
            if code not in codes:
                codes.append(code)

    return codes, not_found
