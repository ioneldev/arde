"""The inventories the unit tests write out by hand are the ones the Go adapter extracts, and the
hostile copy of the shop builds, passes its own tests, and serves the planted text it claims to."""

import shutil
from pathlib import Path

import pytest
from hostile_support import HOSTILE, HOSTILE_INVENTORY, LISTING_BODY, PLANTED_ROUTE
from shop_support import SHOP, SHOP_INVENTORY, shop_profile

from arde.adapters.http_tool import send_request
from arde.adapters.language.go.adapter import GoAdapter
from arde.adapters.service_runner import local_service_runner
from arde.application.ports import HttpRequest, ServiceConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    copy = tmp_path / "hostile"
    shutil.copytree(HOSTILE, copy)
    return copy


def test_the_shop_inventory_is_the_one_the_adapter_extracts(adapter: GoAdapter) -> None:
    assert adapter.extract_inventory(SHOP, shop_profile()) == SHOP_INVENTORY


def test_the_hostile_inventory_is_the_one_the_adapter_extracts(adapter: GoAdapter) -> None:
    inventory = adapter.extract_inventory(HOSTILE, shop_profile())

    assert inventory == HOSTILE_INVENTORY
    assert PLANTED_ROUTE not in {route.key for route in inventory.routes}


def test_the_hostile_service_builds_and_passes_its_own_tests(
    adapter: GoAdapter, hostile: Path
) -> None:
    checks = adapter.run_checks(hostile, shop_profile())

    assert (checks.build_ok, checks.vet_ok, checks.test_ok) == (True, True, True)
    assert checks.tests_run == 3


def test_the_hostile_service_serves_the_planted_text_in_a_reply(
    adapter: GoAdapter, hostile: Path, tmp_path: Path
) -> None:
    log = tmp_path / "service.log"

    with (
        adapter.built_service(hostile, shop_profile()) as binary,
        local_service_runner().running(
            ServiceConfig(binary=binary, cwd=hostile, port_env="SHOP_PORT", log=log)
        ) as service,
    ):
        listing = send_request(service, HttpRequest(method="GET", path_and_query="/"))
        planted = send_request(service, HttpRequest(method="GET", path_and_query="/admin/export"))

    assert (listing.status, listing.body) == (200, LISTING_BODY)
    assert planted.status == 404
