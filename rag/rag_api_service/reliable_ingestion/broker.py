"""Short AMQP callbacks. No parsing PDFs or model requests on the IO thread."""
import logging

from .errors import PermanentError

logger = logging.getLogger(__name__)


class Consumer:
    def __init__(self, inbox):
        self.inbox = inbox

    def handle(self, channel, method, properties, body):
        try:
            status = self.inbox.accept(body)
        except PermanentError as error:
            self.inbox.quarantine(body, error)
            status = 'quarantined'
        # If accept/quarantine cannot commit, propagate and close the connection.
        # RabbitMQ requeues the unacknowledged delivery after disconnect.
        channel.basic_ack(delivery_tag=method.delivery_tag)
        logger.info('event delivery tag=%s status=%s', method.delivery_tag, status)


def relay_dead_letters(inbox, publisher, limit=100):
    count = 0
    for row in inbox.pending_dead_letters(limit):
        publisher.publish_dead(row['body'], row['id'], row['error'])
        inbox.mark_dead_letter_sent(row['id'])
        count += 1
    return count


def connection_parameters(url):
    import pika
    parameters = pika.URLParameters(url)
    parameters.heartbeat = 60
    parameters.blocked_connection_timeout = 30
    parameters.socket_timeout = 10
    parameters.stack_timeout = 20
    parameters.connection_attempts = 1
    return parameters


def declare_queues(channel, config):
    channel.queue_declare(queue=config.queue, durable=True,
                          arguments={'x-queue-type': 'quorum', 'x-delivery-limit': -1})
    channel.queue_declare(queue=config.dead_queue, durable=True,
                          arguments={'x-queue-type': 'quorum'})


class Publisher:
    """Use on a single thread; mandatory + publisher confirms or an exception."""

    def __init__(self, config):
        import pika
        self.config = config
        self.connection = pika.BlockingConnection(connection_parameters(config.rabbit_url))
        try:
            self.channel = self.connection.channel()
            declare_queues(self.channel, config)
            self.channel.confirm_delivery()
        except BaseException:
            self.close()
            raise

    def publish(self, body, message_id, queue=None, headers=None):
        import pika
        self.channel.basic_publish(
            exchange='', routing_key=queue or self.config.queue, body=body, mandatory=True,
            properties=pika.BasicProperties(content_type='application/json', delivery_mode=2,
                                            message_id=message_id, headers=headers or {}))

    def publish_dead(self, body, message_id, error):
        self.publish(body, message_id, queue=self.config.dead_queue,
                     headers={'x-rag-error': error[:300], 'x-rag-dead-id': message_id})

    def close(self):
        if self.connection.is_open:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
