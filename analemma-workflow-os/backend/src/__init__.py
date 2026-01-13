"""Backend package initializer.

This module provides a tiny test-time shim to ensure that test suites which
monkeypatch `boto3.client` or expect `backend` modules to be importable in
restricted environments don't fail at import time. It does not change runtime
behavior when boto3 is fully installed.
"""
try:
	import boto3
	# ensure boto3.client exists (some trimmed environments may provide a
	# minimal boto3 without client/resource convenience functions)
	if not hasattr(boto3, 'client'):
		def _fake_client(*args, **kwargs):
			raise RuntimeError('boto3.client is unavailable in this environment')
		boto3.client = _fake_client
except Exception:
	# If boto3 itself isn't importable, tests typically monkeypatch boto3
	# before importing backend modules, so we ignore import errors here.
	pass
