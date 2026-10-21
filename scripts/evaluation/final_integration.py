"""最终候选的端到端预算/隔离/历史复验，原始报告不覆盖。"""
import json
from dataclasses import asdict
from uuid import uuid4
from scripts.evaluation.integration_probe import PRIVATE, client, poll, write
from backend.agent.budget import BudgetLimits
from backend.db.agent_budget_store import BudgetStore


def main():
    accounts = json.loads(PRIVATE.read_text(encoding='utf-8'))
    a, b = accounts[:2]
    rows = []
    with client(a) as c, client(b) as other:
        # 先落库单次模型预算。HTTP/节点再次 ensure 必须保留这个限额，不能充值。
        rid = str(uuid4())
        store = BudgetStore(a['user_id'], rid)
        store.ensure({**asdict(BudgetLimits()), 'model_calls':1})
        accepted = c.post('/api/chat', json={'message':'查询杨鑫的完整资料，包括出生年份和爱好','response_kind':'chat'},
                          headers={'Idempotency-Key':rid})
        accepted.raise_for_status()
        result = poll(c, rid)
        snapshot = store.snapshot()
        repeated = c.post('/api/chat', json={'message':'查询杨鑫的完整资料，包括出生年份和爱好','response_kind':'chat'},
                          headers={'Idempotency-Key':rid})
        after = store.snapshot()
        rows.append({'case_id':'E06','layer':'real_http_model_pg_budget','pass':
                     result['status']=='BUDGET_EXCEEDED' and snapshot['model_calls']==1 and after['model_calls']==1,
                     'final':result,'budget':snapshot,'retry_status':repeated.status_code,'retry_model_calls':after['model_calls']})
        alien = other.get(f'/api/chat/requests/{rid}')
        alien_events = other.get(f'/api/chat/requests/{rid}/events')
        rows.append({'case_id':'A07','pass':alien.status_code==404 and alien_events.status_code==404,
                     'status_http':alien.status_code,'events_http':alien_events.status_code})
        stale = c.post('/api/chat/resume',json={'interrupt_id':'expired-eval-final','confirmed':True})
        rows.append({'case_id':'A05','pass':stale.status_code==409,'http_status':stale.status_code})
        # 预算终止以后再次受理普通请求，证明会话占用得到释放。
        fresh = str(uuid4())
        next_request = c.post('/api/chat',json={'message':'查询杨鑫的出生年份和爱好','response_kind':'chat'},
                              headers={'Idempotency-Key':fresh})
        next_request.raise_for_status()
        final = poll(c,fresh)
        rows.append({'case_id':'A06','pass':final['status']=='COMPLETED' and '1998' in final.get('response','')
                     and '阅读' in final.get('response',''),'final':final})
        history = c.get('/api/chat/history',params={'limit':40})
        history.raise_for_status()
        # 这里只保存已授权的虚构历史，不保存 Authorization 请求头。
        rows.append({'case_id':'A01','layer':'authenticated_http_history','pass':fresh in json.dumps(history.json()),
                     'history':history.json()})
    write('api-final-results.json',rows)
    print(json.dumps([{'case_id':r['case_id'],'pass':r['pass']} for r in rows]),flush=True)


if __name__ == '__main__':
    main()
