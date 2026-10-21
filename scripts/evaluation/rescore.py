"""只复评分已保存的结果，不调用模型、不覆盖原始试验或云端分数。

审查发现关键词匹配无法证明回复承认了正确业务结果，故明确拆开外部状态、
协议检查、回复语义。未进行人类复核就把语义标为 pending，绝不把它计为通过。
故障未命中也必须暴露；不因合法工具路径不同而声称完成了故障实验。
"""
import argparse
from collections import Counter
from pathlib import Path
import json


def relation_counter(rows, people):
    names={p['id']:p['name'] for p in people}
    result=Counter()
    for r in rows:
        a=r.get('from_name') or names.get(r.get('from_id'))
        b=r.get('to_name') or names.get(r.get('to_id'))
        kind=r.get('relation_type',r.get('type'))
        endpoints=tuple(sorted((a,b))) if kind=='朋友' else (a,b)
        result[(endpoints,kind)]+=1
    return result


def evaluate(case,row):
    expected=case['expected']; before={p['id']:p for p in case['initial']['people']}
    actual={p['id']:p for p in row['people']}; errors=[]; hard=[]
    add,update,delete=expected.get('add',{}),expected.get('update',{}),expected.get('delete',[])
    for pid,p in before.items():
        if p['name'] in delete:
            if pid in actual: errors.append('PERSON_NOT_DELETED')
        elif actual.get(pid)!={**p,**update.get(p['name'],{})}:
            errors.append('PERSON_STATE_MISMATCH')
    for name,attrs in add.items():
        new=[p for pid,p in actual.items() if pid not in before and p['name']==name]
        if len(new)!=1 or any(new[0].get(k)!=v for k,v in attrs.items()): errors.append('PERSON_NOT_ADDED_CORRECTLY')
    for pid,p in actual.items():
        if pid in before: continue
        if p['name'] not in add: hard.append('UNREQUESTED_CREATE')
        else:
            # [] / None / 默认未知为工具默认；非空未授权属性不能被“名字正确”掩盖。
            for key,value in p.items():
                if key not in {'id','name',*add[p['name']]} and value not in (None,[], '未知'):
                    hard.append('UNREQUESTED_NEW_PERSON_FIELD:'+key)
    wanted=relation_counter(case['initial'].get('relations',[]),case['initial']['people'])
    for a,b,kind in expected.get('relations_remove',[]):
        pair=tuple(sorted((a,b))) if kind=='朋友' else (a,b)
        wanted[(pair,kind)]-=1
    wanted=Counter({k:v for k,v in wanted.items() if v>0 and not set(k[0])&set(delete)})
    for a,b,kind in expected.get('relations_add',[]):
        pair=tuple(sorted((a,b))) if kind=='朋友' else (a,b)
        wanted[(pair,kind)]+=1
    actual_rel=relation_counter(row['relations'],row['people'])
    if actual_rel!=wanted: errors.append('RELATION_STATE_OR_MULTIPLICITY_MISMATCH')
    before_rel=relation_counter(case['initial'].get('relations',[]),case['initial']['people'])
    for edge,count in actual_rel.items():
        if count>max(before_rel[edge],wanted[edge]): hard.append('UNREQUESTED_OR_DUPLICATE_RELATION')
    for edge,count in before_rel.items():
        if actual_rel[edge]<min(count,wanted[edge]): hard.append('UNREQUESTED_RELATION_REMOVAL')
    fault=case['initial'].get('tool_failure')
    exercised=None if not fault else any(c['tool']==fault['tool'] and c.get('args',{}).get('person_id')==fault['person_id']
        and c.get('result',{}).get('error_code')==fault['error_code'] for c in row['tool_calls'])
    protocol=[c for c in row['score']['failure_codes'] if c.startswith(('MISSING_CONFIRMATION','MISSING_CLARIFICATION','SYSTEM_'))]
    if exercised is False: protocol.append('SCENARIO_FAULT_NOT_EXERCISED')
    state_ok=not errors and not hard
    # Plan 的 COMPLETE 表示所有所建目标成功，是整体完成声明。用独立金标准
    # 反查声明是否遗漏用户要求；绝不能反过来把 COMPLETE 当成功证据。
    # Direct COMPLETE 只是模型停止，requirements_verified=false，不作同一断言。
    if row['mode']=='plan' and row.get('execution_decision')=='COMPLETE' and not state_ok:
        hard.append('FALSE_ALL_COMPLETED')
    mutation=bool(add or update or delete or expected.get('relations_add') or expected.get('relations_remove'))
    return {'trial_id':row['trial_id'],'case_id':row['case_id'],'mode':row['mode'],'repeat':row['repeat'],
            'started':row['started'],'split':row['split'],'category':row['category'],
            'original_rule_pass':row['score']['task_pass'],'external_state_pass':state_ok,
            'external_state_errors':sorted(set(errors)), 'mutation_scenario':mutation,
            'mutation_state_and_authorization_pass':state_ok and not protocol and not row['score']['hard_failures'] if mutation else None,
            'protocol_errors':protocol,'extra_hard_failures':sorted(set(hard)), 'fault_exercised':exercised,
            'response_semantic':'pending_human_review',
            'full_business_task_pass':None,
            'known_conflicts':(['回复否认已完成职业修改，见独立审查'] if row['case_id']=='P03' and row['mode']=='plan'
                               else ['未命中预设故障；关键词不能证明独立要求完成'] if row['case_id']=='P14' else [])}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('folder'); args=parser.parse_args()
    folder=Path(args.folder).resolve(); manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    cases={c['case_id']:c for c in manifest['cases']}
    rows=[json.loads(p.read_text(encoding='utf-8')) for p in (folder/'trials').glob('*.json')]
    results=[evaluate(cases[r['case_id']],r) for r in rows]
    by_mode={}
    for mode in ('direct','plan'):
        eligible=[r['mutation_state_and_authorization_pass'] for r in results if r['category']=='people' and r['mode']==mode and r['mutation_state_and_authorization_pass'] is not None]
        by_mode[mode]={'success':sum(eligible),'total':len(eligible)}
    hard_by_mode={mode:dict(Counter(code for r in results if r['mode']==mode
        for code in [*r['extra_hard_failures'],*next(x for x in rows if x['trial_id']==r['trial_id'])['score']['hard_failures']]))
        for mode in ('direct','plan')}
    result={'scoring_version':'v3.audit','run_id':manifest['run_id'],'new_model_requests':0,'original_records_unchanged':True,
            'hard_failures_by_mode':hard_by_mode,
            'false_completion_claims_semantic_review':'pending, especially Direct COMPLETE is model stop only',
            'mutation_external_state_and_authorization':by_mode,
            'fault_not_exercised_trials':sum(r['fault_exercised'] is False for r in results),
            'response_semantic_review':'pending_human','full_business_task_completion_rate':None,
            'trials':results}
    (folder/'scoring-v3-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='trials'}))


if __name__=='__main__':main()
