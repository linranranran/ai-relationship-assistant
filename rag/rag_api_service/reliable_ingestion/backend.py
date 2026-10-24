"""Version publication logic independent of external indexing clients."""
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

from .errors import PermanentError
from .events import canonical


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(directory):
    if os.name != 'nt':
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def embedding_signature(model_fingerprint, dimensions):
    return hashlib.sha256(canonical([model_fingerprint, dimensions]).encode()).hexdigest()


class VersionedBackend:
    def __init__(self, root, parser, embed, vector_store, mirror, lexical_builder,
                 model_fingerprint, dimensions, pipeline_version, batch_size=20):
        if dimensions < 1 or batch_size < 1:
            raise ValueError('dimensions and batch_size must be positive')
        self.root = Path(root)
        self.parser, self.embed = parser, embed
        self.vector_store, self.mirror, self.lexical_builder = vector_store, mirror, lexical_builder
        self.model_fingerprint, self.dimensions = model_fingerprint, dimensions
        self.pipeline_version, self.batch_size = pipeline_version, batch_size

    def generation_id(self, event):
        data = event.data
        content_version = event.version_id if event.is_versioned else data['document_version']
        identity = [event.scope, data['document_id'], content_version,
                    data['object']['sha256'], self.pipeline_version, self.model_fingerprint, self.dimensions]
        return hashlib.sha256(canonical(identity).encode()).hexdigest()

    def reuse(self, event, generation):
        if generation != self.generation_id(event):
            raise PermanentError('indexed version config changed; restore config or explicitly rebuild/migrate')
        rows = json.loads((self.root / 'generations' / f'{generation}.json').read_text(encoding='utf-8'))
        signature = embedding_signature(self.model_fingerprint, self.dimensions)
        if not rows or any(row['metadata']['generation'] != generation
                           or row['metadata']['embedding_config'] != signature for row in rows):
            raise PermanentError('invalid generation manifest or config')
        self.vector_store.verify(generation, [row['id'] for row in rows])
        self.mirror(rows)
        return generation

    def stage(self, event, inbox):
        data = event.data
        content_version = event.version_id if event.is_versioned else data['document_version']
        generation = self.generation_id(event)
        manifest_path = self.root / 'generations' / f'{generation}.json'
        if manifest_path.exists():
            # Completed immutable versions can be activated without downloading or re-embedding.
            return self.reuse(event, generation)
        parsed = self.parser(event)
        if not parsed or not any(node['text'].strip() for node in parsed):
            raise PermanentError('document contains no extractable text')
        nodes = []
        for ordinal, original in enumerate(parsed):
            if not original['text'].strip():
                continue
            node = {
                'id': f'{generation}:{ordinal}', 'text': original['text'],
                'embedding_text': original.get('embedding_text', original['text']),
                'metadata': dict(original.get('metadata', {})) | {
                    'tenant_id': data['tenant_id'], 'knowledge_base_id': data['knowledge_base_id'],
                    'document_id': data['document_id'],
                    'document_version': data.get('version_label', content_version),
                    'file_name': data['file_name'], 'object_key': data['object']['key'],
                    'bucket': data['object']['bucket'], 'generation': generation, 'chunk_ordinal': ordinal,
                    'embedding_config': embedding_signature(self.model_fingerprint, self.dimensions),
                },
            }
            if event.is_versioned:
                node['metadata'].update(version_id=event.version_id,
                                        version_label=data.get('version_label', event.version_id),
                                        creation_revision=event.revision)
                if data.get('parent_version_id') is not None:
                    node['metadata']['parent_version_id'] = data['parent_version_id']
            if data['object'].get('version_id'):
                node['metadata']['object_version_id'] = data['object']['version_id']
            nodes.append(node)
        for start in range(0, len(nodes), self.batch_size):
            batch = nodes[start:start + self.batch_size]
            missing = []
            for node in batch:
                node['cache_key'] = hashlib.sha256(canonical([
                    event.scope, self.model_fingerprint, self.dimensions,
                    self.pipeline_version, node['embedding_text']]).encode()).hexdigest()
                node['embedding'] = inbox.embedding(node['cache_key'])
                if node['embedding'] is None:
                    missing.append(node)
            if missing:
                vectors = self.embed([node['embedding_text'] for node in missing])
                if len(vectors) != len(missing):
                    raise PermanentError('embedding batch length mismatch')
                for node, vector in zip(missing, vectors):
                    self._validate_vector(vector)
                    vector = [float(value) for value in vector]
                    inbox.cache_embedding(node['cache_key'], vector)
                    node['embedding'] = vector
            for node in batch:
                self._validate_vector(node['embedding'])
            # Retried attempts overwrite the same deterministic physical node IDs.
            self.vector_store.upsert(batch)
        self.vector_store.verify(generation, [node['id'] for node in nodes])
        self.mirror(nodes)
        manifest_nodes = [{key: value for key, value in node.items()
                           if key not in {'embedding', 'cache_key'}} for node in nodes]
        write_json(manifest_path, manifest_nodes)
        return generation

    def _validate_vector(self, vector):
        if len(vector) != self.dimensions or any(
                not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
            raise PermanentError('invalid embedding dimension or non-finite value')

    def build_snapshot(self, event, generation, active):
        active = dict(active)
        if event.deleted:
            active.pop(event.data['document_id'], None)
        else:
            active[event.data['document_id']] = generation
        nodes = []
        for current in sorted(active.values()):
            nodes.extend(json.loads((self.root / 'generations' / f'{current}.json').read_text(encoding='utf-8')))
        signature = embedding_signature(self.model_fingerprint, self.dimensions)
        if any(node['metadata']['embedding_config'] != signature for node in nodes):
            raise PermanentError('cannot mix embedding models; migrate the knowledge base as a separate deployment')
        snapshot_id = uuid.uuid4().hex
        directory = self.root / 'snapshots' / snapshot_id
        directory.mkdir(parents=True)
        if nodes:
            self.lexical_builder(nodes, directory)
        write_json(directory / 'snapshot.json', {
            'schema_version': 1, 'scope': event.scope, 'generations': sorted(active.values()),
            'documents': active, 'nodes': nodes, 'embedding_config': signature,
        })
        # Flush every BM25 artifact before the SQL pointer can expose it.
        for artifact in directory.rglob('*'):
            if artifact.is_file():
                with artifact.open('r+b') as stream:
                    os.fsync(stream.fileno())
        sync_directory(directory)
        sync_directory(directory.parent)
        return snapshot_id

    def read_snapshot(self, snapshot_id):
        if len(snapshot_id) != 32 or any(char not in '0123456789abcdef' for char in snapshot_id):
            raise ValueError('invalid snapshot id')
        return json.loads((self.root / 'snapshots' / snapshot_id / 'snapshot.json').read_text(encoding='utf-8'))
