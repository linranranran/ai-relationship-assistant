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
            if event.is_versioned:
                result = self._run_tree(job, event)
            else:
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

    def _run_tree(self, job, event):
        created = event.data['event_type'] == 'document.version.created'
        if not created and not self.inbox.version_should_publish(job):
            self.inbox.publish(job, None, None)
            return self.inbox.job(job['id'])['status']
        generation = None
        if not event.deleted:
            source = self.inbox.version_source(event)
            ready_generation = self.inbox.version_generation(source)
            generation = (self.backend.reuse(source, ready_generation) if ready_generation
                          else self.backend.stage(source, self.inbox))
            self.inbox.version_ready(source, generation)
        snapshot_id = None
        if self.inbox.version_should_publish(job):
            active = self.inbox.active_generations(event.scope)
            snapshot_id = self.backend.build_snapshot(event, generation, active)
        # Recheck the selection in the same transaction that publishes. Old creations
        # still finish as archived; old control events cannot move the active pointer.
        self.inbox.publish(job, snapshot_id, generation)
        return self.inbox.job(job['id'])['status']
