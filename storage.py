"""Durable PostgreSQL adapter with explicit JSON beta fallback."""
from pathlib import Path
import json, os, threading
try:
    import psycopg2
    from psycopg2.extras import Json
except ImportError:
    psycopg2 = None
    Json = None

class StorageBackend:
    VERSION = 'kv-v1'
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.url_configured = bool(os.environ.get('DATABASE_URL','').strip())
        self.mode = 'json-beta'
        self.reason = 'DATABASE_URL não configurada; armazenamento local não durável para beta.'
        self.migration = 'not_configured'
        self.lock = threading.RLock()
        if self.url_configured and psycopg2 is None:
            self.reason = 'DATABASE_URL configurada, mas o driver PostgreSQL não está instalado.'
        elif self.url_configured:
            try:
                self._schema()
                self._migrate_files()
                self.mode = 'postgresql'
                self.reason = ''
                self.migration = 'kv-v1-ready'
            except Exception as exc:
                self.reason = 'PostgreSQL configurado, mas indisponível; fallback local beta ativo (' + type(exc).__name__ + ').'
                self.migration = 'failed'
    def _connect(self):
        return psycopg2.connect(os.environ['DATABASE_URL'].strip(), connect_timeout=8)
    def _schema(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS vendacertaai_kv (
                    key TEXT PRIMARY KEY, value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                cur.execute('''CREATE TABLE IF NOT EXISTS vendacertaai_migrations (
                    version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
                cur.execute("INSERT INTO vendacertaai_migrations(version) VALUES (%s) ON CONFLICT (version) DO NOTHING", (self.VERSION,))
            conn.commit()
    def _migrate_files(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                for path in self.data_dir.glob('*.json'):
                    try: value = json.loads(path.read_text(encoding='utf-8'))
                    except Exception: continue
                    cur.execute('''INSERT INTO vendacertaai_kv(key,value) VALUES (%s,%s)
                                   ON CONFLICT (key) DO NOTHING''', (path.name, Json(value)))
            conn.commit()
    def _local(self, path, default):
        try:
            if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
        except Exception: pass
        return default
    def read(self, path, default):
        if self.mode != 'postgresql': return self._local(path, default)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT value FROM vendacertaai_kv WHERE key=%s', (path.name,))
                    row = cur.fetchone()
                    return row[0] if row else default
        except Exception as exc:
            self.mode = 'json-beta'
            self.reason = 'Falha de leitura PostgreSQL; fallback local beta ativo (' + type(exc).__name__ + ').'
            return self._local(path, default)
    def write(self, path, value):
        if self.mode != 'postgresql': return False
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO vendacertaai_kv(key,value,updated_at) VALUES (%s,%s,NOW())
                                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()''', (path.name, Json(value)))
                conn.commit()
            return True
        except Exception as exc:
            self.mode = 'json-beta'
            self.reason = 'Falha de gravação PostgreSQL; fallback local beta ativo (' + type(exc).__name__ + ').'
            return False
    def status(self):
        durable = self.mode == 'postgresql'
        return {'backend':'postgresql' if durable else 'json-beta', 'durable':durable,
                'database_url_configured':self.url_configured, 'migration':self.migration,
                'version':self.VERSION, 'fallback':not durable,
                'message': 'PostgreSQL durável ativo.' if durable else self.reason}

def storage_status():
    return STORAGE.status()

