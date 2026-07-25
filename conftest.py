# Empty of fixtures on purpose. Its presence puts the project root on sys.path
# so tests can import the packages (pipeline, servers, orchestrator, gateway).
#
# It also restricts anyio to the asyncio backend, so async tests run once rather
# than also under trio (which is not installed).
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"