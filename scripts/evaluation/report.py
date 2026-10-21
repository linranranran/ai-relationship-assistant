"""从不可覆盖的逐例记录生成验收报告；不调用模型、不用 Agent 自评当金标准。

业务失败、供应商失败都留在已启动试验分母中；未启动单列。引用合法只是
来源/逐字校验，人工事实支持率不可从这里推导。跨模型数据只分组，不报提升率。
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from scripts.evaluation.run import summarize

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT/'docs/评测文档'
INTEGRATION = DOC/'runs/integration-20261014'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rate(values):
    eligible = [v for v in values if v is not None]
    return {'success':sum(bool(v) for v in eligible),'total':len(eligible),
            'rate':sum(bool(v) for v in eligible)/len(eligible) if eligible else None}


def fmt(value):
    return f"{value['success']}/{value['total']}（{value['rate']:.1%}）" if value['total'] else '不适用'


def summarize_group(rows, cases):
    rows = [r for r in rows if r.get('started')]
    positive_sales = [r for r in rows if r['category']=='sales' and not cases[r['case_id']]['expected'].get('expected_failure')
                      and not cases[r['case_id']].get('expected_failure')]
    return {'started':len(rows), 'rule':rate(r['score']['task_pass'] for r in rows),
            'business':rate(r['score']['business_pass'] for r in rows),
            'multi':rate(r['score']['multi_requirement_pass'] for r in rows),
            'positive_sales_structure':rate(r['score']['structure_pass'] for r in positive_sales),
            'model_requests':sum(len(r['model_calls']) for r in rows),
            'graph_invocations':sum(r.get('graph_invocations',0) for r in rows),
            'tools':sum(len(r['tool_calls']) for r in rows),
            'hard_failures':dict(Counter(code for r in rows for code in r['score']['hard_failures']))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_folder')
    args = parser.parse_args()
    folder = Path(args.run_folder).resolve()
    manifest = load(folder/'manifest.json')
    rows = [load(p) for p in (folder/'trials').glob('*.json')]
    assert len(rows)==manifest['planned_trials'], '正式记录未完成，不生成最终验收结论'
    cases = {c['case_id']:c for c in manifest['cases']}
    summary = summarize(rows,manifest)
    groups = {name:summarize_group([r for r in rows if r['category']+'/'+r['mode']==name],cases)
              for name in summary['groups']}
    splits = {split:summarize_group([r for r in rows if r['split']==split],cases) for split in ('dev','holdout')}
    direct = [r for r in rows if r['mode']=='direct']
    candidate = summarize_group(direct,cases)
    performance = load(INTEGRATION/'performance-final.json')
    catalog = load(INTEGRATION/'catalog-final-corrected.json')
    final_api = load(INTEGRATION/'api-final-results.json')
    fault = load(INTEGRATION/'fault-results.json')
    service_fault = load(INTEGRATION/'service-fault-results.json')
    restore = load(INTEGRATION/'backup-restore.json')
    restart = load(INTEGRATION/'restart-result.json')
    original_api = load(INTEGRATION/'api-results.json')
    adapter = load(INTEGRATION/'frontend-adapter-result.json')
    ui = load(INTEGRATION/'ui-results.json')
    scoring_audit = load(folder/'scoring-v3-audit.json')
    evidence = [*fault, *[r for r in service_fault if r['case_id'] in {'E05','E07'}], restart, catalog,
                *[r for r in original_api if r.get('case_id') in {'E01','A03','A08'}], *final_api, adapter, *ui]
    # 恢复探针独立报告，不把它们混进180图试验的业务完成率。
    findings = [{k:r.get(k) for k in ('trial_id','case_id','mode','repeat','split','score','response','error_type')}
                for r in rows if not r['score']['task_pass']]
    archive = load(folder/'langfuse-upload.json') if (folder/'langfuse-upload.json').exists() else None
    result = {'run_id':manifest['run_id'],'manifest':manifest,'summary':summary,'groups':groups,'splits':splits,
              'candidate_direct':candidate,'integration':evidence,'performance':performance,'failures':findings,
              'langfuse_archive':archive,'scoring_v3_audit':scoring_audit,'release_gate':'NOT_ACCEPTED',
              'full_business_task_completion_rate':None,
              'legacy_score_warning':'groups/business and original cloud scores are rule proxies, not validated full task completion. See scoring_v3_audit.',
              'pending':['人工原子事实支持率/必要事实覆盖率','内部销售试用','私有模型端点单任务费用上限',
                         'Neo4j+PostgreSQL一致备份与完整版本回滚演练','生产负载与长时间故障验证'],
              'scope_limitations':['真实图质量使用重置的虚构业务/预算/检查点仓储；PG/Neo4j另做真实集成',
                '长期事实与后台摘要任务在图矩阵中使用替身，不证明真实长期记忆检索质量',
                '180是44个独立核心案例的重复/模式组合，数据集另有16个恢复/API案例',
                'SSE适配器重复事件检查使用模拟传输；浏览器验收是人工式自动操作观察',
                '工具超时是抛出受控TimeoutError，不证明任意阻塞工具可被强制终止']}
    (DOC/'evaluation-final.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines = ['# 评测结果与验收结论', '', '日期：2026-10-14。结论：**尚未达到本项目提出的发布验收要求**。',
             '自动化评测已完成；人工销售语义复核和内部试用待真实参与者完成，不能代填。', '',
             '## 固定条件与样本', '', f"最终运行 `{manifest['run_id']}`：模型 `{manifest['model']}`，temperature=0，enable_thinking=false。",
             '60个独立合成场景分为40开发/20保留验收；其中44个核心场景完成180次试验，另外16个执行/API场景分层检查。',
             '人物16×Direct/Plan×3=96，销售16×Direct×3=48，多轮12×Direct×3=36；重复三次不算新增独立案例。',
             '每次重置初始业务数据、独立会话/请求。更新与删除均采用执行前确认。',
             f"代码内容 SHA256：`{manifest['backend_source_sha256']}`；数据 SHA256：`{manifest['dataset_sha256']}`。",
             '未提交源码保存在运行目录 source-snapshot.zip；Git提交标识仅是基底，不代表全部评测代码。', '',
             '## 最终质量数据', '',
             '**评分审查更正：下表为原自动规则分数，不是已验收的业务完成率。** 关键词可以出现在否定句中，原评分可能假阳性/假阴性。',
             'P03 Plan三次外部职业已更新，但回复否认成功；P14六次均没有命中预设故障。因此有1个故障案例未完成有效覆盖，完整业务完成率及多要求完整率暂未验收。',
             '修正评分单独保存在scoring-v3-audit.json，原始JSON和云端分数不覆盖。', '',
             '| 分组 | 已启动 | 原规则通过 | 原正向规则通过 | 原多要求词/状态检查 | 模型请求 |',
             '|---|---:|---|---|---|---:|']
    for name,g in groups.items():
        lines.append(f"| {name} | {g['started']} | {fmt(g['rule'])} | {fmt(g['business'])} | {fmt(g['multi'])} | {g['model_requests']} |")
    mutations=scoring_audit['mutation_external_state_and_authorization']
    lines += ['', f"独立复评分：人物写场景外部状态及确认/授权符合，Direct {mutations['direct']['success']}/{mutations['direct']['total']}，Plan {mutations['plan']['success']}/{mutations['plan']['total']}。此项不包含最终回复语义。",
              f"合法正向销售准备单结构生成：{fmt(candidate['positive_sales_structure'])}。",
              '原正向规则列含人物/多轮正向案例；销售单独看结构、引用、覆盖和人工语义。原规则还包含拒绝、澄清、停止等负向案例。',
              f"销售原始结构生成分母为全部48次请求，其中旧快照应拒绝，原始结果见逐次报告；正向生成分母明确排除预设旧快照拒绝。",
              f"接受的引用逐字/来源合法：{summary['citations']['valid']}/{summary['citations']['total']}；**不能据此宣称事实支持率100%**。",
              f"三次均通过原规则：{summary['three_repeat_stability']['all_three_rule_pass']}/{summary['three_repeat_stability']['eligible_case_mode_groups']} 个案例×模式组合；不代表三次语义均正确。",
              f"复评分硬失败：`{json.dumps(scoring_audit['hard_failures_by_mode'],ensure_ascii=False)}`。Direct原规则未命中不代表硬门禁已全部通过：错误地宣称全部完成仍需回复语义复核。", '',
              '| 集合 | 已启动 | 原规则符合预期 | 原正向规则通过 |', '|---|---:|---|---|']
    for split,g in splits.items():
        lines.append(f"| {split} | {g['started']} | {fmt(g['rule'])} | {fmt(g['business'])} |")
    lines += ['', '保留验收集没有用于提示词调优；修复依据为开发集。已启动超时、解析与系统错误均留在分母。', '',
              '## Direct / Plan 配对比较', '', '同一模型/代码/数据/预算下的16个人物案例，每模式48次。P14各3次没有触发故障，不能据此比较故障处理；其余案例的回复语义仍待审核。两种模式采用各自正常流程。', '']
    for mode in ('direct','plan'):
        g=groups['people/'+mode]; lat=summary['groups']['people/'+mode]
        lines.append(f"- {mode}：原正向规则 {fmt(g['business'])}，原规则 {fmt(g['rule'])}；模型{g['model_requests']}次，工具{g['tools']}次，场景P95 {lat['p95_graph_trial_ms']/1000:.2f}秒。")
    lines += ['', '差异来自模式组合，不证明Plan普遍优于ReAct；不得混入销售/记忆场景比较。', '',
              '## 调用、Token与成本', '', f"180次场景实际调用模型{summary['physical_model_requests']}次；图invoke/resume {sum(r.get('graph_invocations',0) for r in rows)}次。",
              f"供应商usage已知{summary['usage_known_requests']}/{summary['physical_model_requests']}次，已知输入{summary['prompt_tokens']:,}、输出{summary['completion_tokens']:,} Token。未知usage不当成零。",
              '私有端点实际价格/免费额度不明，费用金额和单任务费用门禁标记未验收。', '',
              '## 真实集成与恢复', '', '| 检查 | 结果 | 环境 |', '|---|---|---|']
    for item in evidence:
        lines.append(f"| {item.get('case_id',item.get('check','restart'))} | {'通过' if item.get('pass') else '未通过'} | {item.get('layer',item.get('experiment','详见原始JSON'))} |")
    lines += ['', 'PG隔离schema relation_eval_20261014；数据库账号无CREATEDB。Neo4j Community共享实例，以4个新虚构owner隔离。原有用户数据保留。',
              '真实崩溃：Neo4j提交后子进程os._exit(71)，PG仍running；新进程接管并复用相同动作，最终仅一个人物。',
              '真实重启：服务进程关闭后，新服务恢复PG里的同一interrupt，并核验实际Neo4j更新。',
              'E08协议变化时保留成功查询为PARTIALLY_COMPLETED，写入未发生；原始探针只接受FAILED过严，原始文件保留，另存corrected文件。',
              f"PG备份恢复：{len(restore['tables'])}张表、{sum(t['rows'] for t in restore['tables'])}行，恢复schema逐表行数匹配；尚未验证Neo4j+PG一致快照/完整版本回滚。",
              '首轮API使用带“评测客户”前缀的姓名时模型误解析，准备卡片未生成；失败记录保留。后续改用正常姓名杨鑫，不能因此声称前缀姓名问题已修复。', '',
              '## 真实HTTP/SSE性能', '', f"最终模型4个独立owner并发，每人2次，共{performance['samples']}个完整请求：业务{performance['business_correct']}/8，超时{performance['timeouts']}/8。",
              f"首有效进度P95 {performance['first_progress_p95_ms']/1000:.3f}秒；首回答内容P95 {performance['first_content_p95_ms']/1000:.3f}秒；完整回复P95 {performance['full_response_p95_ms']/1000:.3f}秒。",
              'N=8使用nearest-rank P95（最大样本）；共享云数据库，且正式图评测同时运行。仅满足该小样本测量的180秒/5秒提案，不能宣称生产SLA或容量认证。',
              '图场景时间包含多个预设回答轮次，与完整HTTP请求延迟分别报告；未做真实人工等待时长测量。', '',
              '## 历次运行与外部故障', '', 'qwen3.5-plus、qwen3.5-flash基线均保留180条结果；有供应商403和账户欠费。用户充值后另用qwen3.6-flash复验，不拼接成同模型实验。',
              '早期串行段中断保留13条完成结果；Flash中断保留21条再续跑159条。未落结果的在途调用usage未知，计费不能只算已存记录。',
              '充值前qwen3.6先导只有3次已启动失败、9次未启动；后者不算执行12次。',
              'Plus与Flash、Flash与本次跨模型/开关/代码同时变化，不能将成功率或延迟差称为单项整改提升。', '',
              '## 发布门禁与交付', '', '**门禁：NOT_ACCEPTED。** 品质目标未满足时须整改；即便自动化全部通过，也仍需人工语义、费用、完整回滚和内部试用验收。',
              '原始结果：当前run的trials/*.json、summary.json、semantic-review.csv；总汇总evaluation-final.json。',
              f"Langfuse归档：{archive['trials'] if archive else 0}个任务根观察读回验证={archive['readback_verified'] if archive else '待上传'}。这是task_roots_only，未逐项验证886个generation/180个轨迹/所有评分完整性；不能说全量明细已读回确认。归档不新增模型调用，云端导入时长不当作原场景延迟。",
              '建议依次阅读：本报告 → 失败案例与整改记录 → 人工复核与内部试用 → 简历与五分钟演示。']
    (DOC/'评测结果与验收结论.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

    failures = ['# 失败案例与整改记录', '', '旧失败、原始评分和修正后的独立文件均保留；没有通过修改标准答案提升分数。', '',
                '## 独立审查新增发现', '',
                '- P03 Plan三次：外部职业已更新，但任务被判FAILED、回复否认成功。原评分因缺少回复一致性检查而误通过。原轨迹显示Extraction产生额外tags=[]，完成合同据此要求该字段；后续应核对抽取的显式字段与用户授权，而非相信goal状态。',
                '- P14六次：预设仅get_person_detail失败，合法路径可能直接读取find_person完整回执，故障均未触发。另有否定句包含“钓鱼/失败”导致假通过。故障未覆盖与回复语义分别标待审，不能声称测试了独立要求容错。',
                '- 原评分使用关系集合会丢失重复次数；scoring-v3改为Counter，并检测未授权删边与新增人物额外非空属性。',
                '- 原评分漏掉“错误地宣称全部完成”硬门禁；Plan结构化COMPLETE与外部状态不一致在v3标FALSE_ALL_COMPLETED。Direct COMPLETE仅表示模型停止，最终回复的全部完成声明需人工复核，不能声称硬失败风险为零。',
                '- 图矩阵使用虚构人物字典，真实Neo4j find_person也投影完整字段，但缺省字段可能为None/[]，与fixture缺字段不同；P03的tags判定不可直接外推为真实数据库结果。下一轮应对齐默认值和返回合同。',
                '- Fixture.update_person内部取出person_id后旧审计记录丢失目标ID；调用链**kwargs隔离，不改变产品节点。当前驱动已保留完整参数快照，旧结果可从nodes回执补查；不覆盖旧记录、不因审计修复伪装重跑模型。', '',
                '## 已修复的开发集问题', '',
                '- 结构输出透传enable_thinking：普通SDK与LangChain结构输出采用相同供应商开关，避免思考消耗完2000输出预算。',
                '- Plan更新也走用户确认；工具授权与是否接收confirmed参数分开。',
                '- 关系方向统一：person是角色持有者，related_to是被描述的人；“张是杨的上司”是杨→张。修正范围校验和完成合同。',
                '- Schema修复反馈补充数值约束：只允许max_length等已知数字，避免泄露异常中的客户正文。',
                '- 确认工具说明明确调用只是操作建议，系统在写入前interrupt；普通文本询问确认不能代替工具建议。',
                '- 销售prompt v2每项最多3条证据、分开档案与聊天措辞，保留原引用上限。', '',
                '## 最终复验失败列表', '', '| 案例 | 模式 | 重复 | 集合 | 原因 |', '|---|---|---:|---|---|']
    for r in sorted(findings,key=lambda r:(r['case_id'],r['mode'],r['repeat'])):
        codes=', '.join(r['score']['failure_codes']).replace('|',' / ')
        failures.append(f"| {r['case_id']} | {r['mode']} | {r['repeat']} | {r['split']} | {codes} |")
    failures += ['', '后续整改先按同case_id读取trials中的实际提示词、返回、节点结果及最终外部状态，区分模型决策、校验器、工具、回复错误。',
                 '典型剩余限制包括跨计划依赖、非对称关系方向、多要求遗漏、代词歧义/事实表述；以本次列表为准，不能根据单次成功宣称已解决。',
                 '不得用保留集继续反复调提示词后仍将它当独立验收集；下一轮应新增保留集并另建run_id。']
    (DOC/'失败案例与整改记录.md').write_text('\n'.join(failures)+'\n',encoding='utf-8')
    resume = ['# 简历与五分钟演示', '', '## 可如实写入简历', '',
              f"建立覆盖客户资料、销售聊天证据、多轮记忆及故障恢复的60例版本化合成评测集，使用LangGraph与Langfuse完成180次真实模型图评测（qwen3.6-flash、受控业务仓储），记录{summary['physical_model_requests']}次模型调用及Token消耗；完成PostgreSQL检查点重启恢复、Neo4j提交后崩溃重放和跨用户隔离实验。",
              f"在4个测试账号并发的8次真实HTTP/SSE请求中，业务结果8/8，首有效进度P95 {performance['first_progress_p95_ms']/1000:.2f}秒，完整回复P95 {performance['full_response_p95_ms']/1000:.2f}秒；评测失败与待审项可追溯。", '',
              '不写“生产已上线”“企微已接入”“成交率提升”“零风险”。目前是模拟聊天导入，人工事实语义复核及内部销售试用待完成。', '',
              '## 面试介绍', '', '我先固定业务结果与安全边界，用真实模型跑图，评分看外部人物/关系状态和证据原文，不采用Agent自报成功。',
              f"两模式同模型跑16个人物案例各重复3次；人物写入场景外部状态与确认/授权检查Direct {mutations['direct']['success']}/{mutations['direct']['total']}，Plan {mutations['plan']['success']}/{mutations['plan']['total']}。最终回复语义和完整业务完成率仍待复核，不能用关键词评分代替。",
              '我根据开发集定位了应用校验器反向关系、确认协议和结构反馈的问题，而不是只修改提示词。未通过门禁的结果也保留，下一轮先修真实缺陷再换新验收集。', '',
              '## 五分钟演示', '',
              '1. 0:00–0:45：说明业务是基于虚构聊天证据准备下一次客户沟通；展示数据集与固定标准。',
              '2. 0:45–1:45：查杨鑫的出生年份和爱好，再问“他的职业是什么”，展示实际工具回执与进度事件。',
              '3. 1:45–2:45：选择已有杨鑫、导入虚构聊天、生成准备单，打开需求/约定对应原文。',
              '4. 2:45–3:45：请求修改备注，展示确认再执行；刷新显示历史/卡片恢复。提前确认使用虚构账号，不操作真实客户。',
              '5. 3:45–5:00：展示本次Langfuse trace、配对结果和提交后崩溃原始报告；主动解释失败、人工待审与性能N=8限制。', '',
              '现场使用演示账号而非云数据库管理员；不要将.env/API Key截图。演示可能产生模型费用；评测服务端口18082/前端5175与PyCharm隔离。']
    (DOC/'简历与五分钟演示.md').write_text('\n'.join(resume)+'\n',encoding='utf-8')
    human = ['# 人工复核与内部试用', '', '**状态：待真实参与者完成。自动化运行不代填人类评价。**', '',
             f"请从 runs/{manifest['run_id']}/semantic-review.csv 开始。每行是准备单中的一个待拆分事实条目及证据，可能包含多个原子事实，所有human字段初始pending。",
             '复核发言人、转述、否定、时间、条件、约定状态，分别记录supported/unsupported/uncertain和必要事实遗漏；销售建议可用性另外评分。',
             '事实支持率＝supported原子事实/实际复核原子事实；必要事实覆盖率＝正确表达的必要事实/人工预设必要事实。逐字引用合法率不能代替二者。',
             'csv中gold_required_sources是消息来源代理，不能直接当原子事实覆盖的金标准。建议两名复核者先校准10条，冲突由第三人/业务负责人确认。', '',
             '## 内部试用记录', '', '| 日期 | 参与者 | 虚构场景 | 任务完成 | 人工纠正 | 建议可用性 | 耗时 | 备注 |',
             '|---|---|---|---|---|---|---|---|', '| 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 未执行 |', '',
             '先邀请1–2名销售在模拟资料上试用，记录可完成任务、遗漏与不被支持的结论。未经真实试用不能写销售绩效改善。',
             '人工审核完成后生成独立版本报告，保留本轮pending状态及原始自动化评分，不覆盖旧实验。']
    human += ['', '人物与多轮回复另见本run的response-review.csv；审核数据已保存但回复否认成功、遗漏独立要求和误报全部完成。',
              '所有人类评价仍为pending。完整聊天及证据来源见逐例JSON及manifest，不只审核quote。']
    (DOC/'人工复核与内部试用.md').write_text('\n'.join(human)+'\n',encoding='utf-8')
    # 人物/记忆的回复同样可能否认已提交的结果，不能只审核销售准备单。
    with (folder/'response-review.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['trial_id','case_id','mode','repeat','expected_contract','initial_facts','actual_people','actual_relations',
                         'tool_receipts','response','human_result_consistency','human_all_requirements','human_false_completion','reviewer','notes'])
        for row in sorted(rows,key=lambda r:(r['case_id'],r['mode'],r['repeat'])):
            if row['category']=='sales':continue
            response=row['response']; response="'"+response if response.startswith(('=','+','-','@')) else response
            writer.writerow([row['trial_id'],row['case_id'],row['mode'],row['repeat'],
                json.dumps(cases[row['case_id']]['expected'],ensure_ascii=False),
                json.dumps(cases[row['case_id']]['initial']['people'],ensure_ascii=False),
                json.dumps(row['people'],ensure_ascii=False),json.dumps(row['relations'],ensure_ascii=False),
                json.dumps(row['tool_calls'],ensure_ascii=False),response,'pending','pending','pending','',''])
    print(json.dumps({'run_id':manifest['run_id'],'started':summary['started_trials'],'groups':groups,
                      'release_gate':'NOT_ACCEPTED'},ensure_ascii=False))


if __name__=='__main__':
    main()
