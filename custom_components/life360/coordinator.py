"""DataUpdateCoordinator for the Life360 integration."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Coroutine, Iterable
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from functools import partial
import logging
from math import ceil
from typing import Any, TypeVar, TypeVarTuple, cast
import uuid

from aiohttp import ClientSession
from life360 import Life360Error, LoginError, NotFound, NotModified, RateLimited

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import helpers
from .const import (
    API_BASE_URL,
    API_USER_AGENT,
    COMM_MAX_RETRIES,
    COMM_TIMEOUT,
    DOMAIN,
    LOGIN_ERROR_RETRY_DELAY,
    LTD_LOGIN_ERROR_RETRY_DELAY,
    MAX_LTD_LOGIN_ERROR_RETRIES,
    SIGNAL_ACCT_STATUS,
    SIGNAL_DEVICES_CHANGED,
    UPDATE_INTERVAL,
)
from .helpers import (
    AccountID,
    CircleData,
    CircleID,
    CirclesMembersData,
    ConfigOptions,
    DeviceData,
    DeviceID,
    Life360Store,
    MemberData,
    MemberDetails,
    MemberID,
    NoLocReason,
)

_LOGGER = logging.getLogger(__name__)

_R = TypeVar("_R")
_Ts = TypeVarTuple("_Ts")


@dataclass
class AccountData:
    """Data for a Life360 account."""

    session: ClientSession
    api: helpers.Life360
    failed: asyncio.Event
    failed_task: asyncio.Task
    online: bool = True


class LoginRateLimitErrResp(Enum):
    """Response to Login or RateLimited errors."""

    LTD_LOGIN_ERROR_RETRY = auto()
    RETRY = auto()
    SILENT = auto()


class RequestError(Enum):
    """Request error type."""

    NOT_FOUND = auto()
    NOT_MODIFIED = auto()
    NO_DATA = auto()


class CirclesMembersDataUpdateCoordinator(DataUpdateCoordinator[CirclesMembersData]):
    """Circles & Members data update coordinator."""

    config_entry: ConfigEntry
    _bg_update_task: asyncio.Task | None = None
    _fg_update_task: asyncio.Task | None = None

    def __init__(self, hass: HomeAssistant, store: Life360Store) -> None:
        """Initialize data update coordinator."""
        super().__init__(hass, _LOGGER, name="Circles & Members")
        self._store = store
        self.data = self._data_from_store()
        self._options = ConfigOptions.from_dict(self.config_entry.options)
        self._acct_data: dict[AccountID, AccountData] = {}
        self._create_acct_data(self._options.accounts)
        self._client_request_ok = asyncio.Event()
        self._client_request_ok.set()
        self._client_tasks: set[asyncio.Task] = set()

        self.config_entry.async_on_unload(
            self.config_entry.add_update_listener(self._config_entry_updated)
        )

        # Cache for Tile authentication keys (Life360 device ID -> auth key bytes)
        self._tile_auth_cache: dict[str, bytes] = {}
        # Cache for Tile BLE device IDs (Life360 device ID -> BLE device ID)
        self._tile_ble_id_cache: dict[str, str] = {}
        # Cache for device names (Life360 device ID -> name)
        self._device_name_cache: dict[str, str] = {}
        # Cache for device avatars (Life360 device ID -> avatar URL)
        self._device_avatar_cache: dict[str, str] = {}
        # Cache for device categories (Life360 device ID -> category)
        self._device_category_cache: dict[str, str] = {}
        # Cache for registered device ID (for API requests requiring x-device-id)
        self._registered_device_id: str | None = None
        self._device_registration_attempted: bool = False

    async def async_shutdown(self) -> None:
        """Cancel any scheduled call, and ignore new runs."""
        await super().async_shutdown()
        # Now that no new tasks should be created, stop any ongoing ones.
        await self._stop_tasks()
        self._delete_acct_data(list(self._acct_data))

    def acct_online(self, aid: AccountID) -> bool:
        """Return if account is online."""
        # When config updates and there's a new, enabled account, binary sensor could
        # get created before coordinator finishes updating from the same event. In that
        # case, just return True. If/when the account is determined to be offline, the
        # binary sensor will be updated accordingly.
        if aid not in self._acct_data:
            return True
        return self._acct_data[aid].online

    # Once supporting only HA 2024.5 or newer, change to @cached_property and clear
    # cache (i.e., if hasattr(self, "mem_circles"): delattr(self, "mem_circles"))
    # in _async_refresh_finished override, after call to async_set_updated_data and in
    # _config_entry_updated after updating self.data.circles.
    @property
    def mem_circles(self) -> dict[MemberID, set[CircleID]]:
        """Return Circles Members are in."""
        return {
            mid: {
                cid
                for cid, circle_data in self.data.circles.items()
                if mid in circle_data.mids
            }
            for mid in self.data.mem_details
        }

    async def update_member_location(self, mid: MemberID) -> None:
        """Request Member location update."""
        # Member may no longer be available before corresponding device_tracker entity
        # has been removed.
        if mid not in self.data.mem_details:
            return
        name = self.data.mem_details[mid].name
        # Member may be in more than one Circle, and each of those Circles might be
        # accessible from more than one account. So try each Circle/account combination
        # until one works.
        for cid in self.mem_circles[mid]:
            circle_data = self.data.circles[cid]
            for aid in circle_data.aids:
                api = self._acct_data[aid].api
                result = await self._client_request(
                    aid,
                    api.request_circle_member_location_update,
                    cid,
                    mid,
                    msg=(
                        f"while requesting location update for {name} "
                        f"via {circle_data.name} Circle"
                    ),
                )
                if not isinstance(result, RequestError):
                    return

        _LOGGER.error("Could not update location of %s", name)

    async def get_raw_member_data(
        self, mid: MemberID
    ) -> dict[CircleID, dict[str, Any] | RequestError] | None:
        """Get raw Member data from each Circle Member is in."""
        # Member may no longer be available before corresponding device_tracker entity
        # has been removed.
        if mid not in self.data.mem_details:
            return None
        cids = self.mem_circles[mid]
        raw_member_list = await asyncio.gather(
            *(self._get_raw_member(mid, cid) for cid in cids)
        )
        return dict(zip(cids, raw_member_list, strict=True))

    def _data_from_store(self) -> CirclesMembersData:
        """Get Circles & Members from storage."""
        if not self._store.loaded_ok:
            _LOGGER.warning(
                "Could not load Circles & Members from storage"
                "; will wait for data from server"
            )
            return CirclesMembersData()
        return CirclesMembersData(self._store.circles, self._store.mem_details)

    async def _async_update_data(self) -> CirclesMembersData:
        """Fetch the latest data from the source."""
        done_msg = "Circles & Members list retrieval %s"
        assert not self._fg_update_task
        self._fg_update_task = asyncio.current_task()
        try:
            data, complete = await self._update_data(retry=False)
            if not complete:
                _LOGGER.warning(
                    "Could not retrieve full Circles & Members list from server"
                    "; will retry"
                )

                async def bg_update() -> None:
                    """Update Circles & Members in background."""
                    try:
                        data, _ = await self._update_data(retry=True)
                        self.async_set_updated_data(data)
                        _LOGGER.warning(done_msg, "complete")
                    except asyncio.CancelledError:
                        _LOGGER.warning(done_msg, "cancelled")
                        raise
                    finally:
                        self._bg_update_task = None

                assert not self._bg_update_task
                self._bg_update_task = self.config_entry.async_create_background_task(
                    self.hass, bg_update(), "Circles & Members background update"
                )

            elif not self._store.loaded_ok:
                _LOGGER.warning(done_msg, "complete")

            return data  # noqa: TRY300
        except asyncio.CancelledError:
            _LOGGER.warning(done_msg, "cancelled")
            raise
        finally:
            self._fg_update_task = None

    async def _update_data(self, retry: bool) -> tuple[CirclesMembersData, bool]:
        """Update Life360 Circles & Members seen from all enabled accounts."""
        start = dt_util.utcnow()
        _LOGGER.debug("Begin updating Circles & Members")
        cancelled = False
        try:
            return await self._do_update(retry)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            _LOGGER.debug(
                "Updating Circles & Members %stook %s",
                "(which was cancelled) " if cancelled else "",
                dt_util.utcnow() - start,
            )

    async def _do_update(self, retry: bool) -> tuple[CirclesMembersData, bool]:
        """Update Life360 Circles & Members seen from all enabled accounts.

        rerty: If True, will retry indefinitely if login or rate limiting errors occur.
        If False, will retrieve whatever data it can without retrying login or rate
        limiting errors.

        Returns True if Circles & Members were retrieved from all accounts without
        error, or False if retry was False and at least one error occurred.
        """
        circle_errors = False
        circles: dict[CircleID, CircleData] = {}

        # Get Circles each account can see, keeping track of which accounts can see each
        # Circle, since a Circle can be seen by more than one account.
        raw_circles_list = await self._get_raw_circles_list(retry)
        for aid, raw_circles in zip(self._acct_data, raw_circles_list, strict=True):
            if isinstance(raw_circles, RequestError):
                circle_errors = True
                continue
            for raw_circle in raw_circles:
                if (cid := CircleID(raw_circle["id"])) not in circles:
                    circles[cid] = CircleData(raw_circle["name"])
                circles[cid].aids.add(aid)

        # Get Members in each Circle, recording their name & entity_picture.
        mem_details: dict[MemberID, MemberDetails] = {}
        raw_members_list = await self._get_raw_members_list(circles)
        for circle, raw_members in zip(circles.items(), raw_members_list, strict=True):
            if not isinstance(raw_members, RequestError):
                cid, circle_data = circle
                for raw_member in raw_members:
                    # PATCH: Fix API Key Mismatch (snake_case -> camelCase)
                    if "first_name" in raw_member and "firstName" not in raw_member:
                        raw_member["firstName"] = raw_member["first_name"]
                    if "last_name" in raw_member and "lastName" not in raw_member:
                        raw_member["lastName"] = raw_member["last_name"]
                    
                    # Fallback: Parse 'name' string if individual fields are missing
                    if "firstName" not in raw_member and "name" in raw_member:
                        parts = raw_member["name"].strip().split(" ", 1)
                        raw_member["firstName"] = parts[0]
                        raw_member["lastName"] = parts[1] if len(parts) > 1 else ""
                        
                    mid = MemberID(raw_member["id"])
                    circle_data.mids.add(mid)
                    if mid not in mem_details:
                        mem_details[mid] = MemberDetails.from_server(raw_member)

        # If there were any errors while getting Circles for each account, then retry
        # must have been False. Since we haven't yet received Circle data for all
        # enabled accounts, use any old information that is available to fill in the
        # gaps for now. E.g., we don't want to remove any Member entity until we're
        # absolutely sure they are no longer in any Circle visible from all enabled
        # accounts.
        if circle_errors:
            for cid, old_circle_data in self.data.circles.items():
                if cid in circles:
                    circles[cid].aids |= old_circle_data.aids
                else:
                    circles[cid] = old_circle_data
            for mid, old_md in self.data.mem_details.items():
                if mid not in mem_details:
                    mem_details[mid] = old_md

        # Protect storage writing in case we get cancelled while it's running. We do not
        # want to interrupt that process. It is an atomic operation, so if we get
        # cancelled and called again while it's running, and we somehow manage to get to
        # this point again while it still hasn't finished, we'll just wait until it is
        # done and it will be begun again with the new data.
        self._store.circles = circles
        self._store.mem_details = mem_details
        save_task = self.config_entry.async_create_task(
            self.hass,
            self._store.save(),
            "Save to Life360 storage",
        )
        await asyncio.shield(save_task)

        return CirclesMembersData(circles, mem_details), not circle_errors

    async def _get_raw_circles_list(
        self,
        retry: bool,
    ) -> list[list[dict[str, str]] | RequestError]:
        """Get raw Circle data for each Circle that can be seen by each account."""
        lrle_resp = (
            LoginRateLimitErrResp.RETRY if retry else LoginRateLimitErrResp.SILENT
        )
        return await asyncio.gather(  # type: ignore[no-any-return]
            *(
                self._request(
                    aid,
                    acct_data.api.get_circles,
                    msg="while getting Circles",
                    lrle_resp=lrle_resp,
                )
                for aid, acct_data in self._acct_data.items()
            )
        )

    async def _get_raw_members_list(
        self, circles: dict[CircleID, CircleData]
    ) -> list[list[dict[str, Any]] | RequestError]:
        """Get raw Member data for each Member in each Circle."""

        async def get_raw_members(
            cid: CircleID, circle_data: CircleData
        ) -> list[dict[str, Any]] | RequestError:
            """Get raw Member data for each Member in Circle."""
            # For each Circle, there may be more than one account that can see it, so
            # keep trying if for some reason an error occurs while trying to use one.
            for aid in circle_data.aids:
                raw_members = await self._request(
                    aid,
                    self._acct_data[aid].api.get_circle_members,
                    cid,
                    msg=f"while getting Members in {circle_data.name} Circle",
                )
                if not isinstance(raw_members, RequestError):
                    return raw_members  # type: ignore[no-any-return]
            # TODO: It's possible Circle was deleted, or accounts were removed from
            #       Circle, after the Circles list was obtained. This is very unlikely,
            #       and this is not called very often, so for now, don't worry about it.
            #       To be really robust, this possibility should be handled.
            return RequestError.NO_DATA

        return await asyncio.gather(
            *(get_raw_members(cid, circle_data) for cid, circle_data in circles.items())
        )

    async def _get_raw_member(
        self, mid: MemberID, cid: CircleID
    ) -> dict[str, Any] | RequestError:
        """Get raw Member data from given Circle."""
        name = self.data.mem_details[mid].name
        circle_data = self.data.circles[cid]
        raw_member: dict[str, Any] | RequestError = RequestError.NO_DATA
        for aid in circle_data.aids:
            raw_member = await self._client_request(
                aid,
                partial(
                    self._acct_data[aid].api.get_circle_member,
                    cid,
                    mid,
                    raise_not_modified=True,
                ),
                msg=f"while getting data for {name} from {circle_data.name} Circle",
            )
            if raw_member is RequestError.NOT_MODIFIED:
                return RequestError.NOT_MODIFIED
            if not isinstance(raw_member, RequestError):
                return raw_member
        # Can be NO_DATA or NOT_FOUND.
        return raw_member

    async def get_circle_devices(
        self, cid: CircleID
    ) -> tuple[dict[DeviceID, DeviceData], int | None]:
        """Get devices (Tiles, Jiobit) for a circle via direct API call.

        Returns:
            Tuple of (devices dict, HTTP status code if error or None if success)
        """
        devices: dict[DeviceID, DeviceData] = {}
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            _LOGGER.debug("get_circle_devices: Circle %s not found", cid)
            return devices, None

        # Fetch device metadata first to get names, avatars, categories
        # This populates the cache that we use below
        if not self._device_name_cache:
            await self._fetch_device_metadata(cid)

        if self._options.verbosity >= 2:
            _LOGGER.debug("Fetching device locations for circle %s", cid)

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                # Build the request with CloudEvents headers as required by API
                url = f"{API_BASE_URL}/v5/circles/devices/locations?providers[]=tile&providers[]=jiobit"

                # Generate CloudEvents headers per request
                ce_id = str(uuid.uuid4())
                ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                # Use registered device ID if available, otherwise generate one
                x_device_id = self._registered_device_id or f"homeassistant{self.config_entry.entry_id.replace('-', '')[:20]}"

                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                    "Cache-Control": "no-cache",
                    # CloudEvents specification headers
                    "circleid": cid,  # Circle ID goes in circleid header
                    "x-device-id": x_device_id,  # Required device identifier
                    "ce-type": "com.life360.cloud.platform.devices.locations.v1",
                    "ce-id": ce_id,  # Random UUID per request
                    "ce-specversion": "1.0",
                    "ce-time": ce_time,
                    "ce-source": f"/HOMEASSISTANT/{DOMAIN}",
                }

                session = self._acct_data[aid].session
                _LOGGER.debug("GET %s with circleid=%s ce-type=%s x-device-id=%s", url, cid, headers["ce-type"], x_device_id)

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()

                        # Handle nested 'data' wrapper in response
                        device_data = data.get("data", data) if isinstance(data, dict) else data

                        # Always log full response to debug device issues
                        _LOGGER.debug(
                            "Device locations response: status=200, full_response=%s",
                            data,
                        )

                        # Parse devices from response - devices are in 'items' array
                        items = device_data.get("items", []) if isinstance(device_data, dict) else []
                        if isinstance(items, list):
                            for raw_device in items:
                                try:
                                    # API may nest device info in 'data' field (CloudEvents style)
                                    # Flatten structure: merge item-level with nested data
                                    nested_data = raw_device.get("data", {})
                                    type_data = nested_data.get("typeData", {})

                                    # Build flat device dict with all available fields
                                    flat_device = {
                                        **raw_device,  # Start with top-level fields
                                        **nested_data,  # Add nested data fields
                                        **type_data,   # Add typeData fields (for Tile)
                                    }

                                    # Preserve the item-level id if present
                                    if "id" in raw_device:
                                        flat_device["id"] = raw_device["id"]

                                    # Get device ID for cache lookup
                                    device_id = (
                                        flat_device.get("deviceId") or
                                        flat_device.get("id") or
                                        raw_device.get("deviceId") or
                                        ""
                                    )

                                    # Inject cached metadata (name, avatar, category)
                                    # The locations endpoint doesn't return names
                                    if device_id and device_id in self._device_name_cache:
                                        if not flat_device.get("name"):
                                            flat_device["name"] = self._device_name_cache[device_id]
                                            _LOGGER.debug(
                                                "Using cached name for %s: %s",
                                                device_id, flat_device["name"],
                                            )
                                    if device_id and device_id in self._device_avatar_cache:
                                        if not flat_device.get("avatar"):
                                            flat_device["avatar"] = self._device_avatar_cache[device_id]
                                    if device_id and device_id in self._device_category_cache:
                                        if not flat_device.get("category"):
                                            flat_device["category"] = self._device_category_cache[device_id]

                                    # Determine provider from device data
                                    provider = (
                                        flat_device.get("provider") or
                                        flat_device.get("type") or
                                        nested_data.get("provider") or
                                        "unknown"
                                    ).lower()

                                    device = DeviceData.from_server(flat_device, provider)
                                    devices[DeviceID(device.device_id)] = device
                                    _LOGGER.debug(
                                        "Parsed %s device: %s (%s)",
                                        provider,
                                        device.name,
                                        device.device_id,
                                    )
                                except (KeyError, ValueError) as err:
                                    _LOGGER.warning(
                                        "Error parsing device: %s - raw=%s - %s",
                                        raw_device.get("name", raw_device.get("deviceName", "unknown")),
                                        raw_device,
                                        err,
                                    )

                        # Success - return devices (may be empty if no devices linked)
                        _LOGGER.debug("Found %d total devices from %d items", len(devices), len(items))
                        return devices, None
                    else:
                        resp_text = await resp.text()
                        _LOGGER.debug(
                            "Device locations request failed: HTTP %s - %s",
                            resp.status,
                            resp_text[:500],
                        )
                        return devices, resp.status
            except Exception as err:
                _LOGGER.debug("Error fetching device locations: %s", err)
                if self._options.verbosity >= 3:
                    _LOGGER.debug("Exception details", exc_info=True)

        return devices, None

    async def get_all_devices(self) -> tuple[dict[DeviceID, DeviceData], bool]:
        """Get all devices from all circles.

        Returns:
            Tuple of (devices dict, True if 403 forbidden was encountered)
        """
        all_devices: dict[DeviceID, DeviceData] = {}
        got_403 = False

        for cid in self.data.circles:
            circle_devices, status = await self.get_circle_devices(cid)
            all_devices.update(circle_devices)
            if status == 403:
                got_403 = True

        return all_devices, got_403

    async def get_circle_places(
        self, cid: CircleID
    ) -> list[helpers.PlaceData]:
        """Get places for a circle via direct API call."""
        places: list[helpers.PlaceData] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return places

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/circles/{cid}/allplaces"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Places response for circle %s: %s", cid, data)

                        raw_places = data.get("places", [])
                        if isinstance(raw_places, list):
                            for raw_place in raw_places:
                                try:
                                    place = helpers.PlaceData.from_server(raw_place)
                                    places.append(place)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing place: %s", err)

                        if places:
                            return places
                    else:
                        _LOGGER.debug("Places request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching places: %s", err)

        return places

    async def get_all_places(self) -> dict[str, helpers.PlaceData]:
        """Get all places from all circles."""
        all_places: dict[str, helpers.PlaceData] = {}

        for cid in self.data.circles:
            circle_places = await self.get_circle_places(cid)
            for place in circle_places:
                # Use place_id as key, or name if no id
                key = place.place_id or place.name
                all_places[key] = place

        return all_places

    async def get_driving_stats(
        self, cid: CircleID, mid: MemberID, week_offset: int = 0
    ) -> helpers.DrivingStats | None:
        """Get driving statistics for a member."""
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            _LOGGER.debug("get_driving_stats: Circle %s not found", cid)
            return None

        if self._options.verbosity >= 2:
            _LOGGER.debug("Fetching driving stats for member %s (week offset: %d)", mid, week_offset)

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/drivereport/circle/{cid}/user/{mid}/stats"
                params = {"weekOffset": str(week_offset)}
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                if self._options.verbosity >= 3:
                    _LOGGER.debug("GET %s params=%s", url, params)

                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if self._options.verbosity >= 2:
                            _LOGGER.debug(
                                "Driving stats for %s: score=%s, distance=%s, trips=%s",
                                mid,
                                data.get("score"),
                                data.get("distance"),
                                data.get("numTrips"),
                            )
                        if self._options.verbosity >= 3:
                            _LOGGER.debug("Full driving stats response: %s", data)
                        return helpers.DrivingStats.from_server(data)
                    else:
                        _LOGGER.debug("Driving stats request failed: HTTP %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching driving stats for %s: %s", mid, err)
                if self._options.verbosity >= 3:
                    _LOGGER.debug("Exception details", exc_info=True)

        return None

    async def get_crash_detection_status(self) -> bool | None:
        """Get crash detection enabled status."""
        for aid, acct_data in self._acct_data.items():
            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/driverbehavior/crashenabledstatus"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                async with acct_data.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Crash detection status: %s", data)
                        return data.get("crashDetection", {}).get("enabled", False)
                    else:
                        _LOGGER.debug("Crash status request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching crash status: %s", err)

        return None

    async def get_emergency_contacts(
        self, cid: CircleID
    ) -> list[helpers.EmergencyContact]:
        """Get emergency contacts for a circle."""
        contacts: list[helpers.EmergencyContact] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return contacts

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/circles/{cid}/emergencyContacts"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Emergency contacts: %s", data)

                        raw_contacts = data.get("emergencyContacts", [])
                        if isinstance(raw_contacts, list):
                            for raw_contact in raw_contacts:
                                try:
                                    contact = helpers.EmergencyContact.from_server(
                                        raw_contact
                                    )
                                    contacts.append(contact)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing contact: %s", err)

                        if contacts:
                            return contacts
                    else:
                        _LOGGER.debug("Emergency contacts failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching emergency contacts: %s", err)

        return contacts

    async def get_all_emergency_contacts(
        self,
    ) -> dict[CircleID, list[helpers.EmergencyContact]]:
        """Get emergency contacts from all circles."""
        all_contacts: dict[CircleID, list[helpers.EmergencyContact]] = {}

        for cid in self.data.circles:
            contacts = await self.get_emergency_contacts(cid)
            if contacts:
                all_contacts[cid] = contacts

        return all_contacts

    async def get_trip_history(
        self, cid: CircleID, mid: MemberID, limit: int = 10
    ) -> list[helpers.TripData]:
        """Get recent trip history for a member."""
        trips: list[helpers.TripData] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return trips

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/drivereport/circle/{cid}/user/{mid}/trips"
                params = {"limit": str(limit)}
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, params=params, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Trip history for %s: %s", mid, data)

                        raw_trips = data.get("trips", [])
                        if isinstance(raw_trips, list):
                            for raw_trip in raw_trips:
                                try:
                                    trip = helpers.TripData.from_server(raw_trip)
                                    trips.append(trip)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing trip: %s", err)

                        if trips:
                            return trips
                    else:
                        _LOGGER.debug("Trip history request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching trip history: %s", err)

        return trips

    async def get_geofence_zones(
        self, cid: CircleID
    ) -> list[helpers.GeofenceZone]:
        """Get geofence zones for a circle."""
        zones: list[helpers.GeofenceZone] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return zones

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v4/circles/{cid}/zones/"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Geofence zones for circle %s: %s", cid, data)

                        raw_zones = data.get("zones", [])
                        if isinstance(raw_zones, list):
                            for raw_zone in raw_zones:
                                try:
                                    zone = helpers.GeofenceZone.from_server(raw_zone)
                                    zones.append(zone)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing zone: %s", err)

                        if zones:
                            return zones
                    else:
                        _LOGGER.debug("Geofence zones request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching geofence zones: %s", err)

        return zones

    async def get_all_geofence_zones(self) -> dict[CircleID, list[helpers.GeofenceZone]]:
        """Get all geofence zones from all circles."""
        all_zones: dict[CircleID, list[helpers.GeofenceZone]] = {}

        for cid in self.data.circles:
            zones = await self.get_geofence_zones(cid)
            if zones:
                all_zones[cid] = zones

        return all_zones

    async def get_place_alerts(
        self, cid: CircleID
    ) -> list[helpers.PlaceAlert]:
        """Get place arrival/departure alerts for a circle."""
        alerts: list[helpers.PlaceAlert] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return alerts

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/circles/{cid}/places/alerts"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Place alerts for circle %s: %s", cid, data)

                        raw_alerts = data.get("alerts", [])
                        if isinstance(raw_alerts, list):
                            for raw_alert in raw_alerts:
                                try:
                                    alert = helpers.PlaceAlert.from_server(raw_alert)
                                    alerts.append(alert)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing alert: %s", err)

                        if alerts:
                            return alerts
                    else:
                        _LOGGER.debug("Place alerts request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching place alerts: %s", err)

        return alerts

    async def get_scheduled_alerts(
        self, cid: CircleID, mid: MemberID
    ) -> list[helpers.ScheduledAlert]:
        """Get scheduled check-in alerts for a member."""
        alerts: list[helpers.ScheduledAlert] = []
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return alerts

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v1/circles/{cid}/users/{mid}/scheduled/alerts"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Scheduled alerts for %s: %s", mid, data)

                        raw_alerts = data.get("alerts", [])
                        if isinstance(raw_alerts, list):
                            for raw_alert in raw_alerts:
                                try:
                                    alert = helpers.ScheduledAlert.from_server(raw_alert)
                                    alerts.append(alert)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing alert: %s", err)

                        if alerts:
                            return alerts
                    else:
                        _LOGGER.debug("Scheduled alerts request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching scheduled alerts: %s", err)

        return alerts

    async def get_member_role(
        self, cid: CircleID, mid: MemberID
    ) -> helpers.MemberRole | None:
        """Get member's role in a circle."""
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return None

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/circles/{cid}/members/{mid}/role"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                session = self._acct_data[aid].session
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Member role for %s: %s", mid, data)
                        return helpers.MemberRole.from_server(data)
                    else:
                        _LOGGER.debug("Member role request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching member role: %s", err)

        return None

    async def get_device_issues(self) -> list[helpers.DeviceIssue]:
        """Get device issues/errors."""
        issues: list[helpers.DeviceIssue] = []

        for aid, acct_data in self._acct_data.items():
            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v5/circles/devices/issues"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                async with acct_data.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Device issues: %s", data)

                        raw_issues = data.get("issues", [])
                        if isinstance(raw_issues, list):
                            for raw_issue in raw_issues:
                                try:
                                    issue = helpers.DeviceIssue.from_server(raw_issue)
                                    issues.append(issue)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing issue: %s", err)

                        if issues:
                            return issues
                    else:
                        _LOGGER.debug("Device issues request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching device issues: %s", err)

        return issues

    async def get_user_profile(self) -> helpers.UserProfile | None:
        """Get current user's profile."""
        for aid, acct_data in self._acct_data.items():
            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v3/users/me"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                async with acct_data.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("User profile: %s", data)
                        return helpers.UserProfile.from_server(data)
                    else:
                        _LOGGER.debug("User profile request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching user profile: %s", err)

        return None

    async def get_integrations(self) -> list[helpers.ConnectedIntegration]:
        """Get connected integrations/apps."""
        integrations: list[helpers.ConnectedIntegration] = []

        for aid, acct_data in self._acct_data.items():
            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v6/integrations"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                }

                async with acct_data.session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Integrations: %s", data)

                        raw_integrations = data.get("integrations", [])
                        if isinstance(raw_integrations, list):
                            for raw_int in raw_integrations:
                                try:
                                    integration = helpers.ConnectedIntegration.from_server(
                                        raw_int
                                    )
                                    integrations.append(integration)
                                except (KeyError, ValueError) as err:
                                    _LOGGER.debug("Error parsing integration: %s", err)

                        if integrations:
                            return integrations
                    else:
                        _LOGGER.debug("Integrations request failed: %s", resp.status)
            except Exception as err:
                _LOGGER.debug("Error fetching integrations: %s", err)

        return integrations

    async def send_jiobit_command(
        self, device_id: str, cid: CircleID, command: str = "buzz"
    ) -> bool:
        """Send command to Jiobit device (e.g., buzz to find pet)."""
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return False

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v6/provider/jiobit/devices/{device_id}/circle/{cid}/command"
                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": API_USER_AGENT,
                }
                payload = {"command": command}

                session = self._acct_data[aid].session
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in (200, 201, 204):
                        _LOGGER.info("Jiobit command '%s' sent to %s", command, device_id)
                        return True
                    else:
                        _LOGGER.debug(
                            "Jiobit command failed: %s - %s",
                            resp.status,
                            await resp.text(),
                        )
            except Exception as err:
                _LOGGER.debug("Error sending Jiobit command: %s", err)

        return False

    async def send_device_command(
        self,
        device_id: str,
        cid: CircleID,
        provider: str,
        feature_id: int,
        enable: bool = True,
        duration: int = 30,
        strength: int = 2,
    ) -> bool:
        """Send command to a device (Tile or Jiobit).

        Args:
            device_id: Device ID
            cid: Circle ID
            provider: Device provider (tile, jiobit)
            feature_id: Feature ID (1=ring/buzz, 30=light)
            enable: Enable or disable the feature
            duration: Duration in seconds
            strength: Strength level (1-3)

        Returns:
            True if command was sent successfully
        """
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            _LOGGER.debug("send_device_command: Circle %s not found", cid)
            return False

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            try:
                url = f"{API_BASE_URL}/v6/provider/{provider}/devices/{device_id}/circle/{cid}/command"

                # Generate CloudEvents headers
                ce_id = str(uuid.uuid4())
                ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                # Use registered device ID if available, otherwise generate one
                x_device_id = self._registered_device_id or f"homeassistant{self.config_entry.entry_id.replace('-', '')[:20]}"

                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": API_USER_AGENT,
                    # CloudEvents headers
                    "circleid": cid,
                    "x-device-id": x_device_id,  # Required device identifier
                    "ce-type": "com.life360.cloud.device.commands.provider.action.invoke.v1",
                    "ce-id": ce_id,
                    "ce-specversion": "1.0",
                    "ce-time": ce_time,
                    "ce-source": f"/HOMEASSISTANT/{DOMAIN}",
                }

                # Command payload structure from captured traffic
                payload = {
                    "data": {
                        "commands": [
                            {
                                "args": {
                                    "duration": duration,
                                    "delivered": False,
                                    "featureId": feature_id,
                                    "strength": strength,
                                },
                                "command": "deviceUiFeatureEnable" if enable else "deviceUiFeatureDisable",
                            }
                        ]
                    }
                }

                session = self._acct_data[aid].session
                _LOGGER.debug(
                    "POST %s with provider=%s feature=%d enable=%s",
                    url, provider, feature_id, enable,
                )

                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in (200, 201, 202, 204):
                        _LOGGER.info(
                            "Device command sent: %s feature=%d to %s/%s",
                            "enable" if enable else "disable",
                            feature_id,
                            provider,
                            device_id,
                        )
                        return True
                    else:
                        resp_text = await resp.text()
                        _LOGGER.debug(
                            "Device command failed: HTTP %s - %s",
                            resp.status,
                            resp_text[:500],
                        )
            except Exception as err:
                _LOGGER.debug("Error sending device command: %s", err)

        return False

    async def ring_device(
        self,
        device_id: str,
        cid: CircleID,
        provider: str = "jiobit",
        duration: int = 30,
        strength: int = 2,
    ) -> bool:
        """Ring/buzz a device to help locate it.

        Args:
            device_id: Device ID
            cid: Circle ID
            provider: Device provider (tile, jiobit)
            duration: Ring duration in seconds
            strength: Ring strength (1=low, 2=med, 3=high)

        Returns:
            True if ring command was sent successfully
        """
        # For Tile devices, try BLE first if available
        if provider == "tile":
            ble_success = await self._ring_tile_ble(device_id, cid)
            if ble_success:
                return True
            _LOGGER.debug("Tile BLE ring failed, falling back to server API")

        # Feature ID 1 = ring/buzz
        return await self.send_device_command(
            device_id, cid, provider, feature_id=1, enable=True,
            duration=duration, strength=strength,
        )

    async def stop_ring_device(
        self,
        device_id: str,
        cid: CircleID,
        provider: str = "jiobit",
    ) -> bool:
        """Stop ringing/buzzing a device.

        Args:
            device_id: Device ID
            cid: Circle ID
            provider: Device provider (tile, jiobit)

        Returns:
            True if stop command was sent successfully
        """
        # For Tile devices, try BLE first if available
        if provider == "tile":
            ble_success = await self._stop_ring_tile_ble(device_id, cid)
            if ble_success:
                return True
            _LOGGER.debug("Tile BLE stop failed, falling back to server API")

        # Feature ID 1 = ring/buzz, enable=False to stop
        return await self.send_device_command(
            device_id, cid, provider, feature_id=1, enable=False,
        )

    async def toggle_device_light(
        self,
        device_id: str,
        cid: CircleID,
        provider: str = "jiobit",
        enable: bool = True,
    ) -> bool:
        """Toggle the light on a device (Jiobit only).

        Args:
            device_id: Device ID
            cid: Circle ID
            provider: Device provider
            enable: Turn light on or off

        Returns:
            True if command was sent successfully
        """
        # Feature ID 30 = light
        return await self.send_device_command(
            device_id, cid, provider, feature_id=30, enable=enable,
        )

    async def _get_or_register_device_id(
        self, aid: AccountID, acct: helpers.AccountDetails
    ) -> str | None:
        """Get a registered device ID for API requests, registering if needed."""
        if self._registered_device_id:
            return self._registered_device_id

        if self._device_registration_attempted:
            return None

        self._device_registration_attempted = True

        if aid not in self._acct_data:
            return None

        try:
            # Register Home Assistant as a "device" with Life360
            url = f"{API_BASE_URL}/v3/users/devices"

            # Generate a unique ID that mimics Android format
            entry_id = self.config_entry.entry_id.replace("-", "")
            device_id = f"android{entry_id[:24]}"
            
            ce_id = str(uuid.uuid4())
            ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            headers = {
                "Authorization": f"Bearer {acct.authorization}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": API_USER_AGENT,
                "ce-type": "com.life360.user.devices.v1",
                "ce-id": ce_id,
                "ce-specversion": "1.0",
                "ce-time": ce_time,
                "ce-source": f"/HOMEASSISTANT/{DOMAIN}",
            }

            # FIX: Added 'version' and 'deviceVersion' to satisfy API validation
            payload = {
                "appId": "com.life360.android.safetymapd",
                "deviceId": device_id,
                "deviceUdid": device_id,
                "os": "android",
                "osVersion": "13.0",
                "appVersion": "25.45.0",
                "pushToken": "",
                "deviceType": "mobile",
                "language": "en_US",
                "country": "US",
                "installId": device_id,
                
                # Naming
                "name": "Pixel 6",
                "deviceName": "Pixel 6",
                
                # Hardware Profile
                "model": "Pixel 6",
                "deviceModel": "Pixel 6",
                "manufacturer": "Google",
                "deviceManufacturer": "Google",
                "brand": "google",
                "product": "oriole",
                "board": "oriole",

                # NEW FIX: Version info
                "version": "13.0",
                "deviceVersion": "13.0"
            }

            session = self._acct_data[aid].session
            _LOGGER.info("Attempting to register HA as device: %s", device_id)

            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    _LOGGER.info("Registration SUCCESS: %s", data)
                    self._registered_device_id = data.get("deviceId", device_id)
                    return self._registered_device_id
                elif resp.status == 409:
                    self._registered_device_id = device_id
                    _LOGGER.info("Device already registered, using: %s", device_id)
                    return self._registered_device_id
                else:
                    resp_text = await resp.text()
                    _LOGGER.error("Registration FAILED: HTTP %s - %s", resp.status, resp_text)
        except Exception as err:
            _LOGGER.exception("Error registering device: %s", err)

        return None

    async def _fetch_device_metadata(self, cid: CircleID) -> bool:
        """Fetch and cache device metadata (names, avatars, categories) from /v6/devices.

        This endpoint returns all Tile/Jiobit devices for the user with their names and metadata.
        The cid parameter is used to get account credentials but the endpoint returns all devices.

        Args:
            cid: Circle ID (used to get account credentials)

        Returns:
            True if metadata was fetched successfully
        """
        circle_data = self.data.circles.get(cid)
        if not circle_data:
            return False

        for aid in circle_data.aids:
            if aid not in self._acct_data:
                continue

            acct = self._options.accounts.get(aid)
            if not acct:
                continue

            # First try to get registered device ID, or register one if needed
            registered_device_id = await self._get_or_register_device_id(aid, acct)

            try:
                # Use /v6/devices endpoint to get Tile/Jiobit device names and metadata
                # This endpoint does NOT use circleid - it returns all devices for the user
                url = f"{API_BASE_URL}/v6/devices?activationStates=activated,pending,pending_disassociated"

                ce_id = str(uuid.uuid4())
                ce_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                headers = {
                    "Authorization": f"Bearer {acct.authorization}",
                    "Accept": "application/json",
                    "User-Agent": API_USER_AGENT,
                    "ce-type": "com.life360.device.devices.v1",
                    "ce-id": ce_id,
                    "ce-specversion": "1.0",
                    "ce-time": ce_time,
                    "ce-source": f"/HOMEASSISTANT/{DOMAIN}",
                }

                # Only add x-device-id if we have a registered one
                if registered_device_id:
                    headers["x-device-id"] = registered_device_id

                session = self._acct_data[aid].session
                _LOGGER.debug("Fetching device metadata from %s with x-device-id=%s", url, registered_device_id or "none")

                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _LOGGER.debug("Device metadata response: %s", data)

                        # Parse response - structure is data.items[]
                        items = data.get("data", {}).get("items", []) if isinstance(data.get("data"), dict) else data.get("items", [])
                        if not items and isinstance(data, list):
                            items = data

                        # =========================================================================
                        # PATCH: Loop to handle new API keys for Tiles and Jiobits
                        # =========================================================================
                        for item in items:
                            item_type = item.get("type", "device")

                            # FIX 1: Handle Jiobit/Profiles (e.g. "Ollie")
                            if item_type == "profile":
                                profile_data = item.get("data", {})
                                # Try both camelCase (old) and snake_case (new)
                                tracker_id = profile_data.get("trackerId") or profile_data.get("tracker_id") or ""
                                name = profile_data.get("name", "")
                                
                                if tracker_id and name:
                                    self._device_name_cache[tracker_id] = name
                                    _LOGGER.debug(f"Mapped Profile: {name} -> {tracker_id}")
                                continue

                            # FIX 2: Handle Tiles/Devices with new keys
                            # Try 'id', 'deviceId', 'device_id'
                            device_id = (
                                item.get("id") or 
                                item.get("deviceId") or 
                                item.get("device_id") or 
                                ""
                            )
                            
                            if not device_id:
                                continue

                            # Try 'name', 'deviceName', 'device_name'
                            name = (
                                item.get("name") or 
                                item.get("deviceName") or 
                                item.get("device_name")
                            )

                            if name:
                                self._device_name_cache[device_id] = name
                                _LOGGER.debug(f"Mapped Device: {name} -> {device_id}")

                            # Handle Avatar
                            avatar = item.get("avatar") or item.get("avatar_url")
                            if avatar:
                                self._device_avatar_cache[device_id] = avatar

                            # Handle Category
                            category = item.get("category")
                            if category:
                                self._device_category_cache[device_id] = category

                            # Handle Auth Key (for BLE)
                            type_data = item.get("typeData") or item.get("type_data") or {}
                            tile_device_id = type_data.get("deviceId") or type_data.get("device_id") or ""
                            auth_key_b64 = type_data.get("authKey") or type_data.get("auth_key") or ""

                            if tile_device_id and auth_key_b64:
                                try:
                                    auth_key = base64.b64decode(auth_key_b64)
                                    self._tile_auth_cache[device_id] = auth_key
                                    self._tile_ble_id_cache[device_id] = tile_device_id
                                    # Also cache by BLE device ID for lookup
                                    self._tile_auth_cache[tile_device_id] = auth_key
                                    self._tile_ble_id_cache[tile_device_id] = tile_device_id
                                except Exception:
                                    _LOGGER.debug("Failed to decode auth key for %s", device_id)
                        # =========================================================================

                        _LOGGER.debug(
                            "Cached metadata for %d devices", len(self._device_name_cache)
                        )
                        return True
                    elif resp.status == 404:
                        # Feature not available for this account - not an error
                        _LOGGER.debug("Device metadata endpoint returned 404 - feature not available")
                        return True
                    else:
                        resp_text = await resp.text()
                        _LOGGER.debug("Device metadata request failed: HTTP %s - %s", resp.status, resp_text[:200])
            except Exception as err:
                _LOGGER.debug("Error fetching device metadata: %s", err)

        return False

    async def _get_tile_auth_data(
        self, device_id: str, cid: CircleID
    ) -> tuple[bytes | None, str | None]:
        """Get Tile authentication key and BLE device ID.

        Args:
            device_id: Life360 device ID

        Returns:
            Tuple of (auth_key bytes, ble_device_id) or (None, None) if not found
        """
        # Check cache first
        if device_id in self._tile_auth_cache and device_id in self._tile_ble_id_cache:
            return self._tile_auth_cache[device_id], self._tile_ble_id_cache[device_id]

        # Try to fetch metadata which will populate the cache
        await self._fetch_device_metadata(cid)

        # Check cache again
        if device_id in self._tile_auth_cache:
            return (
                self._tile_auth_cache[device_id],
                self._tile_ble_id_cache.get(device_id),
            )

        return None, None

    async def _ring_tile_ble(self, device_id: str, cid: CircleID) -> bool:
        """Ring a Tile device via Bluetooth LE.

        Args:
            device_id: Life360 device ID
            cid: Circle ID (needed to fetch auth key)

        Returns:
            True if BLE ring was successful
        """
        try:
            from .tile_ble import ring_tile_ble, BLEAK_AVAILABLE, TileVolume
        except ImportError:
            _LOGGER.debug("Tile BLE module not available")
            return False

        if not BLEAK_AVAILABLE:
            _LOGGER.debug("bleak library not available for Tile BLE")
            return False

        auth_key, ble_device_id = await self._get_tile_auth_data(device_id, cid)
        if not auth_key or not ble_device_id:
            _LOGGER.debug("No Tile auth data found for device %s", device_id)
            return False

        _LOGGER.debug("Attempting BLE ring for Tile %s", ble_device_id)
        return await ring_tile_ble(
            ble_device_id,
            auth_key,
            volume=TileVolume.MED,
            duration_seconds=30,
            scan_timeout=10.0,
        )

    async def _stop_ring_tile_ble(self, device_id: str, cid: CircleID) -> bool:
        """Stop ringing a Tile device via Bluetooth LE.

        Args:
            device_id: Life360 device ID
            cid: Circle ID

        Returns:
            True if BLE stop was successful
        """
        try:
            from .tile_ble import stop_ring_tile_ble, BLEAK_AVAILABLE
        except ImportError:
            _LOGGER.debug("Tile BLE module not available")
            return False

        if not BLEAK_AVAILABLE:
            _LOGGER.debug("bleak library not available for Tile BLE")
            return False

        auth_key, ble_device_id = await self._get_tile_auth_data(device_id, cid)
        if not auth_key or not ble_device_id:
            _LOGGER.debug("No Tile auth data found for device %s", device_id)
            return False

        _LOGGER.debug("Attempting BLE stop ring for Tile %s", ble_device_id)
        return await stop_ring_tile_ble(
            ble_device_id,
            auth_key,
            scan_timeout=10.0,
        )

    async def _client_request(
        self,
        aid: AccountID,
        target: Callable[[*_Ts], Coroutine[Any, Any, _R]],
        *args: *_Ts,
        msg: str,
    ) -> _R | RequestError:
        """Make a request to the Life360 server on behalf of Member coordinator."""
        await self._client_request_ok.wait()

        task = self.config_entry.async_create_background_task(
            self.hass,
            self._request(aid, target, *args, msg=msg),
            f"Make client request to {aid}",
        )
        self._client_tasks.add(task)
        try:
            return await task
        except asyncio.CancelledError:
            return RequestError.NO_DATA
        finally:
            self._client_tasks.discard(task)

    # _requests = 0

    async def _request(
        self,
        aid: AccountID,
        target: Callable[[*_Ts], Coroutine[Any, Any, _R]],
        *args: *_Ts,
        msg: str,
        lrle_resp: LoginRateLimitErrResp = LoginRateLimitErrResp.LTD_LOGIN_ERROR_RETRY,
    ) -> _R | RequestError:
        """Make a request to the Life360 server."""
        if self._acct_data[aid].failed.is_set():
            return RequestError.NO_DATA

        start = dt_util.utcnow()
        login_error_retries = 0
        delay: int | None = None
        delay_reason = ""
        warned = False

        failed_task = self._acct_data[aid].failed_task
        request_task: asyncio.Task[_R] | None = None
        try:
            while True:
                if delay is not None:
                    if (
                        not warned
                        and (dt_util.utcnow() - start).total_seconds() + delay > 60 * 60
                    ):
                        _LOGGER.warning(
                            "Getting response from Life360 for %s "
                            "is taking longer than expected",
                            aid,
                        )
                        warned = True
                    _LOGGER.debug(
                        "%s: %s %s: will retry (%i) in %i s",
                        aid,
                        delay_reason,
                        msg,
                        login_error_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                request_task = self.config_entry.async_create_background_task(
                    self.hass,
                    target(*args),
                    f"Make request to {aid}",
                )
                done, _ = await asyncio.wait(
                    [failed_task, request_task], return_when=asyncio.FIRST_COMPLETED
                )
                if failed_task in done:
                    (rt := request_task).cancel()
                    request_task = None
                    with suppress(asyncio.CancelledError, Life360Error):
                        await rt
                    return RequestError.NO_DATA

                try:
                    # if aid == "federicktest95@gmail.com":
                    #     self._requests += 1
                    #     if self._requests == 1:
                    #         (rt := request_task).cancel()
                    #         request_task = None
                    #         with suppress(BaseException):
                    #             await rt
                    #         raise LoginError("TEST TEST TEST")
                    result = await request_task

                except NotFound:
                    self._set_acct_exc(aid)
                    return RequestError.NOT_FOUND

                except NotModified:
                    self._set_acct_exc(aid)
                    return RequestError.NOT_MODIFIED

                except LoginError as exc:
                    self._acct_data[aid].session.cookie_jar.clear()

                    if (
                        lrle_resp is LoginRateLimitErrResp.RETRY
                        or lrle_resp is LoginRateLimitErrResp.LTD_LOGIN_ERROR_RETRY
                        and login_error_retries < MAX_LTD_LOGIN_ERROR_RETRIES
                    ):
                        self._set_acct_exc(aid)
                        if lrle_resp is LoginRateLimitErrResp.RETRY:
                            delay = LOGIN_ERROR_RETRY_DELAY
                        else:
                            delay = LTD_LOGIN_ERROR_RETRY_DELAY
                        delay_reason = "login error"
                        login_error_retries += 1
                        continue

                    treat_as_error = lrle_resp is not LoginRateLimitErrResp.SILENT
                    self._set_acct_exc(aid, not treat_as_error, msg, exc)
                    if treat_as_error:
                        self._handle_login_error(aid)
                    return RequestError.NO_DATA

                except Life360Error as exc:
                    rate_limited = isinstance(exc, RateLimited)
                    if lrle_resp is LoginRateLimitErrResp.RETRY and rate_limited:
                        self._set_acct_exc(aid)
                        delay = ceil(cast(RateLimited, exc).retry_after or 0) + 10
                        delay_reason = "rate limited"
                        continue

                    treat_as_error = not (
                        rate_limited and lrle_resp is LoginRateLimitErrResp.SILENT
                    )
                    self._set_acct_exc(aid, not treat_as_error, msg, exc)
                    return RequestError.NO_DATA

                else:
                    request_task = None
                    self._set_acct_exc(aid)
                    return result

        except asyncio.CancelledError:
            if request_task:
                request_task.cancel()
                with suppress(asyncio.CancelledError, Life360Error):
                    await request_task
            raise
        finally:
            if warned:
                _LOGGER.warning("Done trying to get response from Life360 for %s", aid)

    def _set_acct_exc(
        self,
        aid: AccountID,
        online: bool = True,
        msg: str = "",
        exc: Exception | None = None,
    ) -> None:
        """Set account exception status and signal clients if it has changed."""
        acct = self._acct_data[aid]
        if exc is not None:
            level = logging.ERROR if not online and acct.online else logging.DEBUG
            _LOGGER.log(level, "%s: %s: %s", aid, msg, exc)

        if online == acct.online:
            return

        if online and not acct.online:
            _LOGGER.error("%s: Fetching data recovered", aid)
        acct.online = online
        async_dispatcher_send(self.hass, SIGNAL_ACCT_STATUS, aid)

    def _handle_login_error(self, aid: AccountID) -> None:
        """Handle account login error."""
        if (failed := self._acct_data[aid].failed).is_set():
            return
        # Signal all current requests using account to stop and return NO_DATA.
        failed.set()

        # Create repair issue for account and disable it. Deleting repair issues will be
        # handled by config flow.
        async_create_issue(
            self.hass,
            DOMAIN,
            aid,
            is_fixable=False,
            is_persistent=True,
            severity=IssueSeverity.ERROR,
            translation_key="login_error",
            translation_placeholders={"acct_id": aid},
        )
        options = self._options.as_dict()
        options["accounts"][aid]["enabled"] = False
        self.hass.config_entries.async_update_entry(self.config_entry, options=options)

    async def _config_entry_updated(self, _: HomeAssistant, entry: ConfigEntry) -> None:
        """Run when the config entry has been updated."""
        if self._options == (new_options := ConfigOptions.from_dict(entry.options)):
            return

        old_options = self._options
        self._options = new_options
        # Get previously and currently enabled accounts.
        old_accts = {
            aid: acct for aid, acct in old_options.accounts.items() if acct.enabled
        }
        new_accts = {
            aid: acct for aid, acct in new_options.accounts.items() if acct.enabled
        }
        if old_accts == new_accts and old_options.verbosity == new_options.verbosity:
            return

        old_acct_ids = set(old_accts)
        new_acct_ids = set(new_accts)

        for aid in old_acct_ids & new_acct_ids:
            api = self._acct_data[aid].api
            api.authorization = new_options.accounts[aid].authorization
            api.name = (
                aid
                if new_options.verbosity >= 3
                else f"Account {list(self._acct_data).index(aid) + 1}"
            )
            api.verbosity = new_options.verbosity

        if old_accts == new_accts:
            return

        await self._stop_tasks()

        del_acct_ids = old_acct_ids - new_acct_ids
        self._delete_acct_data(del_acct_ids)
        self._create_acct_data(new_acct_ids - old_acct_ids)

        # Remove any accounts that no longer exist, or at least, are no longer
        # enabled. If that leaves any Circles with no accounts that can access it, then
        # also remove those Circles. And, lastly, if that leaves any Members not
        # associated with at least one Circle, then remove those Members, too.
        no_aids: list[CircleID] = []
        for cid, circle_data in self.data.circles.items():
            circle_data.aids -= del_acct_ids
            if not circle_data.aids:
                no_aids.append(cid)
        for cid in no_aids:
            del self.data.circles[cid]
        for mid in [mid for mid in self.data.mem_details if not self.mem_circles[mid]]:
            del self.data.mem_details[mid]

        await self.async_refresh()

        # Allow client requests to proceed.
        self._client_request_ok.set()

    async def _stop_tasks(self) -> None:
        """Stop all background tasks."""
        # Prevent any client requests from starting.
        self._client_request_ok.clear()

        # Stop everything.
        tasks = set(self._client_tasks)
        if self._fg_update_task:
            tasks.add(self._fg_update_task)
            self._fg_update_task = None
        if self._bg_update_task:
            tasks.add(self._bg_update_task)
            self._bg_update_task = None
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _create_acct_data(self, aids: Iterable[AccountID]) -> None:
        """Create data needed for each specified Life360 account."""
        for idx, aid in enumerate(aids):
            acct = self._options.accounts[aid]
            if not acct.enabled:
                continue
            session = async_create_clientsession(self.hass, timeout=COMM_TIMEOUT)
            name = aid if self._options.verbosity >= 3 else f"Account {idx + 1}"
            api = helpers.Life360(
                session,
                COMM_MAX_RETRIES,
                acct.authorization,
                name=name,
                verbosity=self._options.verbosity,
            )
            failed = asyncio.Event()
            failed_task = self.config_entry.async_create_background_task(
                self.hass,
                failed.wait(),
                f"Monitor failed requests to {aid}",
            )
            self._acct_data[aid] = AccountData(session, api, failed, failed_task)

    def _delete_acct_data(self, aids: Iterable[AccountID]) -> None:
        """Delete data previously created for each specified Life360 account."""
        for aid in aids:
            acct = self._acct_data.pop(aid)
            acct.session.detach()
            acct.failed_task.cancel()


class MemberDataUpdateCoordinator(DataUpdateCoordinator[MemberData]):
    """Member data update coordinator."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
        mid: MemberID,
    ) -> None:
        """Initialize data update coordinator."""
        mem_details = coordinator.data.mem_details[mid]
        super().__init__(
            hass, _LOGGER, name=mem_details.name, update_interval=UPDATE_INTERVAL
        )
        # always_update added in 2023.9.
        if hasattr(self, "always_update"):
            self.always_update = False
        self.data = MemberData(mem_details)
        self._coordinator = coordinator
        self._mid = mid
        self._member_data: dict[CircleID, MemberData] = {}

    async def update_location(self) -> None:
        """Request Member location update."""
        await self._coordinator.update_member_location(self._mid)

    async def _async_update_data(self) -> MemberData:
        """Fetch the latest data from the source."""
        raw_member_data = await self._coordinator.get_raw_member_data(self._mid)
        # Member may no longer be available, but we haven't been removed yet.
        if raw_member_data is None:
            return self.data

        member_data: dict[CircleID, MemberData] = {}
        for cid, raw_member in raw_member_data.items():
            if not isinstance(raw_member, RequestError):
                member_data[cid] = MemberData.from_server(raw_member)
            elif raw_member is RequestError.NOT_FOUND:
                member_data[cid] = MemberData(
                    self.data.details, loc_missing=NoLocReason.NOT_FOUND
                )
            elif old_md := self._member_data.get(cid):
                # NOT_MODIFIED or NO_DATA
                member_data[cid] = old_md
        if not member_data:
            return self.data

        # Save the data in case NotModified or server error on next cycle.
        self._member_data = member_data

        # Now take "best" data for Member.
        data = sorted(member_data.values())[-1]
        if len(self._coordinator.data.circles) > 1:
            # Each Circle has its own Places. Collect all the Places where the
            # Member might be, while keeping the Circle they came from. Then
            # update the chosen MemberData with the Place or Places where the
            # Member is, with each having a suffix of the name of its Circle.
            places = {
                cid: cast(str, md.loc.details.place)
                for cid, md in member_data.items()
                if md.loc and md.loc.details.place
            }
            if places:
                place: str | list[str] = [
                    f"{c_place} ({self._coordinator.data.circles[cid].name})"
                    for cid, c_place in places.items()
                ]
                if len(place) == 1:
                    place = place[0]
                data = deepcopy(data)
                assert data.loc
                data.loc.details.place = place

        return data


class DeviceDataUpdateCoordinator(DataUpdateCoordinator[dict[DeviceID, DeviceData]]):
    """Device data update coordinator for Tiles and pet GPS trackers."""

    config_entry: ConfigEntry

    # After this many consecutive 403 errors, disable device polling
    MAX_CONSECUTIVE_403_ERRORS = 5

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize data update coordinator."""
        super().__init__(
            hass, _LOGGER, name="Life360 Devices", update_interval=UPDATE_INTERVAL
        )
        if hasattr(self, "always_update"):
            self.always_update = False
        self.data: dict[DeviceID, DeviceData] = {}
        self._coordinator = coordinator
        self._consecutive_403_errors = 0
        self._device_polling_disabled = False

    async def _async_update_data(self) -> dict[DeviceID, DeviceData]:
        """Fetch the latest device data from the source."""
        # Skip if device polling has been disabled due to persistent 403 errors
        if self._device_polling_disabled:
            return self.data

        try:
            devices, got_403 = await self._coordinator.get_all_devices()

            if got_403:
                self._consecutive_403_errors += 1
                if self._consecutive_403_errors >= self.MAX_CONSECUTIVE_403_ERRORS:
                    self._device_polling_disabled = True
                    _LOGGER.info(
                        "Device tracking disabled after %d consecutive 403 errors. "
                        "This usually means Tile/Jiobit tracking is not available for your account. "
                        "Restart Home Assistant to retry.",
                        self._consecutive_403_errors,
                    )
                elif self._consecutive_403_errors == 1:
                    # Only log on first error to reduce spam
                    _LOGGER.debug(
                        "Device locations returned 403 Forbidden - "
                        "Tile/Jiobit tracking may not be available for your subscription"
                    )
                return self.data
            else:
                # Reset error count on success
                self._consecutive_403_errors = 0

            if devices:
                _LOGGER.debug("Got %d devices from Life360", len(devices))
                # Send signal if devices changed
                old_device_ids = set(self.data.keys())
                new_device_ids = set(devices.keys())
                if old_device_ids != new_device_ids:
                    async_dispatcher_send(self.hass, SIGNAL_DEVICES_CHANGED)
                return devices
        except Exception as err:
            _LOGGER.debug("Error fetching devices: %s", err)

        # Return existing data on error
        return self.data


@dataclass
class L360Coordinators:
    """Life360 data update coordinators."""

    coordinator: CirclesMembersDataUpdateCoordinator
    mem_coordinator: dict[MemberID, MemberDataUpdateCoordinator]
    device_coordinator: DeviceDataUpdateCoordinator | None = None


type L360ConfigEntry = ConfigEntry[L360Coordinators]
