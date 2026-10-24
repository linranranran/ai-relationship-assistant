"""Simulate immutable tree versions and active-version selections from a knowledge module."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.broker import Publisher
from reliable_ingestion.config import RuntimeConfig
from reliable_ingestion.errors import PermanentError
from reliable_ingestion.events import DocumentEvent


def main():
    parser = argparse.ArgumentParser(description='Publish schema2 version-tree events; revision orders operations')
    parser.add_argument('operation', choices=['create', 'activate', 'delete', 'replay'])
    parser.add_argument('--document-id')
    parser.add_argument('--revision', type=int, help='monotonic document operation revision, including activation')
    parser.add_argument('--version-id', help='immutable content version ID; never used to order operations')
    parser.add_argument('--parent-version-id')
    parser.add_argument('--version-label')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--event-file', type=Path)
    parser.add_argument('--tenant-id', default='default')
    parser.add_argument('--knowledge-base-id', default='default')
    parser.add_argument('--event-id')
    parser.add_argument('--prepare-only', action='store_true', help='save immutable object/event without RabbitMQ')
    args = parser.parse_args()
    config = RuntimeConfig.from_env()
    if args.operation == 'replay':
        if not args.event_file:
            parser.error('replay requires --event-file')
        try:
            event = DocumentEvent.parse(args.event_file.read_bytes())
        except (OSError, PermanentError):
            parser.error('event-file must contain a valid readable event')
        if event.data['schema_version'] != 2:
            parser.error('tree replay requires schema_version=2; use publish_event.py for schema1')
        output = args.event_file
    else:
        if args.document_id is None or args.revision is None:
            parser.error('document-id and revision are required')
        if args.operation != 'create' and any(value is not None for value in
                                              (args.file, args.parent_version_id, args.version_label)):
            parser.error('file, parent-version-id and version-label are only valid for create')
        if args.operation == 'delete' and args.version_id is not None:
            parser.error('delete removes the whole document and must omit version-id')
        if args.operation != 'delete' and args.version_id is None:
            parser.error('create/activate requires version-id')
        value = {
            'schema_version': 2, 'event_id': args.event_id or uuid.uuid4().hex,
            'event_type': {'create': 'document.version.created', 'activate': 'document.version.activated',
                           'delete': 'document.deleted'}[args.operation],
            'tenant_id': args.tenant_id, 'knowledge_base_id': args.knowledge_base_id,
            'document_id': args.document_id, 'document_revision': args.revision,
        }
        if args.operation != 'delete':
            value['version_id'] = args.version_id
        content = None
        if args.operation == 'create':
            if not args.file or not args.file.is_file():
                parser.error('create requires an existing --file')
            if config.source_mode != 'local':
                parser.error('file simulation requires RAG_OBJECT_SOURCE=local; replay prepared JSON for OSS')
            with args.file.open('rb') as stream:
                content = stream.read(config.max_object_bytes + 1)
            if len(content) > config.max_object_bytes:
                parser.error('file exceeds configured size limit')
            digest = hashlib.sha256(content).hexdigest()
            key = (f'{args.tenant_id}/{args.knowledge_base_id}/{args.document_id}/versions/'
                   f'{args.version_id}/{digest}-{args.file.name}')
            value.update(parent_version_id=args.parent_version_id, file_name=args.file.name,
                         object={'bucket': 'local-knowledge', 'key': key, 'sha256': digest})
            if args.version_label is not None:
                value['version_label'] = args.version_label
        try:
            event = DocumentEvent.parse(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        except (PermanentError, UnicodeError) as error:
            parser.error(str(error))
        output = config.root / 'published_events' / (event.data['event_id'] + '.json')
        # Detect event ID conflicts before writing another simulated source object.
        if output.exists():
            try:
                existing = DocumentEvent.parse(output.read_bytes())
            except PermanentError:
                parser.error('existing event file is incomplete or invalid; inspect it before retrying')
            if existing.fingerprint != event.fingerprint:
                parser.error('event-id already used for a different local event')
        if content is not None:
            destination = (config.local_root / key).resolve()
            if not destination.is_relative_to(config.local_root.resolve()):
                parser.error('unsafe destination path')
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open('xb') as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    parser.error('immutable object path already contains different bytes')
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            with output.open('xb') as stream:
                stream.write(event.to_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            try:
                previous = DocumentEvent.parse(output.read_bytes())
            except PermanentError:
                parser.error('existing event file is incomplete or invalid; inspect it before retrying')
            if previous.fingerprint != event.fingerprint:
                parser.error('event-id already used for a different local event')
    print(json.dumps({'event_id': event.data['event_id'], 'event_file': str(output),
                      'document_revision': event.data['document_revision'],
                      'version_id': event.data.get('version_id')}, ensure_ascii=False), flush=True)
    if not args.prepare_only:
        try:
            with Publisher(config) as publisher:
                publisher.publish(event.to_bytes(), event.data['event_id'])
        except Exception as error:
            print(f'Publish not confirmed ({type(error).__name__}); replay the saved event file.', file=sys.stderr)
            return 1
        print('RabbitMQ publisher confirm received; inspect jobs and versions for actual activation status.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
