import pymysql
import json
from datetime import datetime
from contextlib import contextmanager

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "Lst201314",
    "database": "ai-test-agent",
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
    "autocommit": False,
}


@contextmanager
def get_conn():
    conn = pymysql.connect(**DB_CONFIG)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ===============================
# Session 表
# ===============================
def create_session(session_id, file_name, file_path):
    sql = """
    INSERT INTO test_session
    (id, file_name, file_path, status, created_at, updated_at)
    VALUES (%s, %s, %s, %s, NOW(), NOW())
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (session_id, file_name, file_path, "UPLOADED"))


def update_session_status(session_id, status):
    sql = """
    UPDATE test_session
    SET status=%s, updated_at=NOW()
    WHERE id=%s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (status, session_id))


def get_session(session_id):
    sql = """
    SELECT *
    FROM test_session
    WHERE id=%s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (session_id,))
            return cur.fetchone()


# ===============================
# Session Data 表
# ===============================
def insert_session_data(session_id, data_type, content):
    sql = """
    INSERT INTO test_session_data
    (session_id, type, content, created_at)
    VALUES (%s, %s, %s, NOW())
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    session_id,
                    data_type,
                    json.dumps(content, ensure_ascii=False),
                ),
            )


# ⭐ 兼容 main.py / orchestrator
def save_session_data(session_id, data_type, content):
    insert_session_data(session_id, data_type, content)


def get_session_data(session_id, data_type):
    sql = """
    SELECT content
    FROM test_session_data
    WHERE session_id=%s AND type=%s
    ORDER BY created_at DESC
    LIMIT 1
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (session_id, data_type))
            row = cur.fetchone()
            if not row:
                return None
            return json.loads(row["content"])
