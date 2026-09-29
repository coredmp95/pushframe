import base64
import boto3
import uuid
import hashlib

from pushframe.aws.awsclient import AWSClient
from pushframe.utils import settings

# MOD-02 (Phase 19): the bucket name and pool ID live in settings.py.
# Phase 23: accessed DYNAMICALLY (config file can now change them) —
# module-level aliases would freeze the import-time value.
def bucket_key() -> str:
    return settings.AWS_S3_BUCKET


def upload_identity_pool_id() -> str:
    return settings.AWS_UPLOAD_IDENTITY_POOL_ID
AWS_UPLOAD_PART_SIZE = 16384


def get_md5(data):
    return base64.b64encode(hashlib.md5(data).digest()).decode('utf-8')


class S3Client(AWSClient):
    s3_client = None

    def __init__(self, pool_id=None, region_name='us-east-1'):
        super().__init__(pool_id if pool_id else upload_identity_pool_id(), region_name)

    def auth(self, pool_id):
        super().auth(pool_id)
        self.s3_client = boto3.client('s3', aws_access_key_id=self.credentials['AccessKeyId'],
                                      aws_secret_access_key=self.credentials['SecretKey'],
                                      aws_session_token=self.credentials['SessionToken'])

    def upload_file(self, data, extension):
        filename = f'{str(uuid.uuid4())}{extension}'
        self.s3_client.put_object(Body=data, Bucket=bucket_key(), Key=filename)

        return filename, get_md5(data)

    def get_file(self, filename):
        return self.s3_client.head_object(Bucket=bucket_key(), Key=filename)
