"""从本地冻结记录导出合成展示证据；不调用模型、不连接数据库、不上传。

完整运行日志/检查点/数据库备份保留本地。公开内容限定为统计、复评分和三个
虚构样例；原始失败保留。公开样例不足以独立验证全部180次调用的每个细节。
"""
import argparse
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser=argparse.ArgumentParser()
    # 这是指定批次的展示快照导出器，下面的集成记录和评测日期也属于该批次。
    # 限制输入可避免把另一批核心评测与本批集成结果混合，造成错误的公开结论。
    parser.add_argument('--run', choices=['20261014-141025-retest'],
                        default='20261014-141025-retest')
    args=parser.parse_args()
    source=ROOT/'docs/评测文档/runs'/args.run
    # 不接受路径穿越，也不从其他项目收集运行数据。
    assert source.resolve().parent==(ROOT/'docs/评测文档/runs').resolve()
    manifest=load(source/'manifest.json'); summary=load(source/'summary.json')
    audit=load(source/'scoring-v3-audit.json')
    trials=[load(path) for path in (source/'trials').glob('*.json')]
    assert len(trials)==manifest['planned_trials']==180 and all(r['started'] for r in trials)
    cases={case['case_id']:case for case in manifest['cases']}
    positive_sales=[row for row in trials if row['category']=='sales'
                    and not cases[row['case_id']].get('expected_failure')
                    and not cases[row['case_id']]['expected'].get('expected_failure')]
    target=ROOT/'docs/evaluations/showcase'
    target.mkdir(parents=True,exist_ok=True)

    def write(name,value):
        (target/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    samples=[]
    for cid,mode in [('P12','direct'),('S01','direct'),('P13','plan')]:
        row=next(r for r in trials if (r['case_id'],r['mode'],r['repeat'])==(cid,mode,1))
        filename=f'{cid}-{mode}-1.json'
        write(filename,row)
        samples.append({'case_id':cid,'mode':mode,'file':filename,'trial_id':row['trial_id'],
                        'physical_model_requests':len(row['model_calls']),'original_rule_pass':row['score']['task_pass']})
    write('sample-index.json',samples)
    write('scoring-v3-audit.json',audit)
    integration=ROOT/'docs/评测文档/runs/integration-20261014'
    performance=load(integration/'performance-final.json')
    write('performance.json',{**{k:v for k,v in performance.items() if k!='rows'},
        'rows':[{k:r[k] for k in ['iteration','status','first_effective_progress_ms',
                   'first_answer_content_ms','full_response_ms','timeout','business_correct']}
                for r in performance['rows']]})
    recovery=[*load(integration/'fault-results.json'),load(integration/'restart-result.json'),
              load(integration/'catalog-final-corrected.json'),
              *[r for r in load(integration/'api-final-results.json') if r['case_id']=='E06']]
    write('recovery.json',recovery)
    write('summary.json',{
        'kind':'frozen_synthetic_evaluation_snapshot','evaluated_on':'2026-10-14',
        'run_id':manifest['run_id'],'model':manifest['model'],'temperature':manifest['temperature'],
        'enable_thinking':manifest['enable_thinking'],'backend_source_sha256':manifest['backend_source_sha256'],
        'dataset_sha256':manifest['dataset_sha256'],'git_base':manifest['git_commit'],
        'planned_trials':manifest['planned_trials'],'started_trials':summary['started_trials'],
        'physical_model_requests':summary['physical_model_requests'],'usage_known_requests':summary['usage_known_requests'],
        'prompt_tokens_known':summary['prompt_tokens'],'completion_tokens_known':summary['completion_tokens'],
        'citations':summary['citations'],'positive_preparation_structure':{
            'success':sum(bool(row['score']['structure_pass']) for row in positive_sales),'total':len(positive_sales)},
        'original_rule_groups':summary['groups'],'original_rule_scores_are_not_business_completion':True,
        'full_business_task_completion_rate':None,'release_gate':'NOT_ACCEPTED',
        'human_semantic_review':'pending','public_raw_trials':len(samples),
        'limits':['quality matrix uses fixture business/budget/checkpoint stores; real integration reported separately',
                  'P14 six attempts did not exercise the preset failure',
                  'literal citation validity is not semantic factual support',
                  'only selected synthetic raw trials published; full local records are not part of Git',
                  'branding publication does not rerun the paid evaluation']})
    print(json.dumps({'source_run':args.run,'public_samples':len(samples),'new_model_requests':0}))


if __name__=='__main__':
    main()
