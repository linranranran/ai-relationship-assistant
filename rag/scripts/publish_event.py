"""Simulate the knowledge-base producer. Run from rag/ with the RAG Python env."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'rag_api_service'))
from reliable_ingestion.broker import Publisher
from reliable_ingestion.config import RuntimeConfig
from reliable_ingestion.events import DocumentEvent


def main():
    parser = argparse.ArgumentParser(description='Publish knowledge-base file events or replay exact JSON')
    parser.add_argument('operation', choices=['create', 'update', 'delete', 'replay'])
    parser.add_argument('--document-id')
    parser.add_argument('--version', type=int)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--event-file', type=Path)
    parser.add_argument('--tenant-id', default='default')
    parser.add_argument('--knowledge-base-id', default='default')
    parser.add_argument('--event-id', default=None)
    parser.add_argument('--prepare-only', action='store_true', help='save event without connecting to RabbitMQ')
    args = parser.parse_args()
    config = RuntimeConfig.from_env()
    if args.operation == 'replay':
        if not args.event_file:
            parser.error('replay requires --event-file')
        event = DocumentEvent.parse(args.event_file.read_bytes())
        output = args.event_file
    else:
        if args.document_id is None or args.version is None:
            parser.error('document-id and version are required')
        value = {
            'schema_version': 1, 'event_id': args.event_id or uuid.uuid4().hex,
            'event_type': {'create': 'document.created', 'update': 'document.updated',
                           'delete': 'document.deleted'}[args.operation],
            'tenant_id': args.tenant_id, 'knowledge_base_id': args.knowledge_base_id,
            'document_id': args.document_id, 'document_version': args.version,
        }
        if args.operation != 'delete':
            if not args.file or not args.file.is_file():
                parser.error('create/update requires an existing --file')
            if config.source_mode != 'local':
                parser.error('file simulation requires RAG_OBJECT_SOURCE=local; use replay for OSS JSON events')
            content = args.file.read_bytes()
            if len(content) > config.max_object_bytes:
                parser.error('file exceeds configured size limit')
            digest = hashlib.sha256(content).hexdigest()
            key = f'{args.tenant_id}/{args.knowledge_base_id}/{args.document_id}/{args.version}/{digest}-{args.file.name}'
            value['file_name'] = args.file.name
            value['object'] = {'bucket': 'local-knowledge', 'key': key, 'sha256': digest}
        event = DocumentEvent.parse(json.dumps(value, ensure_ascii=False).encode())
        if args.operation != 'delete':
            destination = (config.local_root / key).resolve()
            if not destination.is_relative_to(config.local_root.resolve()):
                parser.error('unsafe destination path')
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                with destination.open('xb') as stream:
                    stream.write(content)
            except FileExistsError:
                if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                    parser.error('immutable object path already contains different bytes')
        output = config.root / 'published_events' / (event.data['event_id'] + '.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and DocumentEvent.parse(output.read_bytes()).fingerprint != event.fingerprint:
            parser.error('event-id already used for a different local event')
        output.write_bytes(event.to_bytes())
    print(json.dumps({'event_id': event.data['event_id'], 'event_file': str(output),
                      'document_version': event.data['document_version']}, ensure_ascii=False), flush=True)
    if not args.prepare_only:
        try:
            with Publisher(config) as publisher:
                publisher.publish(event.to_bytes(), event.data['event_id'])
        except Exception as error:
            print(f'Publish not confirmed ({type(error).__name__}); replay the saved event file.', file=sys.stderr)
            return 1
        print('RabbitMQ publisher confirm received; inspect Inbox for indexing status.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
