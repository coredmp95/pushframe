import os

import pytest
from dotenv import load_dotenv

# Load AURA_EMAIL / AURA_PASSWORD from a local .env so the live read-path tests
# can run without exporting shell vars. Shell-exported vars still win (override
# defaults to False), and a missing .env is a no-op — so a credential-less
# checkout still skips the live suite cleanly (D-02).
load_dotenv()


@pytest.fixture(scope="session")
def aura():
    """Authenticated Aura session shared by all live read-path tests.

    Reads AURA_EMAIL/AURA_PASSWORD from the environment and skips the whole
    live suite cleanly when either is unset (D-02), so a credential-less
    checkout stays green. When credentials are present it instantiates Aura()
    and performs the login (READ-01's act, D-03), returning the authenticated
    instance so all live tests reuse one session.
    """
    email = os.getenv("AURA_EMAIL")
    password = os.getenv("AURA_PASSWORD")
    if not email or not password:
        pytest.skip("AURA_EMAIL/AURA_PASSWORD not set; skipping live Aura API tests")

    from pushframe.aura import Aura

    instance = Aura()
    instance.login()
    return instance
