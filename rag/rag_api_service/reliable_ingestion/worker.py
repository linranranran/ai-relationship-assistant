import logging

from .events import DocumentEvent

logger = logging.getLogger(__name__)


class Worker:
    """Run under the runtime's single-writer process lock."""

    def __init__(self, inbox, backend):
        self.inbox, self.backend = inbox, backend

    def run_once(self):
        job = self.inbox.claim()
        if not job:
            return None
        try:
            event = DocumentEvent.parse(job['payload'].encode())
            generation = None if event.deleted else self.backend.stage(event, self.inbox)
            active = self.inbox.active_generations(event.scope)
            snapshot_id = self.backend.build_snapshot(event, generation, active)
            published = self.inbox.publish(job, snapshot_id, generation)
            result = 'succeeded' if published else 'superseded'
        except Exception as error:
            self.inbox.fail(job, error)
            result = self.inbox.job(job['id'])['status']
            logger.warning('index task event_id=%s attempt=%s status=%s error_type=%s',
                           job['id'], job['attempts'], result, type(error).__name__)
        else:
            logger.info('index task event_id=%s version=%s status=%s', job['id'], job['version'], result)
        return result
