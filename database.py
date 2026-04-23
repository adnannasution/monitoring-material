"""
database.py — Koneksi PostgreSQL, migrasi, dan helper query
"""
import os
import json
import decimal
import datetime
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_SSL = os.getenv("DB_SSL", "false").lower() == "true"

_pool = None


def get_pool():
    global _pool
    if _pool is None:
        ssl_config = {"sslmode": "require"} if DB_SSL else {}
        _pool = psycopg2.pool.SimpleConnectionPool(
            1, 20,
            DATABASE_URL,
            **ssl_config,
            cursor_factory=RealDictCursor
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def release_conn(conn):
    get_pool().putconn(conn)


def query(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            try:
                return cur.fetchall()
            except Exception:
                return []
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


def execute_many(sql, params_list):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.executemany(sql, params_list)
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


def with_transaction(fn):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            result = fn(conn, cur)
            conn.commit()
            return result
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        release_conn(conn)


# ─── STATE HELPERS ───────────────────────────────────────────
def get_state(key):
    row = query_one("SELECT value FROM app_state WHERE key=%s", (key,))
    if row:
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]
    return None


class _JSONEncoder(json.JSONEncoder):
    """Handle Decimal, date, datetime dari PostgreSQL."""
    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return str(obj)
        return super().default(obj)


def _dumps(v):
    return json.dumps(v, cls=_JSONEncoder)


def set_state(key, value):
    execute(
        """
        INSERT INTO app_state(key, value, updated_at)
        VALUES (%s, %s, NOW())
        ON CONFLICT(key) DO UPDATE SET value=%s, updated_at=NOW()
        """,
        (key, _dumps(value), _dumps(value)),
    )


# ─── MIGRASI ─────────────────────────────────────────────────
def migrate():
    print("🔄 Running PostgreSQL migration...")

    execute("""
        CREATE TABLE IF NOT EXISTS taex_reservasi (
            id                   SERIAL PRIMARY KEY,
            plant                TEXT,
            equipment            TEXT,
            "order"              TEXT,
            revision             TEXT,
            material             TEXT,
            itm                  TEXT,
            material_description TEXT,
            qty_reqmts           NUMERIC DEFAULT 0,
            qty_stock            NUMERIC DEFAULT 0,
            pr                   TEXT,
            item                 TEXT,
            qty_pr               NUMERIC,
            po                   TEXT,
            po_date              TEXT,
            qty_deliv            NUMERIC,
            delivery_date        TEXT,
            sloc                 TEXT,
            del                  TEXT,
            fis                  TEXT,
            ict                  TEXT,
            pg                   TEXT,
            recipient            TEXT,
            unloading_point      TEXT,
            reqmts_date          TEXT,
            qty_f_avail_check    NUMERIC,
            qty_withdrawn        NUMERIC,
            uom                  TEXT,
            gl_acct              TEXT,
            res_price            NUMERIC,
            res_per              NUMERIC,
            res_curr             TEXT,
            reservno             TEXT,
            cost_ctrs            TEXT,
            created_at           TIMESTAMPTZ DEFAULT NOW(),
            updated_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS prisma_reservasi (
            id                   SERIAL PRIMARY KEY,
            plant                TEXT,
            equipment            TEXT,
            revision             TEXT,
            "order"              TEXT,
            reservno             TEXT,
            itm                  TEXT,
            material             TEXT,
            material_description TEXT,
            del                  TEXT,
            fis                  TEXT,
            ict                  TEXT,
            pg                   TEXT,
            recipient            TEXT,
            unloading_point      TEXT,
            reqmts_date          TEXT,
            qty_reqmts           NUMERIC DEFAULT 0,
            uom                  TEXT,
            pr_prisma            TEXT,
            item_prisma          TEXT,
            qty_pr_prisma        NUMERIC,
            qty_stock_onhand     NUMERIC,
            code_kertas_kerja    TEXT,
            created_at           TIMESTAMPTZ DEFAULT NOW(),
            updated_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS kumpulan_summary (
            id                   SERIAL PRIMARY KEY,
            plant                TEXT,
            equipment            TEXT,
            revision             TEXT,
            "order"              TEXT,
            reservno             TEXT,
            itm                  TEXT,
            material             TEXT,
            material_description TEXT,
            qty_req              NUMERIC DEFAULT 0,
            qty_stock            NUMERIC DEFAULT 0,
            qty_pr               NUMERIC,
            qty_to_pr            NUMERIC,
            code_tracking        TEXT,
            created_at           TIMESTAMPTZ DEFAULT NOW(),
            updated_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS sap_pr (
            id                   SERIAL PRIMARY KEY,
            plant                TEXT,
            pr                   TEXT,
            item                 TEXT,
            material             TEXT,
            material_description TEXT,
            d                    TEXT,
            r                    TEXT,
            pgr                  TEXT,
            tracking_no          TEXT,
            qty_pr               NUMERIC,
            un                   TEXT,
            req_date             TEXT,
            valn_price           NUMERIC,
            pr_curr              TEXT,
            pr_per               NUMERIC,
            release_date         TEXT,
            tracking             TEXT,
            s                    TEXT,
            created_at           TIMESTAMPTZ DEFAULT NOW(),
            updated_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS sap_po (
            id             SERIAL PRIMARY KEY,
            plnt           TEXT,
            purchreq       TEXT,
            item           TEXT,
            material       TEXT,
            short_text     TEXT,
            po             TEXT,
            po_item        TEXT,
            d              TEXT,
            dci            TEXT,
            pgr            TEXT,
            doc_date       TEXT,
            po_quantity    NUMERIC,
            qty_delivered  NUMERIC,
            deliv_date     TEXT,
            oun            TEXT,
            net_price      NUMERIC,
            crcy           TEXT,
            per            NUMERIC,
            created_at     TIMESTAMPTZ DEFAULT NOW(),
            updated_at     TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS work_order (
            id                 SERIAL PRIMARY KEY,
            plant              TEXT,
            "order"            TEXT,
            superior_order     TEXT,
            notification       TEXT,
            created_on         TEXT,
            description        TEXT,
            revision           TEXT,
            equipment          TEXT,
            system_status      TEXT,
            user_status        TEXT,
            funct_location     TEXT,
            location           TEXT,
            wbs_ord_header     TEXT,
            cost_center        TEXT,
            total_plan_cost    NUMERIC,
            total_act_cost     NUMERIC,
            planner_group      TEXT,
            main_work_ctr      TEXT,
            entry_by           TEXT,
            changed_by         TEXT,
            basic_start_date   TEXT,
            basic_finish_date  TEXT,
            actual_release     TEXT,
            created_at         TIMESTAMPTZ DEFAULT NOW(),
            updated_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    execute("""
        CREATE TABLE IF NOT EXISTS app_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # Indexes
    for sql in [
        'CREATE INDEX IF NOT EXISTS idx_taex_material ON taex_reservasi(material)',
        'CREATE INDEX IF NOT EXISTS idx_taex_order    ON taex_reservasi("order")',
        'CREATE UNIQUE INDEX IF NOT EXISTS idx_taex_upsert_key ON taex_reservasi("order", material, itm)',
        'CREATE INDEX IF NOT EXISTS idx_prisma_material ON prisma_reservasi(material)',
        'CREATE INDEX IF NOT EXISTS idx_prisma_order    ON prisma_reservasi("order")',
        'CREATE INDEX IF NOT EXISTS idx_sap_pr          ON sap_pr(pr)',
        'CREATE INDEX IF NOT EXISTS idx_kumpulan_code   ON kumpulan_summary(code_tracking)',
        'CREATE INDEX IF NOT EXISTS idx_sap_po_po       ON sap_po(po)',
        'CREATE INDEX IF NOT EXISTS idx_sap_po_purchreq ON sap_po(purchreq)',
    ]:
        try:
            execute(sql)
        except Exception:
            pass

    print("✅ Migration complete")
