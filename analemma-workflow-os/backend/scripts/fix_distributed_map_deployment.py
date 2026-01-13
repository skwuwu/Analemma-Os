#!/usr/bin/env python3
"""
Distributed Map 워크플로우 배포 문제 수정 스크립트

누락된 Lambda 함수들과 IAM 권한을 template.yaml에 추가하여
Distributed Map 워크플로우가 정상적으로 배포되도록 수정합니다.
"""

import os
import sys
import yaml
import json
from typing import Dict, Any

def add_missing_lambda_functions(template: Dict[str, Any]) -> Dict[str, Any]:
    """누락된 Lambda 함수들을 template.yaml에 추가"""
    
    resources = template.get('Resources', {})
    
    # 1. PrepareDistributedExecutionFunction 추가
    resources['PrepareDistributedExecutionFunction'] = {
        'Type': 'AWS::Serverless::Function',
        'Properties': {
            'Runtime': 'python3.12',
            'CodeUri': 'apps/backend/backend/',
            'Handler': 'prepare_distributed_execution.lambda_handler',
            'Timeout': 300,
            'MemorySize': 2048,
            'Description': 'Distributed Map을 위한 세그먼트 청크 생성',
            'Environment': {
                'Variables': {
                    'WORKFLOWS_TABLE': {'Ref': 'WorkflowsTableV2'},
                    'WORKFLOW_STATE_BUCKET': {
                        'Fn::If': [
                            'CreateWorkflowStateBucket',
                            {'Ref': 'WorkflowStateBucketResource'},
                            {'Ref': 'WorkflowStateBucket'}
                        ]
                    },
                    'DISTRIBUTED_CHUNK_SIZE': '100',
                    'DISTRIBUTED_MAX_CHUNKS': '100'
                }
            },
            'Policies': [
                {
                    'DynamoDBReadPolicy': {
                        'TableName': {'Ref': 'WorkflowsTableV2'}
                    }
                },
                {
                    'S3CrudPolicy': {
                        'BucketName': {
                            'Fn::If': [
                                'CreateWorkflowStateBucket',
                                {'Ref': 'WorkflowStateBucketResource'},
                                {'Ref': 'WorkflowStateBucket'}
                            ]
                        }
                    }
                }
            ]
        }
    }
    
    # 2. ResumeChunkProcessingFunction 추가
    resources['ResumeChunkProcessingFunction'] = {
        'Type': 'AWS::Serverless::Function',
        'Properties': {
            'Runtime': 'python3.12',
            'CodeUri': 'apps/backend/backend/',
            'Handler': 'resume_chunk_processing.lambda_handler',
            'Timeout': 60,
            'MemorySize': 512,
            'Description': 'HITL 후 청크 처리 재개',
            'Environment': {
                'Variables': {
                    'TASK_TOKEN_TABLE': {'Ref': 'TaskTokensTableV2'},
                    'WORKFLOW_STATE_BUCKET': {
                        'Fn::If': [
                            'CreateWorkflowStateBucket',
                            {'Ref': 'WorkflowStateBucketResource'},
                            {'Ref': 'WorkflowStateBucket'}
                        ]
                    }
                }
            },
            'Policies': [
                {
                    'DynamoDBCrudPolicy': {
                        'TableName': {'Ref': 'TaskTokensTableV2'}
                    }
                },
                {
                    'S3CrudPolicy': {
                        'BucketName': {
                            'Fn::If': [
                                'CreateWorkflowStateBucket',
                                {'Ref': 'WorkflowStateBucketResource'},
                                {'Ref': 'WorkflowStateBucket'}
                            ]
                        }
                    }
                },
                {
                    'Statement': [
                        {
                            'Effect': 'Allow',
                            'Action': [
                                'states:SendTaskSuccess',
                                'states:SendTaskFailure'
                            ],
                            'Resource': '*'
                        }
                    ]
                }
            ]
        }
    }
    
    # 3. StoreDistributedTaskTokenFunction 추가 (이미 존재하지만 확인)
    if 'StoreDistributedTaskTokenFunction' not in resources:
        resources['StoreDistributedTaskTokenFunction'] = {
            'Type': 'AWS::Serverless::Function',
            'Properties': {
                'Runtime': 'python3.12',
                'CodeUri': 'apps/backend/backend/',
                'Handler': 'store_distributed_task_token.lambda_handler',
                'Timeout': 60,
                'MemorySize': 512,
                'Description': '분산 실행 HITL Task Token 저장',
                'Environment': {
                    'Variables': {
                        'TASK_TOKEN_TABLE': {'Ref': 'TaskTokensTableV2'},
                        'WORKFLOW_STATE_BUCKET': {
                            'Fn::If': [
                                'CreateWorkflowStateBucket',
                                {'Ref': 'WorkflowStateBucketResource'},
                                {'Ref': 'WorkflowStateBucket'}
                            ]
                        }
                    }
                },
                'Policies': [
                    {
                        'DynamoDBCrudPolicy': {
                            'TableName': {'Ref': 'TaskTokensTableV2'}
                        }
                    },
                    {
                        'S3CrudPolicy': {
                            'BucketName': {
                                'Fn::If': [
                                    'CreateWorkflowStateBucket',
                                    {'Ref': 'WorkflowStateBucketResource'},
                                    {'Ref': 'WorkflowStateBucket'}
                                ]
                            }
                        }
                    }
                ]
            }
        }
    
    return template

def add_distributed_map_permissions(template: Dict[str, Any]) -> Dict[str, Any]:
    """Distributed Map 실행을 위한 IAM 권한 추가"""
    
    resources = template.get('Resources', {})
    
    # RunWorkflowFunction에 자식 실행 권한 추가
    if 'RunWorkflowFunction' in resources:
        policies = resources['RunWorkflowFunction']['Properties'].get('Policies', [])
        
        # 자식 실행 권한 추가
        distributed_map_policy = {
            'Statement': [
                {
                    'Effect': 'Allow',
                    'Action': [
                        'states:StartExecution',
                        'states:DescribeExecution', 
                        'states:GetExecutionHistory',
                        'states:ListExecutions'
                    ],
                    'Resource': [
                        {
                            'Fn::Sub': 'arn:aws:states:${AWS::Region}:${AWS::AccountId}:execution:*'
                        },
                        {
                            'Fn::Sub': 'arn:aws:states:${AWS::Region}:${AWS::AccountId}:stateMachine:*'
                        }
                    ]
                }
            ]
        }
        
        policies.append(distributed_map_policy)
        resources['RunWorkflowFunction']['Properties']['Policies'] = policies
    
    return template

def add_distributed_environment_variables(template: Dict[str, Any]) -> Dict[str, Any]:
    """Distributed Map 전용 환경 변수 추가"""
    
    globals_env = template.get('Globals', {}).get('Function', {}).get('Environment', {}).get('Variables', {})
    
    # Distributed Map 전용 변수들 추가
    distributed_vars = {
        'DISTRIBUTED_MODE_SEGMENT_THRESHOLD': '300',
        'DISTRIBUTED_CHUNK_SIZE': '100', 
        'DISTRIBUTED_MAX_CHUNKS': '100',
        'DISTRIBUTED_RESULT_BUCKET': {
            'Fn::If': [
                'CreateWorkflowStateBucket',
                {'Ref': 'WorkflowStateBucketResource'},
                {'Ref': 'WorkflowStateBucket'}
            ]
        }
    }
    
    globals_env.update(distributed_vars)
    
    return template

def update_step_functions_substitutions(template: Dict[str, Any]) -> Dict[str, Any]:
    """Step Functions에서 사용하는 함수 참조 업데이트"""
    
    resources = template.get('Resources', {})
    
    # StepFunctionDistributedOrchestrator의 DefinitionSubstitutions 업데이트
    if 'StepFunctionDistributedOrchestrator' in resources:
        substitutions = resources['StepFunctionDistributedOrchestrator']['Properties'].get('DefinitionSubstitutions', {})
        
        # 누락된 함수 참조 추가
        new_substitutions = {
            'PrepareDistributedExecutionFunction.Arn': {
                'Fn::GetAtt': ['PrepareDistributedExecutionFunction', 'Arn']
            },
            'ResumeChunkProcessingFunction.Arn': {
                'Fn::GetAtt': ['ResumeChunkProcessingFunction', 'Arn']
            },
            'StoreDistributedTaskTokenFunction.Arn': {
                'Fn::GetAtt': ['StoreDistributedTaskTokenFunction', 'Arn']
            }
        }
        
        substitutions.update(new_substitutions)
        resources['StepFunctionDistributedOrchestrator']['Properties']['DefinitionSubstitutions'] = substitutions
    
    return template

def main():
    """메인 실행 함수"""
    
    template_path = os.path.join(os.path.dirname(__file__), '..', 'template.yaml')
    
    if not os.path.exists(template_path):
        print(f"❌ template.yaml not found at {template_path}")
        sys.exit(1)
    
    print("🔧 Fixing Distributed Map deployment issues...")
    
    # template.yaml 로드
    with open(template_path, 'r', encoding='utf-8') as f:
        template = yaml.safe_load(f)
    
    print("📝 Adding missing Lambda functions...")
    template = add_missing_lambda_functions(template)
    
    print("🔐 Adding Distributed Map IAM permissions...")
    template = add_distributed_map_permissions(template)
    
    print("⚙️ Adding Distributed Map environment variables...")
    template = add_distributed_environment_variables(template)
    
    print("🔗 Updating Step Functions substitutions...")
    template = update_step_functions_substitutions(template)
    
    # 백업 생성
    backup_path = template_path + '.backup'
    with open(backup_path, 'w', encoding='utf-8') as f:
        yaml.dump(template, f, default_flow_style=False, allow_unicode=True)
    
    print(f"💾 Backup created: {backup_path}")
    
    # 수정된 template.yaml 저장
    with open(template_path, 'w', encoding='utf-8') as f:
        yaml.dump(template, f, default_flow_style=False, allow_unicode=True)
    
    print("✅ template.yaml updated successfully!")
    
    # 검증
    print("\n🧪 Validating changes...")
    
    resources = template.get('Resources', {})
    missing_functions = []
    
    required_functions = [
        'PrepareDistributedExecutionFunction',
        'ResumeChunkProcessingFunction', 
        'StoreDistributedTaskTokenFunction'
    ]
    
    for func in required_functions:
        if func not in resources:
            missing_functions.append(func)
    
    if missing_functions:
        print(f"⚠️ Still missing functions: {missing_functions}")
    else:
        print("✅ All required functions are now defined")
    
    # 환경 변수 검증
    globals_env = template.get('Globals', {}).get('Function', {}).get('Environment', {}).get('Variables', {})
    
    required_vars = [
        'DISTRIBUTED_MODE_SEGMENT_THRESHOLD',
        'DISTRIBUTED_CHUNK_SIZE',
        'DISTRIBUTED_MAX_CHUNKS'
    ]
    
    missing_vars = []
    for var in required_vars:
        if var not in globals_env:
            missing_vars.append(var)
    
    if missing_vars:
        print(f"⚠️ Still missing environment variables: {missing_vars}")
    else:
        print("✅ All required environment variables are now defined")
    
    print("\n🎉 Distributed Map deployment fix completed!")
    print("\n📋 Next steps:")
    print("1. Review the changes in template.yaml")
    print("2. Deploy using: sam build && sam deploy")
    print("3. Test both Standard and Distributed workflows")
    print("4. Monitor CloudWatch logs for any issues")

if __name__ == "__main__":
    main()