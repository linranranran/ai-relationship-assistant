"""保留原探针，修正“有已完成查询时只能 FAILED”的错误评测前提。"""
import json
from scripts.evaluation.integration_probe import FOLDER, write

original = json.loads((FOLDER/'catalog-final-result.json').read_text(encoding='utf-8'))
final = original['final']
corrected = {**original, 'original_probe_pass':original['pass'],
             'pass':original['person_unchanged'] and final['status'] in {'FAILED','PARTIALLY_COMPLETED'}
             and final.get('completion_basis')=='SYSTEM_STOP'
             and '执行协议已变化' in final.get('response',''),
             'grading_correction':'允许如实保留已完成查询为 PARTIALLY_COMPLETED；必须协议停止、写入未发生。原始结果不覆盖。'}
write('catalog-final-corrected.json',corrected)
print(json.dumps({'case_id':'E08','pass':corrected['pass'],'status':final['status']}))
