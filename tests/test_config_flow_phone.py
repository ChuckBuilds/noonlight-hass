"""Tests for contact phone validation in the config flow.

    pip install -r requirements_test.txt
    pytest tests
"""
import asyncio
import shutil
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from homeassistant import config_entries, core, loader  # noqa: E402
from homeassistant.helpers import (  # noqa: E402
    area_registry, category_registry, device_registry, entity_registry,
    floor_registry, issue_registry, label_registry,
)

from custom_components.noonlight.config_flow import _validate_phones  # noqa: E402


# --- the validator ----------------------------------------------------------------

@pytest.mark.parametrize("typed, stored", [
    ("18135550100", "18135550100"),
    ("+1 (813) 555-0100", "18135550100"),
    ("1-813-555-0100", "18135550100"),
    ("1.813.555.0100", "18135550100"),
    ("441632960961", "441632960961"),          # 12 digits, non-US country code
    ("123456789012345", "123456789012345"),    # 15 digits, the E.164 maximum
])
def test_valid_numbers_are_stored_as_digits(typed, stored):
    data = {"phone": typed}
    assert _validate_phones(data) == {}
    assert data["phone"] == stored


@pytest.mark.parametrize("typed", [
    "8135550100",          # no country code: the mistake this exists to catch
    "1234567890123456",    # 16 digits
    "abc18135550100",      # letters
    "1813555O100",         # letter O, not zero
    "١٨١٣٥٥٥٠١٠٠",         # Arabic-Indic digits: isdigit() is True, not ASCII
    "１８１３５５５０１００",  # fullwidth digits: isdigit() is True, not ASCII
])
def test_invalid_numbers_are_rejected(typed):
    data = {"phone": typed}
    assert _validate_phones(data) == {"phone": "invalid_phone"}
    assert data["phone"] == typed  # left as typed so the user can fix it


def test_blank_numbers_are_allowed():
    data = {"phone": "", "phone2": ""}
    assert _validate_phones(data) == {}
    assert _validate_phones({}) == {}


def test_secondary_phone_is_checked_too():
    data = {"phone": "18135550100", "phone2": "555-0100"}
    assert _validate_phones(data) == {"phone2": "invalid_phone"}
    assert data["phone"] == "18135550100"


# --- the real config flow -------------------------------------------------------------

DISPATCH = {"id": "", "secret": "", "api_endpoint": "https://api.noonlight.com/dispatch/v1",
            "token_endpoint": "https://noonlight.konnected.io/ha/token", "pin": "", "name2": "",
            "instructions": ""}
LOCATION = {"address1": "1 Main St", "city": "X", "state": "FL", "zip": "00000"}


def run_flow_test(tmp_path, body):
    """Run `body(hass, flow)` with the integration loadable as a custom component."""
    shutil.copytree(REPO / "custom_components" / "noonlight",
                    tmp_path / "custom_components" / "noonlight")

    async def go():
        hass = core.HomeAssistant(str(tmp_path))
        loader.async_setup(hass)
        for reg in (category_registry, floor_registry, label_registry, area_registry,
                    device_registry, entity_registry, issue_registry):
            await reg.async_load(hass)
        hass.config.skip_pip = True
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()

        async def no_deps(*args, **kwargs):
            return None

        # The flow itself is under test; skip setting up http/switch and the entry.
        with patch.object(config_entries, "async_process_deps_reqs", no_deps), \
             patch.object(config_entries.ConfigEntries, "async_setup", return_value=True), \
             patch.object(config_entries.ConfigEntries, "async_reload", return_value=True):
            await body(hass, hass.config_entries.flow)
        await hass.async_stop(force=True)

    asyncio.run(go())


async def to_dispatch_step(flow):
    r = await flow.async_init("noonlight", context={"source": "user"})
    r = await flow.async_configure(r["flow_id"], {"name": "Owner", "location_mode": "address"})
    return await flow.async_configure(r["flow_id"], LOCATION)


def test_setup_rejects_then_accepts(tmp_path):
    async def body(hass, flow):
        r = await to_dispatch_step(flow)
        assert r["step_id"] == "v2_dispatch"
        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "8135550100", "phone2": ""})
        assert r["type"] == "form" and r["errors"] == {"phone": "invalid_phone"}
        assert not hass.config_entries.async_entries("noonlight")
        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "+1 (813) 555-0100", "phone2": ""})
        assert r["type"] == "create_entry"
        assert r["data"]["phone"] == "18135550100" and r["data"]["phone2"] == ""
    run_flow_test(tmp_path, body)


def test_setup_rejects_non_ascii_digits(tmp_path):
    async def body(hass, flow):
        r = await to_dispatch_step(flow)
        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "١٨١٣٥٥٥٠١٠٠", "phone2": ""})
        assert r["type"] == "form" and r["errors"] == {"phone": "invalid_phone"}
    run_flow_test(tmp_path, body)


def test_reconfigure_rejects_and_keeps_entry_unchanged(tmp_path):
    async def body(hass, flow):
        r = await to_dispatch_step(flow)
        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "18135550100", "phone2": ""})
        entry = hass.config_entries.async_entries("noonlight")[0]

        r = await flow.async_init("noonlight", context={"source": "reconfigure", "entry_id": entry.entry_id})
        r = await flow.async_configure(r["flow_id"], {"name": "Owner", "location_mode": "address"})
        r = await flow.async_configure(r["flow_id"], LOCATION)
        assert r["step_id"] == "reconfig_v2_dispatch"
        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "8135550100", "phone2": ""})
        assert r["type"] == "form" and r["errors"] == {"phone": "invalid_phone"}
        assert entry.data["phone"] == "18135550100"

        r = await flow.async_configure(r["flow_id"], {**DISPATCH, "phone": "1-813-555-0199", "phone2": "18135550111"})
        assert r["type"] == "abort" and r["reason"] == "reconfigure_successful"
        assert entry.data["phone"] == "18135550199" and entry.data["phone2"] == "18135550111"
    run_flow_test(tmp_path, body)
