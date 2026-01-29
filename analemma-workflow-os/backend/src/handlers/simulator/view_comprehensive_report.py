"""
Comprehensive Report Viewer - LLM Simulator 종합 리포트 시각화 도구

Usage:
    python -m src.handlers.simulator.view_comprehensive_report <report_json_file>
    
또는 직접 호출:
    from src.handlers.simulator.view_comprehensive_report import print_comprehensive_report
    print_comprehensive_report(report_dict)
"""

import json
import sys
from typing import Dict, Any


def print_comprehensive_report(report: Dict[str, Any], verbose: bool = True):
    """
    종합 리포트를 보기 좋게 출력합니다.
    
    Args:
        report: aggregate_llm_results.py에서 생성된 리포트
        verbose: True면 thinking 로그 전체 출력, False면 요약만
    """
    comp_report = report.get('comprehensive_report', {})
    
    print("\n" + "="*80)
    print("📊 LLM SIMULATOR COMPREHENSIVE REPORT")
    print("="*80)
    
    # 1. 전체 요약
    summary = report.get('summary', {})
    print(f"\n🎯 Overall Status: {report.get('overall_status')}")
    print(f"   Pass Rate: {summary.get('pass_rate')}% ({summary.get('passed')}/{summary.get('total')} passed)")
    
    # 2. 집계 통계
    agg_stats = comp_report.get('aggregate_statistics', {})
    print(f"\n📈 Aggregate Statistics:")
    print(f"   • Total Input Tokens:  {agg_stats.get('total_input_tokens', 0):>10,}")
    print(f"   • Total Output Tokens: {agg_stats.get('total_output_tokens', 0):>10,}")
    print(f"   • Total Cached Tokens: {agg_stats.get('total_cached_tokens', 0):>10,}")
    print(f"   • Avg Cache Hit Rate:  {agg_stats.get('average_cache_hit_rate', 0):>10.2f}%")
    print(f"   • Total Cost:          ${agg_stats.get('total_cost_usd', 0):>9.6f}")
    print(f"   • Cost Saved:          ${agg_stats.get('total_cost_saved_usd', 0):>9.6f} ({agg_stats.get('cost_reduction_percentage', 0):.2f}%)")
    print(f"   • Thinking Steps:      {agg_stats.get('total_thinking_steps', 0):>10}")
    
    # 3. 시나리오별 상세 정보
    scenario_details = comp_report.get('scenario_details', {})
    context_analysis = comp_report.get('context_analysis', {})
    
    print(f"\n📋 Scenario Details:")
    print("-" * 80)
    
    for scenario, details in scenario_details.items():
        status = details.get('status', 'UNKNOWN')
        status_emoji = "✅" if status == "PASSED" else "❌"
        
        print(f"\n{status_emoji} {scenario}")
        print(f"   Status: {status}")
        
        # Usage 정보
        usage = details.get('usage', {})
        print(f"   Usage:")
        print(f"     - Provider: {usage.get('provider', 'unknown')}")
        print(f"     - Input:    {usage.get('input_tokens', 0):,} tokens")
        print(f"     - Output:   {usage.get('output_tokens', 0):,} tokens")
        print(f"     - Cached:   {usage.get('cached_tokens', 0):,} tokens")
        print(f"     - Cost:     ${usage.get('estimated_cost_usd', 0):.6f}")
        if usage.get('cost_saved_usd', 0) > 0:
            print(f"     - Saved:    ${usage.get('cost_saved_usd', 0):.6f}")
        
        # Context 정보
        context = details.get('context', {})
        print(f"   Context:")
        print(f"     - Size:     {context.get('size_tokens', 0):,} tokens (~{context.get('estimated_size_kb', 0):.2f} KB)")
        print(f"     - Cached:   {context.get('cached_tokens', 0):,} tokens")
        print(f"     - Hit Rate: {context.get('cache_hit_rate', 0):.2f}%")
        
        # Thinking Mode 정보
        thinking_count = details.get('thinking_steps_count', 0)
        if thinking_count > 0:
            print(f"   🧠 Thinking Mode: {thinking_count} steps")
        
        # 결과 링크
        outcome_url = details.get('outcome_url')
        if outcome_url:
            print(f"   🔗 Outcome: {outcome_url}")
    
    # 4. Thinking 로그 (시나리오별)
    thinking_logs = comp_report.get('thinking_logs_by_scenario', {})
    
    if thinking_logs:
        print(f"\n🧠 Thinking Mode Logs:")
        print("-" * 80)
        
        for scenario, thinking_data in thinking_logs.items():
            count = thinking_data.get('count', 0)
            logs = thinking_data.get('logs', [])
            
            print(f"\n📝 {scenario} ({count} steps):")
            
            if verbose and logs:
                for i, log in enumerate(logs, 1):
                    if isinstance(log, dict):
                        step = log.get('step', i)
                        thought = log.get('thought', log.get('content', 'No thought'))
                        reasoning = log.get('reasoning', '')
                        
                        print(f"   Step {step}:")
                        print(f"     Thought:   {thought[:100]}..." if len(thought) > 100 else f"     Thought:   {thought}")
                        if reasoning:
                            print(f"     Reasoning: {reasoning[:100]}..." if len(reasoning) > 100 else f"     Reasoning: {reasoning}")
                    else:
                        print(f"   Step {i}: {str(log)[:150]}...")
            else:
                print(f"   (Use --verbose to see full thinking logs)")
    
    # 5. Context 분석 요약
    print(f"\n📊 Context Analysis Summary:")
    print("-" * 80)
    
    if context_analysis:
        scenarios_with_cache = [s for s, c in context_analysis.items() if c.get('cache_hit_rate', 0) > 0]
        
        print(f"   • Scenarios with caching: {len(scenarios_with_cache)}/{len(context_analysis)}")
        
        # 가장 큰 context
        largest_context = max(context_analysis.items(), key=lambda x: x[1].get('size_tokens', 0))
        print(f"   • Largest context: {largest_context[0]} ({largest_context[1].get('size_tokens', 0):,} tokens)")
        
        # 가장 높은 cache hit rate
        if scenarios_with_cache:
            best_cache = max(
                [(s, c) for s, c in context_analysis.items() if c.get('cache_hit_rate', 0) > 0],
                key=lambda x: x[1].get('cache_hit_rate', 0)
            )
            print(f"   • Best cache hit rate: {best_cache[0]} ({best_cache[1].get('cache_hit_rate', 0):.2f}%)")
    
    print("\n" + "="*80)


def main():
    """CLI 진입점"""
    if len(sys.argv) < 2:
        print("Usage: python -m src.handlers.simulator.view_comprehensive_report <report_json_file> [--verbose]")
        print("\nOr use environment variable:")
        print("  python -m src.handlers.simulator.view_comprehensive_report $REPORT_PATH --verbose")
        sys.exit(1)
    
    report_path = sys.argv[1]
    verbose = '--verbose' in sys.argv or '-v' in sys.argv
    
    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            report = json.load(f)
        
        print_comprehensive_report(report, verbose=verbose)
        
    except FileNotFoundError:
        print(f"Error: File not found: {report_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def lambda_handler(event, context):
    """
    AWS Lambda handler - Step Functions에서 호출용
    
    Input:
        event: {
            "report": {...}  # aggregate_llm_results의 출력
        }
    
    Returns:
        {
            "formatted_report": str,  # 텍스트 형식 리포트
            "comprehensive_report": {...},  # 원본 comprehensive_report
            "summary": {...}  # 요약 정보
        }
    """
    import io
    import sys
    
    report = event.get('report', {})
    
    if not report:
        return {
            'error': 'No report data provided',
            'formatted_report': '',
            'comprehensive_report': {},
            'summary': {}
        }
    
    # Capture print output to string
    old_stdout = sys.stdout
    sys.stdout = captured_output = io.StringIO()
    
    try:
        # Generate formatted report
        print_comprehensive_report(report, verbose=False)
        formatted_report = captured_output.getvalue()
    finally:
        sys.stdout = old_stdout
    
    # Extract comprehensive report and summary
    comp_report = report.get('comprehensive_report', {})
    agg_stats = comp_report.get('aggregate_statistics', {})
    
    # Create summary with pass/fail for each scenario
    scenario_details = comp_report.get('scenario_details', {})
    scenarios_summary = {}
    
    for scenario, details in scenario_details.items():
        scenarios_summary[scenario] = {
            'status': details.get('status', 'UNKNOWN'),
            'passed': details.get('status') == 'PASSED',
            'usage_tokens': details.get('usage', {}).get('total_tokens', 0),
            'cached_tokens': details.get('usage', {}).get('cached_tokens', 0),
            'thinking_steps': details.get('thinking_steps_count', 0),
            'context_size_kb': details.get('context', {}).get('estimated_size_kb', 0)
        }
    
    summary = {
        'overall_status': report.get('overall_status', 'UNKNOWN'),
        'pass_rate': report.get('summary', {}).get('pass_rate', 0),
        'total_scenarios': report.get('summary', {}).get('total', 0),
        'passed_count': report.get('summary', {}).get('passed', 0),
        'failed_count': report.get('summary', {}).get('failed', 0),
        'scenarios': scenarios_summary,
        'aggregate_statistics': {
            'total_tokens': agg_stats.get('total_input_tokens', 0) + agg_stats.get('total_output_tokens', 0),
            'cached_tokens': agg_stats.get('total_cached_tokens', 0),
            'cache_hit_rate': agg_stats.get('average_cache_hit_rate', 0),
            'total_cost_usd': agg_stats.get('total_cost_usd', 0),
            'cost_saved_usd': agg_stats.get('total_cost_saved_usd', 0),
            'cost_reduction_percentage': agg_stats.get('cost_reduction_percentage', 0),
            'thinking_steps': agg_stats.get('total_thinking_steps', 0)
        }
    }
    
    return {
        'formatted_report': formatted_report,
        'comprehensive_report': comp_report,
        'summary': summary
    }


if __name__ == '__main__':
    main()
