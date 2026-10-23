import argparse
import json

from .config import RuntimeConfig
from .store import Inbox


def main():
    parser = argparse.ArgumentParser(description='Inspect/replay durable RAG indexing jobs')
    sub = parser.add_subparsers(dest='command', required=True)
    jobs = sub.add_parser('jobs')
    jobs.add_argument('--limit', type=int, default=20)
    retry = sub.add_parser('retry')
    retry.add_argument('--event-id', required=True)
    sub.add_parser('dead')
    args = parser.parse_args()
    inbox = Inbox(RuntimeConfig.from_env().database)
    if args.command == 'jobs':
        rows = [{key: value for key, value in row.items() if key not in {'payload', 'fingerprint'}}
                for row in inbox.jobs(max(1, min(args.limit, 200)))]
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    elif args.command == 'retry':
        inbox.retry(args.event_id)
        print('Task scheduled for replay with the same event ID and document version.')
    else:
        rows = [{'dead_id': row['id'], 'error': row['error']} for row in inbox.pending_dead_letters()]
        print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
