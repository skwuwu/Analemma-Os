import boto3

lam = boto3.client('lambda', region_name='ap-northeast-2')
func = lam.get_function(FunctionName='backend-workflow-dev-SegmentRunnerFunction-i2op6tD2ScJf')

print(f"Function: {func['Configuration']['FunctionName']}")
print(f"Last Modified: {func['Configuration']['LastModified']}")
print(f"CodeSize: {func['Configuration']['CodeSize']} bytes")
print(f"CodeSha256: {func['Configuration']['CodeSha256'][:20]}...")
print(f"Runtime: {func['Configuration'].get('Runtime', 'N/A')}")
print(f"Handler: {func['Configuration']['Handler']}")
