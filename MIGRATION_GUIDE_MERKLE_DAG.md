# Merkle DAG Migration Guide

## 🎯 Overview

This guide helps migrate existing workflows from **Legacy Mode** to **Merkle DAG Mode** for:
- 93% StateBag size reduction (900KB → 60KB)
- Content-Addressable Storage (90% deduplication)
- O(1) segment verification (Pre-computed Hash)
- Automatic Garbage Collection

---

## 📊 Current State Analysis

### Legacy Mode (Deprecated - v4.0 removal)
```python
# initialize_state_data.py
if not manifest_id:
    bag['workflow_config'] = workflow_config  # 200KB
    bag['partition_map'] = partition_map      # 50KB
    # Total: 250KB unnecessary overhead per segment
```

**Issues:**
- ❌ Full workflow graph transmitted to every segment
- ❌ Network/memory inefficiency
- ❌ No content deduplication

### Merkle DAG Mode (Current - Default for new workflows)
```python
# initialize_state_data.py
if manifest_id:
    bag['manifest_id'] = manifest_id      # ~36 bytes
    bag['manifest_hash'] = manifest_hash  # 64 bytes
    bag['config_hash'] = config_hash      # 64 bytes
    # Total: ~160 bytes (99.9% reduction)
```

**Benefits:**
- ✅ Content-Addressable Storage
- ✅ 93% payload reduction
- ✅ Automatic block deduplication
- ✅ Merkle integrity verification

---

## 🔄 Migration Strategy

### Phase 1: Verify Merkle DAG Availability (Immediate)

**Check if your workflow uses Merkle DAG:**

```python
# In segment_runner_service.py logs
[Merkle DAG] State storage optimized: manifest_id=abc12345..., StateBag reduction: ~93%
```

**vs Legacy warning:**

```python
[DEPRECATED] Legacy state storage detected. workflow_config/partition_map will be removed in v4.0.
```

### Phase 2: Infrastructure Setup (Week 1)

**1. Deploy BlockReferenceCounts DynamoDB Table:**

```yaml
# CloudFormation template.yaml
BlockReferenceCounts:
  Type: AWS::DynamoDB::Table
  Properties:
    TableName: !Sub '${AWS::StackName}-BlockReferenceCounts'
    BillingMode: PAY_PER_REQUEST
    AttributeDefinitions:
      - AttributeName: block_id
        AttributeType: S
    KeySchema:
      - AttributeName: block_id
        KeyType: HASH
    StreamSpecification:
      StreamViewType: NEW_AND_OLD_IMAGES
    TimeToLiveSpecification:
      AttributeName: ttl
      Enabled: true
```

**2. Deploy Garbage Collection Lambda:**

```yaml
MerkleGCFunction:
  Type: AWS::Serverless::Function
  Properties:
    CodeUri: src/services/state/
    Handler: merkle_gc_service.lambda_handler
    Runtime: python3.11
    Environment:
      Variables:
        WORKFLOW_MANIFESTS_TABLE: !Ref WorkflowManifestsV3
        BLOCK_REF_COUNT_TABLE: !Ref BlockReferenceCounts
        S3_BUCKET: !Ref StateBucket
    Events:
      DynamoDBStream:
        Type: DynamoDB
        Properties:
          Stream: !GetAtt WorkflowManifestsV3.StreamArn
          StartingPosition: LATEST
          FilterCriteria:
            Filters:
              - Pattern: '{"eventName": ["REMOVE"], "userIdentity": {"type": ["Service"]}}'
```

**3. Verify Merkle DAG Service Deployment:**

```bash
# Check if StateVersioningService is available
python -c "from src.services.state.state_versioning_service import StateVersioningService; print('✅ Ready')"
```

### Phase 3: Enable Merkle DAG (Week 2)

**Option A: New workflows (Recommended)**

All new workflows created after deployment automatically use Merkle DAG.

**Option B: Existing workflows (Optional)**

Re-trigger workflow initialization to migrate:

```python
# Trigger re-initialization (creates new manifest_id)
POST /workflows/{workflow_id}/reinitialize

# Response
{
  "manifest_id": "abc12345-...",
  "manifest_hash": "sha256...",
  "migration_status": "completed",
  "statebag_reduction": "93%"
}
```

### Phase 4: Verification (Week 3)

**1. Check CloudWatch Metrics:**

```bash
# GC Metrics
aws cloudwatch get-metric-statistics \
  --namespace Analemma/GC \
  --metric-name BlocksDeleted \
  --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 300 \
  --statistics Sum
```

**2. Verify Deduplication:**

```python
# Query DynamoDB for block reuse
dynamodb = boto3.resource('dynamodb')
ref_table = dynamodb.Table('BlockReferenceCounts')

response = ref_table.scan(
    FilterExpression='ref_count > :count',
    ExpressionAttributeValues={':count': 1}
)

print(f"Blocks reused: {len(response['Items'])}")
print(f"Average ref_count: {sum(item['ref_count'] for item in response['Items']) / len(response['Items'])}")
```

**3. Monitor Legacy Mode Usage:**

```bash
# Count deprecated warnings
aws logs filter-log-events \
  --log-group-name /aws/lambda/SegmentRunner \
  --filter-pattern "[DEPRECATED] Legacy state storage" \
  --start-time $(date -u -d '1 day ago' +%s000)
```

---

## 🛠️ Troubleshooting

### Issue 1: "Manifest not found" error

**Symptom:**
```
ValueError: Manifest not found: abc12345-...
```

**Solution:**
```python
# Check DynamoDB WorkflowManifestsV3
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table('WorkflowManifestsV3-dev')

response = table.get_item(Key={'manifest_id': 'abc12345-...'})
if 'Item' not in response:
    print("❌ Manifest missing - TTL may have expired")
    print("✅ Solution: Extend TTL or recreate workflow")
```

### Issue 2: Dangling Block corruption

**Symptom:**
```
[GC] Block abc12345... not found in S3
```

**Solution:**
```python
# Check BlockReferenceCounts
ref_table = dynamodb.Table('BlockReferenceCounts')
response = ref_table.get_item(Key={'block_id': 'abc12345...'})

if 'Item' in response:
    ref_count = response['Item']['ref_count']
    print(f"Block ref_count: {ref_count}")
    
    if ref_count > 0:
        print("⚠️ Block deleted prematurely - Reference Counting bug")
        print("✅ Solution: Redeploy GC Lambda with atomic decrement fix")
```

### Issue 3: Legacy mode still active

**Symptom:**
```
[DEPRECATED] Legacy state storage detected
```

**Solution:**
```python
# Force Merkle DAG mode by recreating workflow
# 1. Export workflow config
workflow_config = get_workflow_config(workflow_id)

# 2. Delete old workflow
delete_workflow(workflow_id)

# 3. Recreate with same config (gets new manifest_id)
new_workflow_id = create_workflow(workflow_config)

print(f"✅ Migrated: {workflow_id} → {new_workflow_id}")
```

---

## 📝 Code Cleanup Checklist

### Safe to Remove (After verification)

- [ ] `bag['workflow_config']` in initialize_state_data.py (Line 490)
- [ ] `bag['partition_map']` in initialize_state_data.py (Line 491)
- [ ] `_resolve_segment_config()` in segment_runner_service.py (Line 3905-3995)
- [ ] Legacy fallback branch in segment_runner_service.py (Line 2934-2941)

### Keep (Required for compatibility)

- [x] StateManager.download_state_from_s3() (Used by existing workflows)
- [x] mask_pii_in_state() (Security requirement)
- [x] Fallback checks in execute_segment() (Graceful degradation)

---

## 🎯 Success Criteria

✅ **Migration Complete When:**

1. All new workflows use `manifest_id` (no legacy warnings)
2. BlockReferenceCounts table shows active ref_count management
3. GC Lambda processes TTL expiry events successfully
4. StateBag size reduced by ~90% in CloudWatch metrics
5. No "Manifest not found" errors in production logs

---

## 📅 Timeline

| Phase | Duration | Status |
|-------|----------|--------|
| Infrastructure Setup | Week 1 | ⏳ In Progress |
| Merkle DAG Enablement | Week 2 | ⏳ Pending |
| Verification | Week 3 | ⏳ Pending |
| Legacy Code Removal | 2026 Q3 | 📅 Scheduled |

---

## 🔗 References

- [state_versioning_service.py](analemma-workflow-os/backend/src/services/state/state_versioning_service.py)
- [merkle_gc_service.py](analemma-workflow-os/backend/src/services/state/merkle_gc_service.py)
- [REFACTOR_PLAN_WORKFLOW_CONFIG.md](REFACTOR_PLAN_WORKFLOW_CONFIG.md)
- [SEGMENT_PAYLOAD_OPTIMIZATION.md](SEGMENT_PAYLOAD_OPTIMIZATION.md)
