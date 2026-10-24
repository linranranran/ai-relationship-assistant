from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import time
import uuid

from .errors import PermanentError
from .events import DocumentEvent
from . import version_catalog


class Inbox:
    """Single-host durable handoff, version CAS and retry/dead-letter outbox."""

    def __init__(self, path, max_attempts=4, retry_base=5, clock=time.time, jitter=random.random,
                 dependency_timeout=3600):
        if max_attempts < 1 or retry_base < 0 or dependency_timeout < 0:
            raise ValueError('invalid retry settings')
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts, self.retry_base = max_attempts, retry_base
        self.clock, self.jitter = clock, jitter
        self.dependency_timeout = dependency_timeout
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    scope TEXT NOT NULL, document_id TEXT NOT NULL, version INTEGER NOT NULL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_at REAL NOT NULL DEFAULT 0, last_error TEXT,
                    UNIQUE(scope, document_id, version));
                CREATE TABLE IF NOT EXISTS receipts (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, job_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS heads (
                    scope TEXT NOT NULL, document_id TEXT NOT NULL, desired_version INTEGER NOT NULL,
                    active_version INTEGER, generation TEXT, deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(scope, document_id));
                CREATE TABLE IF NOT EXISTS snapshots (scope TEXT PRIMARY KEY, id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dead_letters (
                    id TEXT PRIMARY KEY, body BLOB NOT NULL, error TEXT NOT NULL,
                    sent INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS embeddings (key TEXT PRIMARY KEY, vector TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, next_at);
            ''')
            version_catalog.install(db)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def accept(self, body):
        event = DocumentEvent.parse(body)
        if event.is_versioned:
            return version_catalog.accept(self, event)
        data, fingerprint = (event.data, event.fingerprint)
        with self.connection(write=True) as db:
            receipt = db.execute('SELECT * FROM receipts WHERE id=?', (data['event_id'],)).fetchone()
            if receipt:
                if receipt['fingerprint'] != fingerprint:
                    raise PermanentError('event_id reused with different content')
                return 'duplicate'
            if db.execute('SELECT 1 FROM version_heads WHERE scope=? AND document_id=?', (event.scope, data['document_id'])).fetchone():
                raise PermanentError('cannot mix schema1 and schema2 for the same document')
            existing = db.execute('SELECT * FROM jobs WHERE scope=? AND document_id=? AND version=?', (event.scope, data['document_id'], data['document_version'])).fetchone()
            if existing and existing['fingerprint'] != fingerprint:
                raise PermanentError('same document version has conflicting content')
            db.execute('INSERT INTO receipts VALUES (?,?,?)', (data['event_id'], fingerprint, existing['id'] if existing else data['event_id']))
            if existing:
                return 'duplicate'
            head = db.execute('SELECT desired_version FROM heads WHERE scope=? AND document_id=?', (event.scope, data['document_id'])).fetchone()
            status = 'superseded' if head and head[0] > data['document_version'] else 'pending'
            db.execute('INSERT INTO jobs (id,payload,fingerprint,scope,document_id,version,status) VALUES (?,?,?,?,?,?,?)', (data['event_id'], event.to_bytes().decode(), fingerprint, event.scope, data['document_id'], data['document_version'], status))
            db.execute('INSERT INTO heads(scope,document_id,desired_version) VALUES (?,?,?) ON CONFLICT(scope,document_id) DO UPDATE SET desired_version=MAX(desired_version,excluded.desired_version)', (event.scope, data['document_id'], data['document_version']))
            db.execute("UPDATE jobs SET status='superseded' WHERE scope=? AND document_id=? AND version<? AND status IN ('pending','retry')", (event.scope, data['document_id'], data['document_version']))
        return 'accepted'

    def claim(self):
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE status IN ('pending','retry','waiting') AND next_at<=? "
                             'ORDER BY rowid LIMIT 1', (self.clock(),)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='processing',attempts=attempts+1 WHERE id=?", (row['id'],))
            return dict(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())

    def _dead(self, db, job, error):
        db.execute("UPDATE jobs SET status='dead',last_error=? WHERE id=?", (error, job['id']))
        db.execute('INSERT INTO dead_letters(id,body,error) VALUES (?,?,?)',
                   (job['id'] + ':' + uuid.uuid4().hex, job['payload'].encode(), error))

    def recover_interrupted(self):
        # Only call while owning the process-wide writer file lock.
        with self.connection(write=True) as db:
            for row in db.execute("SELECT * FROM jobs WHERE status='processing'").fetchall():
                if row['attempts'] >= self.max_attempts:
                    self._dead(db, row, 'worker interrupted; attempts exhausted')
                else:
                    db.execute("UPDATE jobs SET status='retry',next_at=0,last_error=? WHERE id=?",
                               ('worker interrupted', row['id']))

    def fail(self, job, error):
        if DocumentEvent.parse(job['payload'].encode()).is_versioned:
            return version_catalog.fail(self, job, error)
        message = str(error)[:300] if isinstance(error, PermanentError) else type(error).__name__
        with self.connection(write=True) as db:
            head = db.execute('SELECT desired_version FROM heads WHERE scope=? AND document_id=?',
                              (job['scope'], job['document_id'])).fetchone()
            if head[0] > job['version']:
                db.execute("UPDATE jobs SET status='superseded',last_error=? WHERE id=?", (message, job['id']))
            elif isinstance(error, PermanentError) or job['attempts'] >= self.max_attempts:
                self._dead(db, job, message)
            else:
                delay = min(300, self.retry_base * 2**(job['attempts'] - 1)) * (1 + self.jitter() * .2)
                db.execute("UPDATE jobs SET status='retry',next_at=?,last_error=? WHERE id=?",
                           (self.clock() + delay, message, job['id']))

    def publish(self, job, snapshot_id, generation):
        if DocumentEvent.parse(job['payload'].encode()).is_versioned:
            return version_catalog.publish(self, job, snapshot_id, generation)
        with self.connection(write=True) as db:
            row = db.execute('SELECT desired_version FROM heads WHERE scope=? AND document_id=?',
                             (job['scope'], job['document_id'])).fetchone()
            state = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
            if row[0] != job['version'] or state[0] != 'processing':
                if state[0] == 'processing':
                    db.execute("UPDATE jobs SET status='superseded' WHERE id=?", (job['id'],))
                return False
            db.execute('UPDATE heads SET active_version=?,generation=?,deleted=? WHERE scope=? AND document_id=?',
                       (job['version'], generation, int(generation is None), job['scope'], job['document_id']))
            db.execute('INSERT INTO snapshots VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET id=excluded.id',
                       (job['scope'], snapshot_id))
            db.execute("UPDATE jobs SET status='succeeded',last_error=NULL WHERE id=?", (job['id'],))
        return True

    def active_generations(self, scope):
        with self.connection() as db:
            return {row['document_id']: row['generation'] for row in db.execute(
                'SELECT * FROM heads WHERE scope=? AND generation IS NOT NULL AND deleted=0', (scope,))}

    def snapshot(self, scope):
        with self.connection() as db:
            row = db.execute('SELECT id FROM snapshots WHERE scope=?', (scope,)).fetchone()
            return row[0] if row else None

    def jobs(self, limit=100):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?', (limit,))]

    def job(self, event_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (event_id,)).fetchone()
            return dict(row) if row else None

    def quarantine(self, body, error):
        key = 'invalid-' + hashlib.sha256(body).hexdigest()
        with self.connection(write=True) as db:
            db.execute('INSERT OR IGNORE INTO dead_letters(id,body,error) VALUES (?,?,?)',
                       (key, body, str(error)[:300]))

    def pending_dead_letters(self, limit=100):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM dead_letters WHERE sent=0 LIMIT ?', (limit,))]

    def mark_dead_letter_sent(self, message_id):
        with self.connection(write=True) as db:
            db.execute('UPDATE dead_letters SET sent=1 WHERE id=?', (message_id,))

    def retry(self, event_id):
        existing = self.job(event_id)
        if existing and DocumentEvent.parse(existing['payload'].encode()).is_versioned:
            return version_catalog.retry(self, event_id)
        with self.connection(write=True) as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (event_id,)).fetchone()
            if not job or job['status'] != 'dead':
                raise PermanentError('only a dead task can be replayed')
            head = db.execute('SELECT desired_version FROM heads WHERE scope=? AND document_id=?',
                              (job['scope'], job['document_id'])).fetchone()
            if head[0] != job['version']:
                raise PermanentError('newer document version exists; cannot replay old task')
            db.execute("UPDATE jobs SET status='retry',attempts=0,next_at=0,last_error=NULL WHERE id=?", (event_id,))
            # Existing dead-letter records remain an audit record, including pending delivery.

    def embedding(self, key):
        with self.connection() as db:
            row = db.execute('SELECT vector FROM embeddings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def version_source(self, event):
        return version_catalog.source(self, event)

    def version_ready(self, event, generation):
        return version_catalog.ready(self, event, generation)

    def version_generation(self, event):
        return version_catalog.generation(self, event)

    def version_should_publish(self, job):
        return version_catalog.should_publish(self, job)

    def versions(self, scope, document_id):
        return version_catalog.versions(self, scope, document_id)

    def cache_embedding(self, key, vector):
        with self.connection(write=True) as db:
            db.execute('INSERT INTO embeddings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET vector=excluded.vector',
                       (key, json.dumps(vector, allow_nan=False)))
