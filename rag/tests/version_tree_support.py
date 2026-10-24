import json


def tree_event(revision=1, version_id=10, parent=None, action='document.version.created', **overrides):
    value = {
        'schema_version': 2, 'event_id': f'tree-{revision}', 'event_type': action,
        'tenant_id': 'tenant-a', 'knowledge_base_id': 'oa', 'document_id': 'policy',
        'document_revision': revision,
    }
    if action != 'document.deleted':
        value['version_id'] = version_id
    if action == 'document.version.created':
        value.update(parent_version_id=parent, version_label=f'V{revision}', file_name='policy.md',
                     object={'bucket': 'knowledge', 'key': f'policy/{version_id}/policy.md', 'sha256': 'a' * 64})
    value.update(overrides)
    return json.dumps(value).encode()
