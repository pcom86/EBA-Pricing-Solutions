from datetime import UTC, datetime
from pathlib import Path


class S3Error(Exception):
	def __init__(self, code):
		super().__init__(str(code))
		self.response = {"Error": {"Code": str(code)}}


class FakeS3:
	def __init__(self):
		self.data = {}
		self.modified = {}
		self.uploads = []
		self.list_requests = []
		self.download_error = None
		self.upload_error = None
		self.head_error = None

	def seed(self, bucket, key, data=b"data", modified=None):
		self.data[bucket, key] = data
		self.modified[bucket, key] = modified or datetime(2026, 1, 1, tzinfo=UTC)

	def upload_file(self, filename, bucket, key):
		if self.upload_error:
			raise self.upload_error
		self.seed(bucket, key, Path(filename).read_bytes())
		self.uploads.append((bucket, key))

	def download_file(self, bucket, key, filename):
		if self.download_error:
			Path(filename).write_bytes(b"partial")
			raise self.download_error
		if (bucket, key) not in self.data:
			raise S3Error("NoSuchKey")
		Path(filename).write_bytes(self.data[bucket, key])

	def head_object(self, Bucket, Key):
		if self.head_error:
			raise self.head_error
		if (Bucket, Key) not in self.data:
			raise S3Error("404")
		return {}

	def get_paginator(self, operation):
		if operation != "list_objects_v2":
			raise AssertionError(operation)
		return self

	def paginate(self, Bucket, Prefix):
		self.list_requests.append((Bucket, Prefix))
		objects = [{"Key": key, "LastModified": self.modified[bucket, key]}
				   for bucket, key in sorted(self.data) if bucket == Bucket and key.startswith(Prefix)]
		for start in range(0, len(objects), 2):
			yield {"Contents": objects[start:start + 2]}
		if not objects:
			yield {}
