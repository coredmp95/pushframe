import sys
import os
import time

from PIL import Image
from loguru import logger
from tqdm import tqdm

from pushframe.api.accountApi import AccountApi
from pushframe.api.activityApi import ActivityApi
from pushframe.api.assetApi import AssetApi
from pushframe.api.frameApi import FrameApi
from pushframe.api.peopleApi import PeopleApi
from pushframe.aws.s3client import S3Client
from pushframe.aws.sqsclient import SQSClient
from pushframe.client import Client, WriteEndpointError
from pushframe.exif import ExifWriter
from pushframe.export import get_image_from_asset
from pushframe.models.asset import Asset, AssetPartialId
from pushframe.utils.io import build_path, write_model

# MOD-04 (Phase 19): process-level guard against the loguru sink leak.
# `_init_logger()` used to add a stderr sink AND a `logs/file_{time}.log`
# file sink on EVERY `Aura()` construction, so a process that builds several
# instances (CLIs, tests via offline_aura) spawned one log file per instance
# and accumulated duplicate sinks. The FIRST construction in a process
# configures logging; subsequent ones are no-ops for sinks. This is a
# module-level flag on purpose (NOT a per-instance one — distinct instances
# were the leak), and it deliberately performs no wholesale sink teardown:
# cli.py's _configure_cli_logging() stays the single reconfigure point.
_LOGGER_READY = False


class Aura:

    def __init__(self, client: Client | None = None):
        self._init_logger()
        self._client = client or Client()
        self.account_api = AccountApi(self._client)
        self.frame_api = FrameApi(self._client)
        self.people_api = PeopleApi(self._client)
        self.activity_api = ActivityApi(self._client)
        self.asset_api = AssetApi(self._client)
        self.exif_writer = ExifWriter()

    def login(self, email: str = None, password: str = None):
        """

        :param email: The email of the account to authenticate with, defaults to ENVIRON['AURA_EMAIL']
        :param password: The password of the account to authenticate with, defaults to ENVIRON['AURA_PASSWORD']
        :return: Authenticated Aura object
        """
        if email is None:
            email = os.getenv('PUSHFRAME_EMAIL') or os.getenv('AURA_EMAIL')
        if password is None:
            password = os.getenv('PUSHFRAME_PASSWORD') or os.getenv('AURA_PASSWORD')

        user = self.account_api.login(email, password)

        self._client.add_default_headers({
            'x-token-auth': user.auth_token,
            'x-user-id': user.id
        })
        # Session facts for the config wizard (phase 23): the wizard stores
        # these so later commands can RESUME the session without a password
        # (roadmap §23 criterion 1; full token-first lands in phase 24).
        self.auth_token = user.auth_token
        self.user_id = user.id

        return self

    def resume_session(self, email: str = None, auth_token: str = None,
                       user_id: str = None):
        """Attach a previously-established session (phase 23): sets the auth
        headers from stored {email, auth_token, user_id} config WITHOUT any
        login call. Env vars stay the override path (criterion 2); `status`
        falls back to this only when no password-bearing env is set.

        auth_token is mandatory — a session without it cannot authenticate;
        the caller checks the config before deciding to resume.
        """
        if not auth_token:
            raise ValueError('resume_session needs a stored auth_token')
        self._client.add_default_headers({
            'x-token-auth': auth_token,
            'x-user-id': user_id or ''
        })
        self.auth_token = auth_token
        self.user_id = user_id
        return self

    def main(self):
        pass

    def get_all_assets(self, frame_id: str, limit: int = 1000, page_delay: float = 0.0):
        paginated_assets, cursor = self.frame_api.get_assets(frame_id, limit=limit)
        assets = paginated_assets
        while cursor:
            paginated_assets, cursor = self.frame_api.get_assets(frame_id, limit=limit, cursor=cursor)
            if page_delay:
                time.sleep(page_delay)
            assets.extend(paginated_assets)

        return assets

    def dump_frame(self, frame_id: str, path: str, download_images: bool = True, download_activities: bool = True):
        frame, _ = self.frame_api.get_frame(frame_id)
        frame_dir = build_path(path, f'{frame.name}-{frame.id}/')

        write_model(frame, build_path(frame_dir, 'frame.json'))

        if download_activities:
            activities = self.frame_api.get_activities(frame.id)
            write_model(activities, build_path(frame_dir, 'activities.json'))

        assets = self.get_all_assets(frame_id)
        write_model(assets, build_path(frame_dir, 'assets.json'))

        if download_images:
            self.download_images_from_assets(assets, build_path(frame_dir, f'asset_images/'))

    def download_images_from_assets(self, assets: list[Asset], base_path: str):
        failed_to_retrieve = []
        for asset in tqdm(assets):
            try:
                get_image_from_asset(asset, base_path, self.exif_writer)
            except Exception as e:
                failed_to_retrieve.append(asset)
        if len(failed_to_retrieve) > 0:
            logger.debug(f'Failed to retrieve {len(failed_to_retrieve)} assets.')

    def clone(self, source_frame, target_frame):
        # TODO: Read assets from one frame and immediately clones them to another (in memory)
        pass

    def upload_images(self):
        # TODO: tqdm, for each asset, upload_image
        # TODO: How do we know where the original dumped asset images are?
        #       Could rebuild the file path or store it in assets.json
        pass

    def upload_image(self, frame_id: str, image_path: str, asset: Asset):
        """
        Uploads a single image to S3 and associates it with the given frame via the legacy
        single-item path (`select_asset` + `batch_update`), distinct from the batched multi-file
        path `sync.execute_plan` uses.

        :param frame_id: The frame to associate the uploaded asset with.
        :param image_path: Local filesystem path to the image file to upload.
        :param asset: Asset metadata to update after the S3 upload lands.
        :raises WriteEndpointError: if `batch_update` does not acknowledge this asset's
            local_identifier -- REL-07/D-18: `batch_update`'s `unacknowledged` set is a real
            per-item failure signal, not something this legacy caller may silently discard.
        """
        try:
            image = Image.open(image_path)
        except Exception as e:
            logger.error(e)
            return
        local_identifier = asset.local_identifier
        self.frame_api.select_asset(frame_id, AssetPartialId(local_identifier=local_identifier))
        queue_url = self.get_sqs(frame_id)
        self.sqsClient.receive_message(queue_url, wait_time_seconds=5)
        self.frame_api.select_asset(frame_id, AssetPartialId(local_identifier=local_identifier))
        client = S3Client()
        filename, md5 = client.upload_file(
            open(image_path, 'rb').read(), '.jpg')

        asset.file_name = filename
        asset.md5_hash = md5
        asset.height = image.height
        asset.width = image.width

        batch_result = self.asset_api.batch_update(asset)
        if batch_result.unacknowledged:
            raise WriteEndpointError(
                f"upload_image: frame {frame_id} -- batch_update did not acknowledge "
                f"local_identifier(s) {batch_result.unacknowledged}"
            )
        message = self.sqsClient.receive_message(queue_url, wait_time_seconds=5)
        print(message)

    def get_sqs(self, frame_id: str):
        self.sqsClient = SQSClient()
        return self.sqsClient.get_queue_url(frame_id)

    def _init_logger(self):
        """Configure the process's loguru sinks exactly once (MOD-04, D-03):
        the first Aura() construction registers the stderr + file sinks; any
        further construction is a sink no-op. Performs no teardown — cli.py's
        _configure_cli_logging() owns wholesale reconfiguration.
        """
        global _LOGGER_READY
        if _LOGGER_READY:
            return
        # Ensure the loguru file sink's target dir exists before the first
        # instantiation so it does not raise FileNotFoundError (D-08).
        os.makedirs('logs/', exist_ok=True)
        # (The old commented-out "set this to debug if needed" sink-teardown
        # vestige is gone: with the process guard, tearing down every sink on
        # a later construction would silence the process for good.)
        logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                                                    "<level>{level: <8}</level> | "
                                                    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                                                    "<level>{message}</level> Context: {extra}")
        logger.add('logs/file_{time}.log')
        _LOGGER_READY = True
