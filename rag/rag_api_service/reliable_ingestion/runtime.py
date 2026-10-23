from contextlib import contextmanager
import logging
import os
import signal
import threading

from .broker import Consumer, Publisher, connection_parameters, declare_queues, relay_dead_letters
from .config import RuntimeConfig
from .store import Inbox
from .worker import Worker

logger = logging.getLogger(__name__)


@contextmanager
def writer_lock(path):
    """OS releases this lock on process termination; never use an expiring Redis lock here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    try:
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('another RAG worker already owns this data directory') from error
        yield
    finally:
        # Closing releases both Windows and POSIX locks, even on an exception.
        stream.close()


def run():
    import pika
    from .llama_backend import make_backend
    config = RuntimeConfig.from_env()
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with writer_lock(config.root / 'worker.lock'):
        inbox = Inbox(config.database, max_attempts=config.max_attempts, retry_base=config.retry_base)
        inbox.recover_interrupted()

        def index_loop():
            backend = None
            while not stop.is_set():
                try:
                    if backend is None:
                        backend = make_backend(config)
                    outcome = Worker(inbox, backend).run_once()
                    if outcome is None:
                        stop.wait(.5)
                except Exception as error:
                    logger.error('index worker unavailable error_type=%s', type(error).__name__)
                    # If SQL failed after a side effect, only this thread can be in-flight.
                    # Restore processing tasks before the next claim, including attempt limits.
                    try:
                        inbox.recover_interrupted()
                    except Exception:
                        pass
                    stop.wait(5)

        def dead_letter_loop():
            while not stop.is_set():
                try:
                    with Publisher(config) as publisher:
                        while not stop.is_set():
                            relay_dead_letters(inbox, publisher, limit=20)
                            publisher.connection.process_data_events(time_limit=1)
                            stop.wait(.5)
                except Exception as error:
                    logger.warning('dead-letter relay retry error_type=%s', type(error).__name__)
                    stop.wait(5)

        threads = [threading.Thread(target=index_loop, name='rag-indexer'),
                   threading.Thread(target=dead_letter_loop, name='rag-dead-relay')]
        for thread in threads:
            thread.start()
        try:
            while not stop.is_set():
                connection = None
                try:
                    connection = pika.BlockingConnection(connection_parameters(config.rabbit_url))
                    channel = connection.channel()
                    declare_queues(channel, config)
                    channel.basic_qos(prefetch_count=10)
                    channel.basic_consume(queue=config.queue, on_message_callback=Consumer(inbox).handle,
                                          auto_ack=False)
                    logger.info('RAG event consumer ready queue=%s', config.queue)
                    while not stop.is_set():
                        connection.process_data_events(time_limit=1)
                except Exception as error:
                    logger.warning('consumer reconnect error_type=%s', type(error).__name__)
                    stop.wait(5)
                finally:
                    if connection is not None and connection.is_open:
                        try:
                            connection.close()
                        except Exception:
                            pass
        finally:
            stop.set()
            # Finish the in-flight task or let OS termination recover it on next start.
            for thread in threads:
                thread.join()
