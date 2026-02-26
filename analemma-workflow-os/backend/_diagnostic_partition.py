import sys, json
import os

sys.path.insert(0, 'src')

# Direct import of just partition_service module, bypassing __init__.py
import importlib.util
spec = importlib.util.spec_from_file_location(
    "partition_service",
    os.path.abspath("src/services/workflow/partition_service.py")
)
partition_module = importlib.util.module_from_spec(spec)
# Need to add fake logger and deps
import logging
partition_module.__dict__['logger'] = logging.getLogger('partition_service')
spec.loader.exec_module(partition_module)
partition_workflow_advanced = partition_module.partition_workflow_advanced

base = 'src/test_workflows'
files = {
    'COMPLETE': 'test_complete_workflow.json',
    'ASYNC_LLM': 'test_async_llm_workflow.json',
    'STAGE2': 'test_llm_stage2_flow_control.json',
    'STAGE3': 'test_llm_stage3_vision_basic.json',
    'STAGE5': 'test_llm_stage5_hyper_stress.json',
    'LOOP_BENCH': 'test_loop_branch_stress_workflow.json',
    'STAGE6': 'test_llm_stage6_distributed_map_reduce.json',
    'STRESS': 'test_hyper_stress_workflow.json',
}
for name, path in files.items():
    with open(f'{base}/{path}', encoding='utf-8') as f:
        wf = json.load(f)
    try:
        result = partition_workflow_advanced(wf)
        est = result['estimated_executions']
        segs = result['total_segments']
        la = result.get('loop_analysis', {})
        max_li = est + max(int(est*0.25), 20)
        print(f'{name}: segments={segs}, weighted={la.get("total_loop_weighted_segments",0)}, est={est}, max={max_li}')
    except Exception as e:
        print(f'{name}: ERROR: {e}')
