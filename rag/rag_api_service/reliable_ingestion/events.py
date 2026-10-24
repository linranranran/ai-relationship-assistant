from dataclasses import dataclass
import hashlib
import json
import re

from .errors import PermanentError


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class DocumentEvent:
    data: dict

    @classmethod
    def parse(cls, body: bytes):
        if len(body) > 1024 * 1024:
            raise PermanentError('event exceeds 1 MiB')
        try:
            value = json.loads(body.decode('utf-8'))
            # JSON escapes may contain lone surrogates even though the wire bytes are UTF-8.
            # Reject them here so fingerprint/persistence cannot turn them into endless redeliveries.
            canonical(value).encode('utf-8')
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            raise PermanentError('event must be UTF-8 JSON') from exc
        if not isinstance(value, dict):
            raise PermanentError('event must be an object')
        if type(value.get('schema_version')) is int and value['schema_version'] == 2:
            return cls._parse_tree(value)
        allowed = {'schema_version', 'event_id', 'event_type', 'tenant_id',
                   'knowledge_base_id', 'document_id', 'document_version',
                   'file_name', 'object', 'trace_id', 'occurred_at', 'content_type'}
        if value.keys() - allowed:
            raise PermanentError('unknown event fields')
        if type(value.get('schema_version')) is not int or value['schema_version'] != 1:
            raise PermanentError('unsupported schema_version')
        for field in ('event_id', 'tenant_id', 'knowledge_base_id', 'document_id'):
            if not isinstance(value.get(field), str) or not re.fullmatch(
                    r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value[field]):
                raise PermanentError(f'invalid {field}')
        if value.get('event_type') not in ('document.created', 'document.updated', 'document.deleted'):
            raise PermanentError('unsupported event_type')
        version = value.get('document_version')
        if type(version) is not int or not 1 <= version <= 2**63 - 1:
            raise PermanentError('document_version must be a positive 64-bit integer')
        for field in ('trace_id', 'occurred_at', 'content_type'):
            if field in value and (not isinstance(value[field], str) or len(value[field]) > 512):
                raise PermanentError(f'invalid {field}')
        if 'file_name' in value or value['event_type'] != 'document.deleted':
            name = value.get('file_name')
            if not isinstance(name, str) or not name or len(name) > 255 or '/' in name or '\\' in name:
                raise PermanentError('file_name must be a base name')
        if value['event_type'] != 'document.deleted':
            obj = value.get('object')
            if not isinstance(obj, dict) or obj.keys() - {'bucket', 'key', 'version_id', 'sha256'}:
                raise PermanentError('invalid object reference')
            for field in ('bucket', 'key', 'sha256'):
                if not isinstance(obj.get(field), str) or not obj[field]:
                    raise PermanentError(f'missing object.{field}')
            if len(obj['key']) > 2048 or not re.fullmatch(r'[a-f0-9]{64}', obj['sha256']):
                raise PermanentError('invalid object key or sha256')
            if 'version_id' in obj and (not isinstance(obj['version_id'], str) or not obj['version_id']):
                raise PermanentError('invalid object.version_id')
        elif 'object' in value:
            raise PermanentError('delete event must omit object')
        return cls(value)

    @classmethod
    def _parse_tree(cls, value):
        common = {'schema_version', 'event_id', 'event_type', 'tenant_id', 'knowledge_base_id',
                  'document_id', 'document_revision', 'trace_id', 'occurred_at'}
        kind = value.get('event_type')
        if kind == 'document.version.created':
            allowed = common | {'version_id', 'parent_version_id', 'version_label',
                                'file_name', 'object', 'content_type'}
        elif kind == 'document.version.activated':
            allowed = common | {'version_id'}
        elif kind == 'document.deleted':
            allowed = common
        else:
            raise PermanentError('unsupported schema2 event_type')
        if value.keys() - allowed:
            raise PermanentError('unknown version event fields')
        # Reuse validation for identities, revision and immutable file/object fields.
        base = {key: item for key, item in value.items()
                if key in common or key in {'file_name', 'object', 'content_type'}}
        base['schema_version'] = 1
        base['event_type'] = 'document.created' if kind == 'document.version.created' else 'document.deleted'
        base['document_version'] = base.pop('document_revision', None)
        cls.parse(canonical(base).encode())
        normalized = dict(value)

        def version_identity(item):
            if type(item) is int and 0 <= item <= 2**63 - 1:
                item = str(item)
            if not isinstance(item, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', item):
                raise PermanentError('invalid version identity')
            return item

        if kind != 'document.deleted':
            normalized['version_id'] = version_identity(value.get('version_id'))
        if kind == 'document.version.created':
            parent = value.get('parent_version_id')
            normalized['parent_version_id'] = None if parent is None else version_identity(parent)
            if normalized['parent_version_id'] == normalized['version_id']:
                raise PermanentError('a version cannot be its own parent')
            if 'version_label' in value and (not isinstance(value['version_label'], str)
                                           or not value['version_label'] or len(value['version_label']) > 128):
                raise PermanentError('invalid version_label')
        return cls(normalized)

    @property
    def is_versioned(self):
        return self.data['schema_version'] == 2

    @property
    def revision(self):
        return self.data['document_revision'] if self.is_versioned else self.data['document_version']

    @property
    def version_id(self):
        return self.data.get('version_id') if self.is_versioned else None

    @property
    def scope(self):
        return canonical([self.data['tenant_id'], self.data['knowledge_base_id']])

    @property
    def fingerprint(self):
        semantic = {k: v for k, v in self.data.items()
                    if k not in {'event_id', 'trace_id', 'occurred_at'}}
        return hashlib.sha256(canonical(semantic).encode()).hexdigest()

    @property
    def deleted(self):
        return self.data['event_type'] == 'document.deleted'

    def to_bytes(self):
        return canonical(self.data).encode('utf-8')
