import os
from typing import Any

import pymysql
from dotenv import load_dotenv

load_dotenv()

_CONNECTION_CACHE = {}


def get_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or value == "":
        raise ValueError(f"В .env не задана переменная: {name}")
    return value


class MySQLConnection:
    def __init__(self, db_prefix: str):
        self.db_prefix = db_prefix
        self.host = get_env(f"{db_prefix}_HOST")
        self.port = int(get_env(f"{db_prefix}_PORT"))
        self.user = get_env(f"{db_prefix}_USER")
        self.password = get_env(f"{db_prefix}_PASSWORD")
        self.db_name = get_env(f"{db_prefix}_NAME")
        self.connection = None

    def is_alive(self) -> bool:
        return self.connection is not None and self.connection.open

    def connect(self):
        if self.is_alive():
            return
        self.close()
        self.connection = pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def execute(self, sql: str, params=None) -> Any:
        self.connect()
        try:
            with self.connection.cursor() as cursor:
                cursor.execute(sql, params)
                if sql.lstrip().upper().startswith(("SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN")):
                    return cursor.fetchall()
                self.connection.commit()
                return {"affected_rows": cursor.rowcount}
        except Exception:
            if self.connection:
                self.connection.rollback()
            raise

    def close(self):
        if self.connection:
            try:
                self.connection.close()
            finally:
                self.connection = None


def get_mysql_connection(db_prefix: str, reuse: bool = True) -> MySQLConnection:
    if not reuse:
        connection = MySQLConnection(db_prefix)
        connection.connect()
        return connection

    cached = _CONNECTION_CACHE.get(db_prefix)
    if cached and cached.is_alive():
        return cached

    connection = MySQLConnection(db_prefix)
    connection.connect()
    _CONNECTION_CACHE[db_prefix] = connection
    return connection


def run_mysql_query(db_prefix: str, sql: str, params=None, reuse: bool = True):
    connection = get_mysql_connection(db_prefix, reuse=reuse)
    try:
        return connection.execute(sql, params)
    except Exception:
        if reuse:
            cached = _CONNECTION_CACHE.pop(db_prefix, None)
            if cached:
                cached.close()
        else:
            connection.close()
        raise
    finally:
        if not reuse:
            connection.close()


def close_all_mysql_connections():
    while _CONNECTION_CACHE:
        _, connection = _CONNECTION_CACHE.popitem()
        connection.close()


if __name__ == "__main__":
    result = run_mysql_query("DB_HELPDESK", "SELECT NOW() AS now_time")
    print(result)
