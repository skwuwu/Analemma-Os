import os
import sys
import json
import importlib
import types


# Ensure handler reads the table name and does not try to use a real GSI; remove any OWNER_INDEX
os.environ['WORKFLOWS_TABLE'] = 'Workflows'
os.environ.pop('WORKFLOWS_OWNER_INDEX', None)


# Create a fake boto3 module with minimal dynamodb resource and conditions needed by the handler
class FakeConditions:
    class Attr:
        def __init__(self, name):
            self.name = name

        def eq(self, value):
            # return a simple dict the FakeTable.scan can understand
            return {'name': self.name, 'op': 'eq', 'value': value}


class FakeTable:
    def __init__(self):
        self._items = {}

    def put_item(self, Item=None, **kwargs):
        item = Item or kwargs.get('Item')
        # store by composite key ownerId|workflowId
        key = f"{item.get('ownerId')}|{item.get('workflowId')}"
        self._items[key] = item

    def scan(self, **kwargs):
        items = list(self._items.values())
        fe = kwargs.get('FilterExpression')
        if isinstance(fe, dict) and fe.get('op') == 'eq':
            items = [it for it in items if it.get(fe['name']) == fe['value']]
        return {'Items': items}

    def query(self, **kwargs):
        # Simulate query by KeyConditionExpression for ownerId and name equality or ownerId partition
        keycond = kwargs.get('KeyConditionExpression')
        items = []
        if isinstance(keycond, dict) and keycond.get('op') == 'eq' and keycond.get('name') == 'ownerId':
            owner = keycond.get('value')
            # return all items with this owner
            items = [it for it in self._items.values() if it.get('ownerId') == owner]
        return {'Items': items}


class FakeResource:
    def __init__(self):
        self._tables = {}

    def Table(self, name):
        if name not in self._tables:
            self._tables[name] = FakeTable()
        return self._tables[name]


def fake_resource(service_name=None, **kwargs):
    return FakeResource()


# Build module objects so `from boto3.dynamodb.conditions import Attr` works
boto3_mod = types.ModuleType('boto3')
boto3_mod.resource = fake_resource

dynamodb_mod = types.ModuleType('boto3.dynamodb')
conditions_mod = types.ModuleType('boto3.dynamodb.conditions')
setattr(conditions_mod, 'Attr', FakeConditions.Attr)
setattr(conditions_mod, 'Key', object)  # Add Key to avoid import error

sys.modules['boto3'] = boto3_mod
sys.modules['boto3.dynamodb'] = dynamodb_mod
sys.modules['boto3.dynamodb.conditions'] = conditions_mod


# Now import the handler module (it will use our fake boto3)
mod = importlib.import_module('get_workflow_by_name')


def test_get_workflow_by_name_returns_saved_workflow():
    # Get the fake table the module created and put an item
    table = mod.table
    config = {'name': 'My Test Workflow', 'nodes': [], 'edges': []}
    item = {
        'workflowId': 'wf-1',
        'ownerId': 'user-1',
        'config': json.dumps(config),
        'createdAt': 1,
        'updatedAt': 1,
        'is_scheduled': 'false',
        'next_run_time': None
    }
    table.put_item(Item=item)

    # Call handler
    event = {'queryStringParameters': {'ownerId': 'user-1', 'name': 'My Test Workflow'}}
    resp = mod.lambda_handler(event, None)
    assert resp['statusCode'] == 200
    body = json.loads(resp['body'])
    assert body['workflowId'] == 'wf-1'
    assert isinstance(body['config'], dict)
    assert body['config']['name'] == 'My Test Workflow'
