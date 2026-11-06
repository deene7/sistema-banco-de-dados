"""Acesso transacional ao banco. SQLite é usado somente na demonstração/testes."""
import sqlite3
import pymysql


class Database:
    def __init__(self, config):
        self._closed=False
        self._on_commit = config.get('on_commit')
        self.sqlite = bool(config.get('sqlite_path'))
        if self.sqlite:
            self.conn = sqlite3.connect(config['sqlite_path'], timeout=10)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute('PRAGMA foreign_keys = ON')
        else:
            self.conn = pymysql.connect(
                host=config.get('host', '127.0.0.1'), port=config.get('port', 3306),
                user=config['user'], password=config['password'],
                database=config.get('database', 'wa_clientes'), charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor, autocommit=False,
                init_command="SET time_zone='-03:00', innodb_lock_wait_timeout=8, max_execution_time=15000",
                connect_timeout=5, read_timeout=config.get('read_timeout',20), write_timeout=20)

    def execute(self, sql, params=()):
        if self.sqlite:
            sql = sql.replace('%s', '?').replace(' FOR UPDATE', '')
        cursor = self.conn.cursor()
        cursor.execute(sql, params)
        return cursor

    def one(self, sql, params=()):
        cur = self.execute(sql, params)
        row = cur.fetchone()
        cur.close()
        return dict(row) if row is not None else None

    def many(self, sql, params):
        if self.sqlite:
            sql = sql.replace('%s', '?')
        cursor = self.conn.cursor()
        cursor.executemany(sql, params)
        count = cursor.rowcount
        cursor.close()
        return count

    def all(self, sql, params=()):
        cur = self.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]
        cur.close()
        return rows

    def commit(self):
        self.conn.commit()
        if self._on_commit:
            self._on_commit()

    def rollback(self):
        if self._closed or (not self.sqlite and not self.conn.open):return
        try:self.conn.rollback()
        except pymysql.MySQLError as error:
            if not error.args or error.args[0] not in (0,2006,2013,2055):raise

    def close(self):
        if self._closed:return
        if not self.sqlite and not self.conn.open:
            self._closed=True
            return
        try:self.conn.close()
        except pymysql.MySQLError as error:
            if not error.args or error.args[0] not in (0,2006,2013,2055):raise
        finally:self._closed=True
