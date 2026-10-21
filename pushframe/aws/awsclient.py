from datetime import datetime, timedelta, timezone

import boto3
import botocore
from botocore.exceptions import ClientError
from loguru import logger

SESSION_CONFIG = botocore.config.Config(
    user_agent="aws-sdk-android/2.13.1 Linux/5.4.61-android11 Dalvik/2.1.0/0 en_US"
)

# The Cognito identity pool hands out TEMPORARY AWS credentials (default
# lifetime 1 h, `Credentials.Expiration`). They are passed to boto3 as static
# keys, which botocore never refreshes on its own -- so a long apply (the
# WriteBudget paces a 2555-item google-sync over hours) used to outlive them
# and die on `ExpiredToken` (debug session s3-expiredtoken-long-run, venus
# 2026-10-21). Every S3/SQS call now checks the expiry first and refreshes
# once the credentials are within this margin of it. Generous on purpose: it
# must cover one call's duration (an SQS long-poll is <= 20 s) plus any
# client/server clock drift, and a refresh is one cheap Cognito round trip.
CREDENTIAL_REFRESH_MARGIN = timedelta(minutes=5)

# botocore `ClientError` codes meaning "the temporary credentials are stale,
# fetch new ones" -- the remedy is a refresh, never a back-off:
#   ExpiredToken           S3 (venus 2026-10-21: "The provided token has
#                          expired." on PutObject); query-protocol services
#   ExpiredTokenException  the same condition from JSON-protocol services
#                          (SQS speaks JSON/awsQueryCompatible since botocore
#                          1.31, so either spelling may surface)
#   TokenRefreshRequired   S3: "The provided token must be refreshed."
#   RequestExpired         SigV4 request outside its validity window (clock
#                          skew, or a request signed with dying credentials)
EXPIRED_CREDENTIAL_ERROR_CODES = frozenset({
    'ExpiredToken', 'ExpiredTokenException', 'TokenRefreshRequired', 'RequestExpired',
})


def is_expired_credentials_error(exc: BaseException) -> bool:
    """True when `exc` is a botocore `ClientError` whose error code says the
    temporary AWS credentials expired (`EXPIRED_CREDENTIAL_ERROR_CODES`)."""
    if not isinstance(exc, ClientError):
        return False
    code = ((getattr(exc, 'response', None) or {}).get('Error') or {}).get('Code')
    return code in EXPIRED_CREDENTIAL_ERROR_CODES


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# TODO: Convert this to use os.environ vars?
class AWSClient:

    def __init__(self, pool_id: str, region_name='us-east-1', clock=None):
        # boto3.set_stream_logger('', logging.DEBUG)

        self.region_name = region_name
        self.pool_id = pool_id
        self.credentials = None
        # Injectable "now" (tz-aware) so the expiry logic is offline-testable.
        self.clock = clock or _utc_now
        self.cognito = boto3.client('cognito-identity', region_name=self.region_name)
        if pool_id:
            self.auth(pool_id)

    def auth(self, pool_id):
        # Subclasses extend this to (re)build their boto3 client from
        # `self.credentials` -- which is what makes `refresh_credentials()`
        # a full rebuild rather than a stale-client update.
        self.pool_id = pool_id
        ident_resp = self.cognito.get_id(IdentityPoolId=pool_id)
        cred_resp = self.cognito.get_credentials_for_identity(IdentityId=ident_resp['IdentityId'])
        self.credentials = cred_resp['Credentials']

    @property
    def credentials_expiration(self):
        """The tz-aware instant the current credentials expire, or None when
        Cognito did not say (then no proactive refresh is possible; the
        reactive one in `sync.execute_plan` still applies)."""
        expiration = (self.credentials or {}).get('Expiration')
        if not isinstance(expiration, datetime):
            return None
        if expiration.tzinfo is None:
            # botocore parses the timestamp tz-aware; a naive value is read
            # as UTC rather than crashing the comparison.
            expiration = expiration.replace(tzinfo=timezone.utc)
        return expiration

    def refresh_credentials(self):
        """Fetch fresh temporary credentials and rebuild the service client.
        Re-runs exactly the Cognito sequence every construction already
        performs (get_id + get_credentials_for_identity) -- the live-proven
        path, rather than an unprobed identity-reuse variant."""
        logger.info(f'Refreshing AWS temporary credentials ({type(self).__name__}, '
                    f'expiring {self.credentials_expiration})')
        self.auth(self.pool_id)

    def ensure_fresh_credentials(self):
        """Refresh proactively when the credentials expire within
        `CREDENTIAL_REFRESH_MARGIN` -- called before every S3/SQS call."""
        expiration = self.credentials_expiration
        if expiration is not None and self.clock() >= expiration - CREDENTIAL_REFRESH_MARGIN:
            self.refresh_credentials()
