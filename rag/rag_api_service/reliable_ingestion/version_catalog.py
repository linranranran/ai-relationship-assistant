"""Immutable version archives and independently ordered active-version choices.

The knowledge service owns version IDs and the monotonically increasing control
revision. IDs describe the tree; only revisions order user choices. All SQL
mutations here use Inbox's existing durable transaction boundary.
"""
from .errors import DependencyPending, PermanentError
from .events import DocumentEvent


CREATED = 'document.version.created'
ACTIVATED = 'document.version.activated'


def install(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS document_versions (
            scope TEXT NOT NULL, document_id TEXT NOT NULL, version_id TEXT NOT NULL,
            parent_version_id TEXT, version_label TEXT, creation_revision INTEGER NOT NULL,
            payload TEXT NOT NULL, fingerprint TEXT NOT NULL, generation TEXT,
            PRIMARY KEY(scope, document_id, version_id));
        CREATE INDEX IF NOT EXISTS versions_parent
            ON document_versions(scope, document_id, parent_version_id);
        CREATE TABLE IF NOT EXISTS version_heads (
            scope TEXT NOT NULL, document_id TEXT NOT NULL,
            latest_version_id TEXT, latest_creation_revision INTEGER NOT NULL DEFAULT 0,
            desired_version_id TEXT, desired_revision INTEGER NOT NULL DEFAULT 0,
            active_version_id TEXT, active_revision INTEGER,
            desired_deleted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(scope, document_id));
        CREATE TABLE IF NOT EXISTS version_waits (
            job_id TEXT PRIMARY KEY, first_since REAL NOT NULL);
    ''')


def _head(db, scope, document_id):
    return db.execute('SELECT * FROM version_heads WHERE scope=? AND document_id=?',
                      (scope, document_id)).fetchone()


def _version(db, scope, document_id, version_id):
    return db.execute('SELECT * FROM document_versions '
                      'WHERE scope=? AND document_id=? AND version_id=?',
                      (scope, document_id, version_id)).fetchone()


def _validate_parent(db, event):
    data = event.data
    parent = data.get('parent_version_id')
    if parent == event.version_id:
        raise PermanentError('a version cannot be its own parent')
    if parent is None:
        other_root = db.execute('SELECT 1 FROM document_versions WHERE scope=? '
                                'AND document_id=? AND parent_version_id IS NULL',
                                (event.scope, data['document_id'])).fetchone()
        if other_root:
            raise PermanentError('a document version tree can have only one root')
    else:
        known_parent = _version(db, event.scope, data['document_id'], parent)
        if known_parent and known_parent['creation_revision'] >= event.revision:
            raise PermanentError('parent must have an earlier creation revision')
    # A child may arrive first. Check both ends when its missing parent arrives.
    invalid_child = db.execute('SELECT 1 FROM document_versions WHERE scope=? AND document_id=? '
                               'AND parent_version_id=? AND creation_revision<=?',
                               (event.scope, data['document_id'], event.version_id,
                                event.revision)).fetchone()
    if invalid_child:
        raise PermanentError('parent must have an earlier creation revision than its children')


def accept(inbox, event):
    data, fingerprint = event.data, event.fingerprint
    scope, document_id = event.scope, data['document_id']
    created = data['event_type'] == CREATED
    with inbox.connection(write=True) as db:
        receipt = db.execute('SELECT * FROM receipts WHERE id=?', (data['event_id'],)).fetchone()
        if receipt:
            if receipt['fingerprint'] != fingerprint:
                raise PermanentError('event_id reused with different content')
            return 'duplicate'

        head = _head(db, scope, document_id)
        aggregate = db.execute('SELECT * FROM heads WHERE scope=? AND document_id=?',
                               (scope, document_id)).fetchone()
        if aggregate and head is None:
            raise PermanentError('schema1 and schema2 cannot share a logical document; use an explicit migration')
        existing = db.execute('SELECT * FROM jobs WHERE scope=? AND document_id=? AND version=?',
                              (scope, document_id, event.revision)).fetchone()
        if existing and existing['fingerprint'] != fingerprint:
            raise PermanentError('same document revision has conflicting content')
        if existing:
            db.execute('INSERT INTO receipts VALUES (?,?,?)',
                       (data['event_id'], fingerprint, existing['id']))
            return 'duplicate'

        if created:
            current = _version(db, scope, document_id, event.version_id)
            if current:
                # A version's creation revision, parent, object and metadata are immutable.
                if current['fingerprint'] != fingerprint:
                    raise PermanentError('version_id reused with different immutable content')
            else:
                _validate_parent(db, event)
                db.execute('INSERT INTO document_versions '
                           '(scope,document_id,version_id,parent_version_id,version_label,'
                           'creation_revision,payload,fingerprint) VALUES (?,?,?,?,?,?,?,?)',
                           (scope, document_id, event.version_id, data.get('parent_version_id'),
                            data.get('version_label'), event.revision, event.to_bytes().decode(), fingerprint))

        desired_revision = head['desired_revision'] if head else 0
        if (data['event_type'] == ACTIVATED and head and head['desired_deleted']
                and event.revision > desired_revision):
            raise PermanentError('deleted document requires a new created version before activation')
        if event.revision == desired_revision:
            raise PermanentError('document revision already selects another operation')

        status = 'pending' if created or event.revision > desired_revision else 'superseded'
        db.execute('INSERT INTO receipts VALUES (?,?,?)', (data['event_id'], fingerprint, data['event_id']))
        db.execute('INSERT INTO jobs (id,payload,fingerprint,scope,document_id,version,status) '
                   'VALUES (?,?,?,?,?,?,?)',
                   (data['event_id'], event.to_bytes().decode(), fingerprint, scope, document_id,
                    event.revision, status))
        db.execute('INSERT INTO version_heads(scope,document_id) VALUES (?,?) '
                   'ON CONFLICT(scope,document_id) DO NOTHING', (scope, document_id))
        db.execute('INSERT INTO heads(scope,document_id,desired_version) VALUES (?,?,?) '
                   'ON CONFLICT(scope,document_id) DO NOTHING', (scope, document_id, event.revision))
        if created and (not head or event.revision > head['latest_creation_revision']):
            db.execute('UPDATE version_heads SET latest_version_id=?,latest_creation_revision=? '
                       'WHERE scope=? AND document_id=?',
                       (event.version_id, event.revision, scope, document_id))
        if event.revision > desired_revision:
            db.execute('UPDATE version_heads SET desired_version_id=?,desired_revision=?,desired_deleted=? '
                       'WHERE scope=? AND document_id=?',
                       (event.version_id, event.revision, int(event.deleted), scope, document_id))
            db.execute('UPDATE heads SET desired_version=? WHERE scope=? AND document_id=?',
                       (event.revision, scope, document_id))
            # Archives remain valuable even when a later user choice selects another version.
            # Only obsolete selection/delete jobs can be skipped before execution.
            older = db.execute("SELECT id,payload FROM jobs WHERE scope=? AND document_id=? AND version<? "
                               "AND status IN ('pending','retry','waiting')",
                               (scope, document_id, event.revision)).fetchall()
            for row in older:
                if DocumentEvent.parse(row['payload'].encode()).data['event_type'] != CREATED:
                    db.execute("UPDATE jobs SET status='superseded' WHERE id=?", (row['id'],))
                    db.execute('DELETE FROM version_waits WHERE job_id=?', (row['id'],))
    return 'accepted'


def source(inbox, event):
    with inbox.connection() as db:
        row = _version(db, event.scope, event.data['document_id'], event.version_id)
        if row is None:
            raise DependencyPending('target version creation event has not arrived')
        return DocumentEvent.parse(row['payload'].encode())


def ready(inbox, source_event, generation):
    with inbox.connection(write=True) as db:
        row = _version(db, source_event.scope, source_event.data['document_id'], source_event.version_id)
        if row is None or row['fingerprint'] != source_event.fingerprint:
            raise PermanentError('cannot mark an unknown or changed version ready')
        db.execute('UPDATE document_versions SET generation=? '
                   'WHERE scope=? AND document_id=? AND version_id=?',
                   (generation, source_event.scope, source_event.data['document_id'], source_event.version_id))
        waiting = db.execute("SELECT id,payload FROM jobs WHERE scope=? AND document_id=? AND status='waiting'",
                             (source_event.scope, source_event.data['document_id'])).fetchall()
        for job in waiting:
            if DocumentEvent.parse(job['payload'].encode()).version_id == source_event.version_id:
                db.execute('UPDATE jobs SET next_at=0 WHERE id=?', (job['id'],))


def generation(inbox, event):
    with inbox.connection() as db:
        row = _version(db, event.scope, event.data['document_id'], event.version_id)
        return row['generation'] if row else None


def _selection_matches(head, event):
    return bool(head and head['desired_revision'] == event.revision
                and head['desired_version_id'] == event.version_id
                and bool(head['desired_deleted']) == event.deleted)


def should_publish(inbox, job):
    event = DocumentEvent.parse(job['payload'].encode())
    with inbox.connection() as db:
        state = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
        return bool(state and state['status'] == 'processing'
                    and _selection_matches(_head(db, job['scope'], job['document_id']), event))


def publish(inbox, job, snapshot_id, generation):
    event = DocumentEvent.parse(job['payload'].encode())
    with inbox.connection(write=True) as db:
        state = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not state or state['status'] != 'processing':
            return False
        head = _head(db, job['scope'], job['document_id'])
        if event.data['event_type'] == CREATED:
            archive = _version(db, job['scope'], job['document_id'], event.version_id)
            if archive is None or archive['generation'] is None or archive['generation'] != generation:
                raise PermanentError('created version is not ready for archive completion')
        if not _selection_matches(head, event):
            status = 'succeeded' if event.data['event_type'] == CREATED else 'superseded'
            db.execute('UPDATE jobs SET status=?,last_error=NULL WHERE id=?', (status, job['id']))
            db.execute('DELETE FROM version_waits WHERE job_id=?', (job['id'],))
            return False
        if not event.deleted:
            row = _version(db, job['scope'], job['document_id'], event.version_id)
            if row is None or row['generation'] is None or row['generation'] != generation:
                raise PermanentError('selected version is not ready for publication')
        elif generation is not None:
            raise PermanentError('delete publication must not select a generation')
        if not snapshot_id:
            raise PermanentError('publication requires a complete knowledge-base snapshot')
        db.execute('UPDATE heads SET active_version=?,generation=?,deleted=? WHERE scope=? AND document_id=?',
                   (event.revision, generation, int(event.deleted), job['scope'], job['document_id']))
        db.execute('UPDATE version_heads SET active_version_id=?,active_revision=? WHERE scope=? AND document_id=?',
                   (event.version_id, event.revision, job['scope'], job['document_id']))
        db.execute('INSERT INTO snapshots VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET id=excluded.id',
                   (job['scope'], snapshot_id))
        db.execute("UPDATE jobs SET status='succeeded',last_error=NULL WHERE id=?", (job['id'],))
        db.execute('DELETE FROM version_waits WHERE job_id=?', (job['id'],))
    return True


def fail(inbox, job, error):
    event = DocumentEvent.parse(job['payload'].encode())
    message = str(error)[:300] if isinstance(error, (PermanentError, DependencyPending)) else type(error).__name__
    with inbox.connection(write=True) as db:
        state = db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()
        if not state or state['status'] != 'processing':
            return
        head = _head(db, job['scope'], job['document_id'])
        if event.data['event_type'] != CREATED and not _selection_matches(head, event):
            db.execute("UPDATE jobs SET status='superseded',last_error=? WHERE id=?", (message, job['id']))
            db.execute('DELETE FROM version_waits WHERE job_id=?', (job['id'],))
        elif isinstance(error, DependencyPending):
            now = inbox.clock()
            db.execute('INSERT INTO version_waits VALUES (?,?) ON CONFLICT(job_id) DO NOTHING', (job['id'], now))
            first = db.execute('SELECT first_since FROM version_waits WHERE job_id=?', (job['id'],)).fetchone()[0]
            db.execute('UPDATE jobs SET attempts=MAX(0,attempts-1) WHERE id=?', (job['id'],))
            if now - first >= inbox.dependency_timeout:
                inbox._dead(db, job, 'target version creation did not arrive before dependency timeout')
            else:
                db.execute("UPDATE jobs SET status='waiting',next_at=?,last_error=? WHERE id=?",
                           (now + 5, message, job['id']))
        elif isinstance(error, PermanentError) or job['attempts'] >= inbox.max_attempts:
            inbox._dead(db, job, message)
        else:
            delay = min(300, inbox.retry_base * 2**(job['attempts'] - 1)) * (1 + inbox.jitter() * .2)
            db.execute("UPDATE jobs SET status='retry',next_at=?,last_error=? WHERE id=?",
                       (inbox.clock() + delay, message, job['id']))


def retry(inbox, event_id):
    with inbox.connection(write=True) as db:
        job = db.execute('SELECT * FROM jobs WHERE id=?', (event_id,)).fetchone()
        if not job or job['status'] != 'dead':
            raise PermanentError('only a dead task can be replayed')
        event = DocumentEvent.parse(job['payload'].encode())
        head = _head(db, job['scope'], job['document_id'])
        if event.data['event_type'] != CREATED and not _selection_matches(head, event):
            raise PermanentError('newer document selection exists; cannot replay old selection')
        db.execute("UPDATE jobs SET status='retry',attempts=0,next_at=0,last_error=NULL WHERE id=?", (event_id,))
        db.execute('DELETE FROM version_waits WHERE job_id=?', (event_id,))


def versions(inbox, scope, document_id):
    with inbox.connection() as db:
        head = _head(db, scope, document_id)
        desired_job = (db.execute('SELECT id,status,last_error FROM jobs '
                                  'WHERE scope=? AND document_id=? AND version=?',
                                  (scope, document_id, head['desired_revision'])).fetchone() if head else None)
        aggregate = db.execute('SELECT deleted FROM heads WHERE scope=? AND document_id=?',
                               (scope, document_id)).fetchone()
        rows = db.execute('SELECT * FROM document_versions WHERE scope=? AND document_id=? '
                          'ORDER BY creation_revision,version_id', (scope, document_id)).fetchall()
        known = {row['version_id'] for row in rows}
        deleted = bool(aggregate and aggregate['deleted'])
        output = []
        for row in rows:
            job = db.execute('SELECT status FROM jobs WHERE scope=? AND document_id=? AND version=?',
                             (scope, document_id, row['creation_revision'])).fetchone()
            output.append({
                'version_id': row['version_id'], 'parent_version_id': row['parent_version_id'],
                'version_label': row['version_label'], 'creation_revision': row['creation_revision'],
                'index_status': 'ready' if row['generation'] is not None else 'pending',
                'job_status': job['status'] if job else None, 'generation': row['generation'],
                'parent_known': row['parent_version_id'] is None or row['parent_version_id'] in known,
                'is_active': bool(head and head['active_version_id'] == row['version_id'] and not deleted),
            })
        return {
            'document_id': document_id,
            'latest_version_id': head['latest_version_id'] if head else None,
            'desired_version_id': head['desired_version_id'] if head else None,
            'active_version_id': head['active_version_id'] if head else None,
            'desired_revision': head['desired_revision'] if head else None,
            'active_revision': head['active_revision'] if head else None,
            'desired_event_id': desired_job['id'] if desired_job else None,
            'desired_job_status': desired_job['status'] if desired_job else None,
            'desired_error': desired_job['last_error'] if desired_job else None,
            'deleted': deleted, 'desired_deleted': bool(head and head['desired_deleted']),
            'versions': output,
        }
