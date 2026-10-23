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
        except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
            raise PermanentError('event must be UTF-8 JSON') from exc
        if not isinstance(value, dict):
            raise PermanentError('event must be an object')
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
