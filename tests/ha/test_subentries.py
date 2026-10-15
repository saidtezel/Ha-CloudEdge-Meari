"""One account entry with a camera subentry per camera, on a real HA core."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryDisabler
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from cloudplus_helpers import ACCOUNT_DATA, account_entry
from custom_components.cloudplus.const import (
    CONF_KNOWN_CAMERAS,
    CONF_STREAM_HOST_MODE,
    CONF_VIDEO_PASSWORD,
    DOMAIN,
)

def legacy_camera_entry(account: MockConfigEntry | None, device: dict, **kwargs) -> MockConfigEntry:
    data = {
        **ACCOUNT_DATA,
        "sn_num": device["snNum"],
        "device_id": device["deviceID"],
        "device_name": device["deviceName"],
        "device": dict(device),
    }
    if account is not None:
        data["account_entry_id"] = account.entry_id
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"{device['deviceName']} (user@example.invalid)",
        data=data,
        unique_id=device["snNum"],
        version=2,
        **kwargs,
    )


def subentries_by_sn(entry) -> dict:
    return {sub.unique_id: sub for sub in entry.subentries.values()}


async def setup(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()


async def test_new_account_adds_a_subentry_and_device_per_camera(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    await setup(hass)

    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    subs = subentries_by_sn(entry)
    assert set(subs) == {"SN-FRONT", "SN-BACK"}
    assert subs["SN-FRONT"].title == "Front"
    assert sorted(entry.data[CONF_KNOWN_CAMERAS]) == ["SN-BACK", "SN-FRONT"]

    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    for sn, sub in subs.items():
        device = dev_reg.async_get_device(identifiers={(DOMAIN, sn)})
        assert device.config_entries_subentries == {entry.entry_id: {sub.subentry_id}}
        camera = ent_reg.async_get(
            ent_reg.async_get_entity_id("camera", DOMAIN, f"{sn}_camera")
        )
        assert camera.config_subentry_id == sub.subentry_id
        assert camera.device_id == device.id
        assert camera.disabled_by is None  # mains-powered BACK included


async def test_deleted_camera_stays_deleted(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    await setup(hass)
    back = subentries_by_sn(entry)["SN-BACK"]

    hass.config_entries.async_remove_subentry(entry, back.subentry_id)
    await hass.async_block_till_done()  # the update listener reloads the entry

    assert set(subentries_by_sn(entry)) == {"SN-FRONT"}
    assert dr.async_get(hass).async_get_device(identifiers={(DOMAIN, "SN-BACK")}) is None
    assert er.async_get(hass).async_get_entity_id("camera", DOMAIN, "SN-BACK_camera") is None


async def test_add_camera_flow_offers_only_missing_cameras(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    await setup(hass)
    hass.config_entries.async_remove_subentry(
        entry, subentries_by_sn(entry)["SN-BACK"].subentry_id
    )
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "camera"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    options = result["data_schema"].schema["sn_num"].config["options"]
    assert [option["value"] for option in options] == ["SN-BACK"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"sn_num": "SN-BACK", CONF_VIDEO_PASSWORD: "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    back = subentries_by_sn(entry)["SN-BACK"]
    assert back.data[CONF_VIDEO_PASSWORD] == "pw"
    assert er.async_get(hass).async_get_entity_id("camera", DOMAIN, "SN-BACK_camera")

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "camera"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_new_cameras"


async def test_reconfigure_video_password_reloads(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    await setup(hass)
    front = subentries_by_sn(entry)["SN-FRONT"]
    runtime_before = hass.data[DOMAIN][entry.entry_id]

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "camera"),
        context={"source": SOURCE_RECONFIGURE, "subentry_id": front.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_VIDEO_PASSWORD: "new-pw"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert entry.subentries[front.subentry_id].data[CONF_VIDEO_PASSWORD] == "new-pw"
    runtime_after = hass.data[DOMAIN][entry.entry_id]
    assert runtime_after is not runtime_before
    assert runtime_after.coordinators[front.subentry_id]._video_password == "new-pw"


async def test_live_setting_saves_to_subentry_without_reload(hass: HomeAssistant):
    entry = account_entry()
    entry.add_to_hass(hass)
    await setup(hass)
    front = subentries_by_sn(entry)["SN-FRONT"]
    runtime_before = hass.data[DOMAIN][entry.entry_id]
    entity_id = er.async_get(hass).async_get_entity_id(
        "select", DOMAIN, "SN-FRONT_stream_host_mode"
    )

    await hass.services.async_call(
        "select", "select_option", {"entity_id": entity_id, "option": "IP Address"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert entry.subentries[front.subentry_id].data[CONF_STREAM_HOST_MODE] == "ip"
    assert hass.data[DOMAIN][entry.entry_id] is runtime_before
    assert hass.states.get(entity_id).state == "IP Address"


async def test_legacy_camera_entries_fold_into_account(hass: HomeAssistant, cloud_devices):
    account = account_entry(version=2)
    account.add_to_hass(hass)
    front = legacy_camera_entry(
        account, cloud_devices[0],
        options={CONF_VIDEO_PASSWORD: "pw", CONF_STREAM_HOST_MODE: "ip"},
    )
    front.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=front.entry_id, identifiers={(DOMAIN, "SN-FRONT")}, name="Front"
    )
    camera = ent_reg.async_get_or_create(
        "camera", DOMAIN, "SN-FRONT_camera",
        config_entry=front, device_id=device.id, suggested_object_id="my_front_door",
    )
    await setup(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert entries == [account]
    assert account.version == 3
    sub = subentries_by_sn(account)["SN-FRONT"]
    assert sub.data[CONF_VIDEO_PASSWORD] == "pw"
    assert sub.data[CONF_STREAM_HOST_MODE] == "ip"
    # BACK had no legacy entry but is new on the account: added as well
    assert "SN-BACK" in subentries_by_sn(account)

    moved = ent_reg.async_get("camera.my_front_door")
    assert moved is not None and moved.id == camera.id
    assert moved.config_entry_id == account.entry_id
    assert moved.config_subentry_id == sub.subentry_id
    assert ent_reg.async_get_entity_id("camera", DOMAIN, "SN-FRONT_camera") == "camera.my_front_door"
    device = dev_reg.async_get(device.id)
    assert device.config_entries_subentries == {account.entry_id: {sub.subentry_id}}
    coord = hass.data[DOMAIN][account.entry_id].coordinators[sub.subentry_id]
    assert coord._video_password == "pw"
    assert coord.stream_host_mode == "ip"


async def test_user_disabled_legacy_camera_stays_off(hass: HomeAssistant, cloud_devices):
    account = account_entry(version=2)
    account.add_to_hass(hass)
    back = legacy_camera_entry(account, cloud_devices[1], disabled_by=ConfigEntryDisabler.USER)
    back.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get_or_create(
        config_entry_id=back.entry_id, identifiers={(DOMAIN, "SN-BACK")}, name="Back"
    )
    await setup(hass)

    sub = subentries_by_sn(account)["SN-BACK"]
    assert dev_reg.async_get(device.id).disabled_by is dr.DeviceEntryDisabler.USER
    assert sub.subentry_id not in hass.data[DOMAIN][account.entry_id].coordinators
    assert hass.data[DOMAIN][account.entry_id].coordinators  # FRONT still runs


async def test_orphan_legacy_camera_becomes_the_account(hass: HomeAssistant, cloud_devices):
    front = legacy_camera_entry(None, cloud_devices[0])
    front.add_to_hass(hass)
    await setup(hass)

    entries = hass.config_entries.async_entries(DOMAIN)
    assert entries == [front]
    assert "sn_num" not in front.data
    assert front.unique_id == "user@example.invalid"
    assert front.version == 3
    assert set(subentries_by_sn(front)) == {"SN-FRONT", "SN-BACK"}


async def test_camera_shared_with_second_account_is_added_once(hass: HomeAssistant):
    first = account_entry()
    first.add_to_hass(hass)
    second = MockConfigEntry(
        domain=DOMAIN,
        title="CloudEdge / Meari (family@example.invalid)",
        data={**ACCOUNT_DATA, "email": "family@example.invalid"},
        unique_id="family@example.invalid",
        version=3,
    )
    second.add_to_hass(hass)
    await setup(hass)

    owners = [
        entry.entry_id
        for entry in (first, second)
        for sub in entry.subentries.values()
        if sub.unique_id == "SN-FRONT"
    ]
    assert len(owners) == 1
    camera_ids = [
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), owners[0])
        if e.domain == "camera"
    ]
    assert len(camera_ids) == 2
    assert len(hass.states.async_entity_ids("camera")) == 2
