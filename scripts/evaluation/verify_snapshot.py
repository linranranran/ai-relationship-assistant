"""离线检查公开证据包的内部一致性，不把样例校验宣称全量试验重放。"""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def main():
    folder=ROOT/'docs/evaluations/showcase'
    load=lambda path:json.loads(path.read_text(encoding='utf-8'))
    summary=load(folder/'summary.json')
    audit=load(folder/'scoring-v3-audit.json')
    data=ROOT/'docs/评测文档/data/scenarios-v2.jsonl'
    cases=[json.loads(line) for line in data.read_text(encoding='utf-8').splitlines()]
    assert len(cases)==60 and len({c['case_id'] for c in cases})==60
    assert Counter(c['split'] for c in cases)=={'dev':40,'holdout':20}
    assert sha256(data.read_bytes()).hexdigest()==summary['dataset_sha256']
    assert len(audit['trials'])==summary['started_trials']==summary['planned_trials']==180
    assert len({(r['case_id'],r['mode'],r['repeat']) for r in audit['trials']})==180
    assert summary['usage_known_requests']<=summary['physical_model_requests']
    assert summary['release_gate']=='NOT_ACCEPTED' and summary['full_business_task_completion_rate'] is None
    index=load(folder/'sample-index.json')
    for sample in index:
        row=load(folder/sample['file'])
        assert row['trial_id']==sample['trial_id']
        assert len(row['model_calls'])==sample['physical_model_requests']
        assert row['case_id']==sample['case_id'] and row['mode']==sample['mode']
    assert len(index)==summary['public_raw_trials']==3
    assert load(folder/'performance.json')['samples']==8
    print(json.dumps({'public_snapshot_consistent':True,'cases':60,'audit_rows':180,'raw_samples':3,
                      'scope':'internal consistency only; no full model replay','new_model_requests':0}))


if __name__=='__main__':
    main()
