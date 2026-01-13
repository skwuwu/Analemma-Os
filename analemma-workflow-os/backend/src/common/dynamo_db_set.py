from src.aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_dynamodb as dynamodb,
    aws_kms as kms,
    CfnOutput,
)
from src.constructs import Construct


class ServerlessWorkflowDatabaseStack(Stack):
    """
    [개선됨] 단일 책임 원칙: DynamoDB 테이블과 데이터 레이어만 정의
    
    이전 이름: ServerlessWorkflowStack
    새 이름: ServerlessWorkflowDatabaseStack
    
    책임:
    - DynamoDB 테이블 정의 (Workflows, TaskTokens, Users)
    - GSI 및 인덱스 설정
    - 테이블 암호화 및 TTL 설정
    - Cross-stack reference를 위한 ARN 출력
    
    제외된 책임 (별도 스택으로 이동):
    - Lambda 함수들 (ComputeStack/ApiStack으로)
    - EventBridge 규칙들 (ComputeStack으로)
    - Step Functions (ComputeStack으로)
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        workflows_table_name: str = "Workflows",
        task_tokens_table_name: str = "TaskTokens",
        users_table_name: str = "Users",
        enable_tasktokens_cmk: bool = False,
        # Optional list of IAM role ARNs (e.g. Lambda roles) to grant KMS usage
        tasktokens_cmk_grant_role_arns: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # =====================================================================
        # 1. Workflows Table: 워크플로우 설계를 저장하는 핵심 테이블
        # =====================================================================
        # 사용자가 생성한 워크플로우의 JSON 설계도와 메타데이터를 저장합니다.
        # NOTE: `is_scheduled` is stored as STRING ("true"/"false") because
        # DynamoDB does not allow BOOLEAN types as key attributes (GSI PK/SK
        # must be STRING, NUMBER or BINARY). Keep this in mind when writing
        # scheduler code that queries the ScheduledWorkflowsIndex.

        workflows_table = dynamodb.Table(
            self,
            "WorkflowsTable",
            table_name=workflows_table_name,
            partition_key=dynamodb.Attribute(
                name="ownerId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="workflowId",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- Global Secondary Index (GSI) 추가 ---
        # [개선됨] 샤딩 기반 스케줄러 GSI: 대규모 확장성을 위한 "핫 파티션" 방지
        # 중앙 스케줄러 람다가 "지금 실행해야 할" 워크플로우를 효율적으로 찾기 위해 사용합니다.
        # 
        # 샤딩 전략:
        # - PK: schedule_shard_id ("shard_0" ~ "shard_9", 워크플로우 저장 시 랜덤 할당)
        # - SK: next_run_time (실행 시각)
        # - 스케줄러는 10개 샤드를 병렬 쿼리하여 읽기 부하를 분산시킵니다.
        workflows_table.add_global_secondary_index(
            index_name="ScheduledWorkflowsIndexV2",
            partition_key=dynamodb.Attribute(
                name="schedule_shard_id",
                type=dynamodb.AttributeType.STRING
            ),
            sort_key=dynamodb.Attribute(
                name="next_run_time",
                type=dynamodb.AttributeType.NUMBER
            )
        )

        # --- OwnerId/Name GSI: support efficient lookup by (ownerId, name)
        # This index is required by the get_workflow_by_name Lambda which
        # queries OwnerIdNameIndex with ownerId as partition key and name as sort key.
        workflows_table.add_global_secondary_index(
            index_name="OwnerIdNameIndexV2",
            partition_key=dynamodb.Attribute(
                name="ownerId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="name",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL,
        )

        # -----------------------------------------------------------------
        # [제거됨] MergeCallback Lambda는 ComputeStack/ApiStack으로 이동
        # Lambda 함수들은 별도의 스택에서 정의하고, 이 테이블들을
        # Cross-stack reference로 참조하도록 구성하는 것이 좋습니다.
        # -----------------------------------------------------------------

        # =====================================================================
        # 2. Task Tokens Table: Human-in-the-Loop(HITP) 상태 저장용 테이블
        # =====================================================================
        # Step Functions가 일시 중지될 때 생성되는 TaskToken을 임시로 저장합니다.
        # Optionally use a customer-managed KMS key for stronger data-at-rest
        # protection for TaskTokens, which contain sensitive taskToken values.
        task_tokens_encryption = dynamodb.TableEncryption.AWS_MANAGED
        task_tokens_key = None
        if enable_tasktokens_cmk:
            task_tokens_key = kms.Key(self, "TaskTokensCMK", enable_key_rotation=True)
            # Ensure DynamoDB service can use the CMK for table encryption ops
            try:
                from src.aws_cdk import aws_iam as iam

                # Allow DynamoDB service principal to use the key
                task_tokens_key.add_to_resource_policy(iam.PolicyStatement(
                    actions=[
                        "kms:Encrypt",
                        "kms:Decrypt",
                        "kms:ReEncrypt*",
                        "kms:GenerateDataKey*",
                        "kms:DescribeKey",
                    ],
                    principals=[iam.ServicePrincipal("dynamodb.amazonaws.com")],
                    resources=["*"],
                ))

                # Optionally grant the provided role ARNs (Lambda roles) permission
                if tasktokens_cmk_grant_role_arns:
                    for arn in tasktokens_cmk_grant_role_arns:
                        task_tokens_key.add_to_resource_policy(iam.PolicyStatement(
                            actions=[
                                "kms:Decrypt",
                                "kms:Encrypt",
                                "kms:ReEncrypt*",
                                "kms:GenerateDataKey*",
                                "kms:DescribeKey",
                            ],
                            principals=[iam.ArnPrincipal(arn)],
                            resources=["*"],
                        ))
            except Exception:
                # If aws_iam isn't available at synth time in this environment,
                # continue; real CDK synth will have the module.
                pass
            task_tokens_encryption = dynamodb.TableEncryption.CUSTOMER_MANAGED

        # TaskTokens table uses a composite key to scope tokens to a tenant (ownerId).
        # Partition key: ownerId (tenant/owner)
        # Sort key: conversation_id (unique per conversation within owner)
        task_tokens_table = dynamodb.Table(
            self,
            "TaskTokensTable",
            table_name=task_tokens_table_name,
            partition_key=dynamodb.Attribute(
                name="ownerId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="conversation_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
            encryption=task_tokens_encryption,
            encryption_key=task_tokens_key,
        )

        # Add GSI to support efficient lookup by execution_id within a tenant.
        # GSI partition: ownerId, sort: execution_id
        task_tokens_table.add_global_secondary_index(
            index_name="ExecutionIdIndexV2",
            partition_key=dynamodb.Attribute(
                name="ownerId",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="execution_id",
                type=dynamodb.AttributeType.STRING,
            ),
            projection_type=dynamodb.ProjectionType.ALL
        )

        # =====================================================================
        # 3. Users Table: 사용자 정보 저장용 테이블
        # =====================================================================
        # 기본적인 사용자 정보를 저장합니다. 필요에 따라 GSI 등을 추가할 수 있습니다.
        users_table = dynamodb.Table(
            self,
            "UsersTable",
            table_name=users_table_name,
            partition_key=dynamodb.Attribute(
                name="userId",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- CloudFormation Outputs: Cross-stack reference 지원 ---
        # 다른 스택(ComputeStack, ApiStack)에서 이 테이블들을 참조할 수 있도록
        # 테이블 이름과 ARN을 모두 출력합니다.
        
        # Table Names (기존 호환성 유지)
        CfnOutput(self, "WorkflowsTableName", value=workflows_table.table_name)
        CfnOutput(self, "TaskTokensTableName", value=task_tokens_table.table_name) 
        CfnOutput(self, "UsersTableName", value=users_table.table_name)
        
        # Table ARNs (Cross-stack reference용)
        CfnOutput(self, "WorkflowsTableArn", value=workflows_table.table_arn,
                 export_name="DatabaseStack-WorkflowsTableArn")
        CfnOutput(self, "TaskTokensTableArn", value=task_tokens_table.table_arn,
                 export_name="DatabaseStack-TaskTokensTableArn") 
        CfnOutput(self, "UsersTableArn", value=users_table.table_arn,
                 export_name="DatabaseStack-UsersTableArn")
        
        # GSI Names (스케줄러 등에서 사용)
        CfnOutput(self, "ScheduledWorkflowsIndexName", value="ScheduledWorkflowsIndexV2",
                 export_name="DatabaseStack-ScheduledWorkflowsIndexName")
        CfnOutput(self, "OwnerIdNameIndexName", value="OwnerIdNameIndexV2",
                 export_name="DatabaseStack-OwnerIdNameIndexName")
        CfnOutput(self, "ExecutionIdIndexName", value="ExecutionIdIndexV2",
                 export_name="DatabaseStack-ExecutionIdIndexName")

        # -----------------------------------------------------------------
        # [개선됨] Lambda와 EventBridge는 별도 스택에서 정의
        # -----------------------------------------------------------------
        # 이전에 여기서 정의되던 SchedulerFunction은 이제 ComputeStack에서
        # 정의하고, 아래 테이블 ARN들을 Cross-stack reference로 가져와 사용합니다:
        # 
        # Example in ComputeStack:
        # from src.aws_cdk import Fn
        # workflows_table_arn = Fn.import_value("DatabaseStack-WorkflowsTableArn")
        # workflows_table = dynamodb.Table.from_table_arn(self, "ImportedWorkflowsTable", workflows_table_arn)
        # 
        # SchedulerFunction 샤딩 쿼리 예시:
        # for shard_id in range(10):  # shard_0 ~ shard_9
        #     response = table.query(
        #         IndexName="ScheduledWorkflowsIndex",
        #         KeyConditionExpression=Key('schedule_shard_id').eq(f'shard_{shard_id}') & 
        #                               Key('next_run_time').lt(current_time)
        #     )
        # -----------------------------------------------------------------
