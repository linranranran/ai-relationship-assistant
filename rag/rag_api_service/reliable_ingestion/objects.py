from contextlib import contextmanager
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import uuid

from .errors import PermanentError


class ObjectSource:
    def __init__(self, mode, local_root, staging_root, max_bytes=100 * 1024 * 1024,
                 oss_endpoint=None, oss_bucket=None, key_prefix=''):
        if mode not in {'local', 'oss'}:
            raise ValueError('object source must be local or oss')
        self.mode, self.local_root = mode, Path(local_root).resolve()
        self.staging_root, self.max_bytes = Path(staging_root), max_bytes
        self.oss_endpoint, self.oss_bucket, self.key_prefix = oss_endpoint, oss_bucket, key_prefix

    @contextmanager
    def materialize(self, event):
        obj = event.data['object']
        key = obj['key']
        # OSS keys are POSIX paths; disallow Windows drive/UNC forms in local simulation.
        if '\\' in key or ':' in key or PurePosixPath(key).is_absolute() or '..' in PurePosixPath(key).parts:
            raise PermanentError('unsafe object key')
        if self.key_prefix and not key.startswith(self.key_prefix):
            raise PermanentError('object key outside configured prefix')
        directory = self.staging_root / uuid.uuid4().hex
        directory.mkdir(parents=True)
        destination = directory / ('source' + Path(event.data['file_name']).suffix.lower())
        source = None
        try:
            if self.mode == 'local':
                path = (self.local_root / key).resolve()
                if not path.is_relative_to(self.local_root):
                    raise PermanentError('object path escapes configured local root')
                source = path.open('rb')
            else:
                import oss2
                from oss2.credentials import EnvironmentVariableCredentialsProvider
                if obj['bucket'] != self.oss_bucket:
                    raise PermanentError('object bucket is not allowed')
                auth = oss2.ProviderAuth(EnvironmentVariableCredentialsProvider())
                bucket = oss2.Bucket(auth, self.oss_endpoint, self.oss_bucket, connect_timeout=30)
                params = {'versionId': obj['version_id']} if obj.get('version_id') else None
                source = bucket.get_object(key, params=params)
            digest, length = hashlib.sha256(), 0
            with destination.open('wb') as stream:
                while True:
                    data = source.read(1024 * 1024)
                    if not data:
                        break
                    length += len(data)
                    if length > self.max_bytes:
                        raise PermanentError('object exceeds configured size limit')
                    digest.update(data)
                    stream.write(data)
            if digest.hexdigest() != obj['sha256']:
                raise PermanentError('object checksum mismatch; use immutable keys or OSS version_id')
            yield destination
        finally:
            if source is not None:
                close = getattr(source, 'close', None)
                if close:
                    close()
            if directory.resolve().is_relative_to(self.staging_root.resolve()):
                shutil.rmtree(directory)
