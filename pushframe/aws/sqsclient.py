import boto3

from pushframe.aws.awsclient import AWSClient
from pushframe.utils.settings import AWS_SQS_IDENTITY_POOL_ID

# MOD-02 (Phase 19): the pool ID moved to settings.py (env-overridable,
# unchanged default). Kept as an alias so existing references keep working;
# no literal remains in this module.
SQS_IDENTITY_POOL_ID = AWS_SQS_IDENTITY_POOL_ID


# TODO: Might want to thread this out, as the wait time could be an issue
class SQSClient(AWSClient):
    sqs_client: None

    def __init__(self, pool_id=None, region_name='us-east-1'):
        super().__init__(pool_id if pool_id else SQS_IDENTITY_POOL_ID, region_name)

    def auth(self, pool_id):
        super().auth(pool_id)
        self.sqs_client = boto3.client('sqs', aws_access_key_id=self.credentials['AccessKeyId'],
                                       aws_secret_access_key=self.credentials['SecretKey'],
                                       aws_session_token=self.credentials['SessionToken'],
                                       region_name=self.region_name)

    def get_queue_url(self, frame_id: str):
        return self.sqs_client.get_queue_url(QueueName=f'frame-{frame_id}-client').get('QueueUrl')

    def receive_message(self, queue_url: str, max_num_messages: int = 10, wait_time_seconds: int = 20):
        response = self.sqs_client.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=max_num_messages,
                                                   WaitTimeSeconds=wait_time_seconds, AttributeNames=['All'])

        return response
