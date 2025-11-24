"""Life360 helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import IntEnum
from typing import Any, NewType, Self, cast

from life360 import Life360

from homeassistant.const import CONF_ENABLED, CONF_PASSWORD, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import DistanceConverter

from .const import (
    CONF_ACCOUNTS,
    CONF_AUTHORIZATION,
    CONF_DRIVING_SPEED,
    CONF_MAX_GPS_ACCURACY,
    CONF_SHOW_DRIVING,
    CONF_VERBOSITY,
    DOMAIN,
    SPEED_DIGITS,
    SPEED_FACTOR_MPH,
)

# So testing can patch in one place.
LIFE360 = Life360


AccountID = NewType("AccountID", str)
CircleID = NewType("CircleID", str)
MemberID = NewType("MemberID", str)
DeviceID = NewType("DeviceID", str)


@dataclass
class Account:
    """Account info."""

    authorization: str
    password: str | None = None
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        """
        return cls(data[CONF_AUTHORIZATION], data[CONF_PASSWORD], data[CONF_ENABLED])


@dataclass
class ConfigOptions:
    """Config entry options."""

    accounts: dict[AccountID, Account] = field(default_factory=dict)
    # CONF_SHOW_DRIVING is actually "driving" for legacy reasons.
    driving: bool = False
    driving_speed: float | None = None
    max_gps_accuracy: int | None = None
    verbosity: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        """
        accts = cast(dict[str, dict[str, Any]], data[CONF_ACCOUNTS])
        return cls(
            {AccountID(aid): Account.from_dict(acct) for aid, acct in accts.items()},
            data[CONF_SHOW_DRIVING],
            data[CONF_DRIVING_SPEED],
            data[CONF_MAX_GPS_ACCURACY],
            data[CONF_VERBOSITY],
        )


@dataclass
class MemberDetails:
    """Life360 Member "static" details."""

    name: str
    entity_picture: str | None = None

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        """
        return cls(
            restored["name"],
            restored["entity_picture"],
        )

    @classmethod
    def from_server(cls, raw_member: Mapping[str, Any]) -> Self:
        """Initialize from Member's data from server."""
        first = raw_member["firstName"]
        last = raw_member["lastName"]
        if first and last:
            name = f"{first} {last}"
        else:
            name = first or last or "No Name"
        entity_picture = raw_member["avatar"]
        return cls(name, entity_picture)


@dataclass
class LocationDetails:
    """Life360 Member location details."""

    address: str | None
    at_loc_since: datetime
    driving: bool
    gps_accuracy: int  # meters
    last_seen: datetime
    latitude: float
    longitude: float
    place: str | list[str] | None
    speed: float  # mph

    @staticmethod
    def to_datetime(value: Any) -> datetime:
        """Extract value at key and convert to datetime in UTC.

        Raises ValueError if value is not a valid datetime or representation of one.
        """
        if isinstance(value, datetime):
            return dt_util.as_utc(value)
        try:
            parsed_value = dt_util.parse_datetime(value)
        except TypeError:
            raise ValueError from None
        if parsed_value is None:
            raise ValueError
        return dt_util.as_utc(parsed_value)

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        Raises ValueError if any data is malformed.
        """
        return cls(
            restored["address"],
            cls.to_datetime(restored["at_loc_since"]),
            restored["driving"],
            restored["gps_accuracy"],
            cls.to_datetime(restored["last_seen"]),
            restored["latitude"],
            restored["longitude"],
            restored["place"],
            restored["speed"],
        )

    @classmethod
    def from_server(cls, raw_loc: Mapping[str, Any]) -> Self:
        """Initialize from Member's location data from server."""
        address1 = raw_loc["address1"] or None
        address2 = raw_loc["address2"] or None
        if address1 and address2:
            address: str | None = f"{address1}, {address2}"
        else:
            address = address1 or address2

        return cls(
            address,
            dt_util.utc_from_timestamp(int(raw_loc["since"])),
            bool(int(raw_loc["isDriving"])),
            # Life360 reports accuracy in feet, but Device Tracker expects
            # gps_accuracy in meters.
            round(
                DistanceConverter.convert(
                    float(raw_loc["accuracy"]), UnitOfLength.FEET, UnitOfLength.METERS
                )
            ),
            dt_util.utc_from_timestamp(int(raw_loc["timestamp"])),
            float(raw_loc["latitude"]),
            float(raw_loc["longitude"]),
            raw_loc["name"] or None,
            round(max(0, float(raw_loc["speed"]) * SPEED_FACTOR_MPH), SPEED_DIGITS),
        )


@dataclass
class LocationData:
    """Life360 Member location data."""

    details: LocationDetails
    battery_charging: bool = False
    battery_level: int = 0
    wifi_on: bool = False

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        Raises ValueError if any data is malformed.
        """
        return cls(
            LocationDetails.from_dict(restored["details"]),
            restored["battery_charging"],
            restored["battery_level"],
            restored["wifi_on"],
        )

    @classmethod
    def from_server(cls, raw_loc: Mapping[str, Any]) -> Self:
        """Initialize from Member's location data from server."""
        return cls(
            LocationDetails.from_server(raw_loc),
            bool(int(raw_loc["charge"])),
            int(float(raw_loc["battery"])),
            bool(int(raw_loc["wifiState"])),
        )


class NoLocReason(IntEnum):
    """Reason why Member location data is missing."""

    EXPLICIT = 3
    NO_REASON = 2
    NOT_SHARING = 1
    NOT_FOUND = 0
    NOT_SET = -1


@dataclass
class MemberData(ExtraStoredData):
    """Life360 Member data."""

    details: MemberDetails
    loc: LocationData | None = None
    loc_missing: NoLocReason = NoLocReason.NOT_SET
    err_msg: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary.

        Raises KeyError if any data is missing.
        Raises ValueError if any data is malformed.
        """
        if restored_loc := restored["loc"]:
            loc = LocationData.from_dict(restored_loc)
        else:
            loc = None
        return cls(
            MemberDetails.from_dict(restored["details"]),
            loc,
            NoLocReason(restored["loc_missing"]),
            restored["err_msg"],
        )

    @classmethod
    def from_server(cls, raw_member: Mapping[str, Any]) -> Self:
        """Initialize from Member's data from server."""
        details = MemberDetails.from_server(raw_member)

        if not int(raw_member["features"]["shareLocation"]):
            # Member isn't sharing location with this Circle.
            return cls(details, loc_missing=NoLocReason.NOT_SHARING)

        if not (raw_loc := raw_member["location"]):
            if err_msg := raw_member["issues"]["title"]:
                if extended_reason := raw_member["issues"]["dialog"]:
                    err_msg = f"{err_msg}: {extended_reason}"
                loc_missing = NoLocReason.EXPLICIT
            else:
                err_msg = (
                    "The user may have lost connection to Life360. "
                    "See https://www.life360.com/support/"
                )
                loc_missing = NoLocReason.NO_REASON
            return cls(details, loc_missing=loc_missing, err_msg=err_msg)

        return cls(details, LocationData.from_server(raw_loc))

    # Since a Member can exist in more than one Circle, and the data retrieved for the
    # Member might be different in each (e.g., some might not share location info but
    # others do), provide a means to find the "best" data for the Member from a list of
    # data, one from each Circle. Implementing the __lt__ method is all that is needed
    # for the built-in sorted function.
    def __lt__(self, other: MemberData) -> bool:
        """Determine if this member should sort before another."""
        if not self.loc:
            return other.loc is not None or self.loc_missing < other.loc_missing
        if not other.loc:
            return False
        return self.loc.details.last_seen < other.loc.details.last_seen


Members = dict[MemberID, MemberData]


class DeviceType(IntEnum):
    """Device type."""

    TILE = 1
    JIOBIT = 2  # Pet GPS tracker
    UNKNOWN = 0


@dataclass
class DeviceLocationDetails:
    """Device location details."""

    latitude: float
    longitude: float
    last_updated: datetime
    accuracy: int | None = None  # meters

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary."""
        return cls(
            restored["latitude"],
            restored["longitude"],
            LocationDetails.to_datetime(restored["last_updated"]),
            restored.get("accuracy"),
        )

    @classmethod
    def from_server(cls, raw_loc: Mapping[str, Any]) -> Self:
        """Initialize from device location data from server."""
        # Handle different timestamp formats
        timestamp = raw_loc.get("timestamp") or raw_loc.get("lastUpdated")
        if isinstance(timestamp, (int, float)):
            last_updated = dt_util.utc_from_timestamp(timestamp)
        elif timestamp:
            last_updated = dt_util.parse_datetime(timestamp) or dt_util.utcnow()
            last_updated = dt_util.as_utc(last_updated)
        else:
            last_updated = dt_util.utcnow()

        return cls(
            float(raw_loc.get("latitude") or raw_loc.get("lat", 0)),
            float(raw_loc.get("longitude") or raw_loc.get("lng", 0)),
            last_updated,
            int(raw_loc["accuracy"]) if raw_loc.get("accuracy") else None,
        )


@dataclass
class DeviceData(ExtraStoredData):
    """Device data (Tile, Jiobit/Pet GPS)."""

    device_id: str
    name: str
    device_type: DeviceType
    location: DeviceLocationDetails | None = None
    battery_level: int | None = None
    battery_status: str | None = None  # e.g., "LOW", "NORMAL"
    entity_picture: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, restored: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary."""
        if restored_loc := restored.get("location"):
            location = DeviceLocationDetails.from_dict(restored_loc)
        else:
            location = None
        return cls(
            restored["device_id"],
            restored["name"],
            DeviceType(restored["device_type"]),
            location,
            restored.get("battery_level"),
            restored.get("battery_status"),
            restored.get("entity_picture"),
        )

    @classmethod
    def from_server(
        cls, raw_device: Mapping[str, Any], provider: str = "tile"
    ) -> Self:
        """Initialize from device data from server."""
        device_id = raw_device.get("id") or raw_device.get("deviceId", "")

        # Try to get name from various fields in order of preference
        # CloudEvents APIs often nest names in different locations
        name = (
            raw_device.get("name") or
            raw_device.get("deviceName") or
            raw_device.get("tileName") or
            raw_device.get("label") or
            raw_device.get("nickname") or
            raw_device.get("displayName") or
            raw_device.get("title")
        )
        if not name:
            # Create a friendly name from provider and short device ID
            short_id = device_id[-8:] if len(device_id) > 8 else device_id
            provider_name = provider.capitalize() if provider else "Device"
            name = f"{provider_name} {short_id}"

        # Determine device type
        if provider == "jiobit":
            device_type = DeviceType.JIOBIT
        elif provider == "tile":
            device_type = DeviceType.TILE
        else:
            device_type = DeviceType.UNKNOWN

        # Parse location if present
        location = None
        if raw_loc := raw_device.get("location"):
            location = DeviceLocationDetails.from_server(raw_loc)
        elif "latitude" in raw_device or "lat" in raw_device:
            location = DeviceLocationDetails.from_server(raw_device)

        # Parse battery info
        battery_level = None
        battery_status = None
        if battery := raw_device.get("battery"):
            if isinstance(battery, dict):
                battery_level = battery.get("level")
                battery_status = battery.get("status")
            elif isinstance(battery, (int, float)):
                battery_level = int(battery)

        return cls(
            device_id,
            name,
            device_type,
            location,
            battery_level,
            battery_status,
            raw_device.get("avatar") or raw_device.get("image"),
        )


Devices = dict[DeviceID, DeviceData]

PlaceID = NewType("PlaceID", str)


@dataclass
class PlaceData:
    """Life360 Place data."""

    place_id: str
    name: str
    latitude: float
    longitude: float
    radius: float  # meters
    source_id: str | None = None  # Original source (e.g., Google Places ID)

    @classmethod
    def from_server(cls, raw_place: Mapping[str, Any]) -> Self:
        """Initialize from place data from server."""
        return cls(
            raw_place.get("id", ""),
            raw_place.get("name", "Unknown Place"),
            float(raw_place.get("latitude", 0)),
            float(raw_place.get("longitude", 0)),
            float(raw_place.get("radius", 100)),
            raw_place.get("sourceId"),
        )


@dataclass
class DrivingStats:
    """Driving statistics for a member."""

    total_distance: float = 0  # miles
    total_trips: int = 0
    max_speed: float = 0  # mph
    hard_brakes: int = 0
    rapid_accelerations: int = 0
    phone_usage_while_driving: int = 0  # minutes
    score: int | None = None  # driving score 0-100

    @classmethod
    def from_server(cls, raw_stats: Mapping[str, Any]) -> Self:
        """Initialize from stats data from server."""
        return cls(
            float(raw_stats.get("totalDistance", 0)),
            int(raw_stats.get("totalTrips", 0)),
            float(raw_stats.get("maxSpeed", 0)),
            int(raw_stats.get("hardBrakes", 0)),
            int(raw_stats.get("rapidAccelerations", 0)),
            int(raw_stats.get("phoneUsage", 0)),
            int(raw_stats["score"]) if raw_stats.get("score") is not None else None,
        )


@dataclass
class EmergencyContact:
    """Emergency contact data."""

    name: str
    phone: str
    relationship: str | None = None

    @classmethod
    def from_server(cls, raw_contact: Mapping[str, Any]) -> Self:
        """Initialize from contact data from server."""
        return cls(
            raw_contact.get("name", "Unknown"),
            raw_contact.get("phone", ""),
            raw_contact.get("relationship"),
        )


@dataclass
class TripData:
    """Trip data from driving behavior."""

    trip_id: str
    start_time: datetime
    end_time: datetime
    start_address: str | None
    end_address: str | None
    distance: float  # miles
    duration: int  # seconds
    max_speed: float  # mph
    hard_brakes: int = 0
    rapid_accelerations: int = 0

    @classmethod
    def from_server(cls, raw_trip: Mapping[str, Any]) -> Self:
        """Initialize from trip data from server."""
        return cls(
            raw_trip.get("id", ""),
            dt_util.utc_from_timestamp(int(raw_trip.get("startTime", 0))),
            dt_util.utc_from_timestamp(int(raw_trip.get("endTime", 0))),
            raw_trip.get("startAddress"),
            raw_trip.get("endAddress"),
            float(raw_trip.get("distance", 0)),
            int(raw_trip.get("duration", 0)),
            float(raw_trip.get("maxSpeed", 0)),
            int(raw_trip.get("hardBrakes", 0)),
            int(raw_trip.get("rapidAccelerations", 0)),
        )


@dataclass
class GeofenceZone:
    """Geofence zone data."""

    zone_id: str
    name: str
    latitude: float
    longitude: float
    radius: float  # meters
    zone_type: str | None = None  # e.g., "arrival", "departure", "both"
    active: bool = True

    @classmethod
    def from_server(cls, raw_zone: Mapping[str, Any]) -> Self:
        """Initialize from zone data from server."""
        return cls(
            raw_zone.get("id", ""),
            raw_zone.get("name", "Unknown Zone"),
            float(raw_zone.get("latitude", 0)),
            float(raw_zone.get("longitude", 0)),
            float(raw_zone.get("radius", 100)),
            raw_zone.get("type"),
            raw_zone.get("active", True),
        )


@dataclass
class PlaceAlert:
    """Place alert configuration."""

    alert_id: str
    place_id: str
    place_name: str
    member_id: str
    member_name: str
    alert_type: str  # "arrival", "departure", "both"
    enabled: bool = True

    @classmethod
    def from_server(cls, raw_alert: Mapping[str, Any]) -> Self:
        """Initialize from alert data from server."""
        return cls(
            raw_alert.get("id", ""),
            raw_alert.get("placeId", ""),
            raw_alert.get("placeName", "Unknown Place"),
            raw_alert.get("memberId", ""),
            raw_alert.get("memberName", "Unknown"),
            raw_alert.get("alertType", "both"),
            raw_alert.get("enabled", True),
        )


@dataclass
class ScheduledAlert:
    """Scheduled check-in alert."""

    alert_id: str
    member_id: str
    member_name: str
    schedule_time: str  # e.g., "08:00"
    days: list[str]  # e.g., ["monday", "tuesday"]
    enabled: bool = True
    last_check_in: datetime | None = None

    @classmethod
    def from_server(cls, raw_alert: Mapping[str, Any]) -> Self:
        """Initialize from scheduled alert data from server."""
        last_check = raw_alert.get("lastCheckIn")
        last_check_in = None
        if last_check:
            if isinstance(last_check, (int, float)):
                last_check_in = dt_util.utc_from_timestamp(last_check)
            elif isinstance(last_check, str):
                last_check_in = dt_util.parse_datetime(last_check)

        return cls(
            raw_alert.get("id", ""),
            raw_alert.get("memberId", ""),
            raw_alert.get("memberName", "Unknown"),
            raw_alert.get("time", ""),
            raw_alert.get("days", []),
            raw_alert.get("enabled", True),
            last_check_in,
        )


@dataclass
class MemberRole:
    """Member role in a circle."""

    member_id: str
    role: str  # "admin", "member"
    is_admin: bool = False

    @classmethod
    def from_server(cls, raw_role: Mapping[str, Any]) -> Self:
        """Initialize from role data from server."""
        role = raw_role.get("role", "member")
        return cls(
            raw_role.get("memberId", ""),
            role,
            role.lower() == "admin",
        )


@dataclass
class DeviceIssue:
    """Device issue/error data."""

    device_id: str
    device_name: str
    issue_type: str
    message: str
    severity: str | None = None  # "warning", "error"
    timestamp: datetime | None = None

    @classmethod
    def from_server(cls, raw_issue: Mapping[str, Any]) -> Self:
        """Initialize from issue data from server."""
        ts = raw_issue.get("timestamp")
        timestamp = None
        if ts:
            if isinstance(ts, (int, float)):
                timestamp = dt_util.utc_from_timestamp(ts)
            elif isinstance(ts, str):
                timestamp = dt_util.parse_datetime(ts)

        return cls(
            raw_issue.get("deviceId", ""),
            raw_issue.get("deviceName", "Unknown"),
            raw_issue.get("type", "unknown"),
            raw_issue.get("message", ""),
            raw_issue.get("severity"),
            timestamp,
        )


@dataclass
class UserProfile:
    """Current user profile data."""

    user_id: str
    first_name: str
    last_name: str
    email: str | None = None
    phone: str | None = None
    avatar: str | None = None
    created_at: datetime | None = None

    @classmethod
    def from_server(cls, raw_user: Mapping[str, Any]) -> Self:
        """Initialize from user data from server."""
        created = raw_user.get("createdAt")
        created_at = None
        if created:
            if isinstance(created, (int, float)):
                created_at = dt_util.utc_from_timestamp(created)
            elif isinstance(created, str):
                created_at = dt_util.parse_datetime(created)

        return cls(
            raw_user.get("id", ""),
            raw_user.get("firstName", ""),
            raw_user.get("lastName", ""),
            raw_user.get("email"),
            raw_user.get("phone"),
            raw_user.get("avatar"),
            created_at,
        )


@dataclass
class ConnectedIntegration:
    """Connected integration/app data."""

    integration_id: str
    name: str
    provider: str
    connected: bool = True
    status: str | None = None

    @classmethod
    def from_server(cls, raw_integration: Mapping[str, Any]) -> Self:
        """Initialize from integration data from server."""
        return cls(
            raw_integration.get("id", ""),
            raw_integration.get("name", "Unknown"),
            raw_integration.get("provider", ""),
            raw_integration.get("connected", True),
            raw_integration.get("status"),
        )


@dataclass
class CircleData:
    """Circle data."""

    name: str
    aids: set[AccountID] = field(default_factory=set, compare=False)
    mids: set[MemberID] = field(default_factory=set)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary."""
        return cls(data["name"], set(data["aids"]), set(data["mids"]))


@dataclass
class CirclesMembersData:
    """Circles & Members data."""

    circles: dict[CircleID, CircleData] = field(default_factory=dict)
    mem_details: dict[MemberID, MemberDetails] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a dict representation of the data."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        """Initialize from a dictionary."""
        circles = {
            cid: CircleData.from_dict(circle_data)
            for cid, circle_data in data["circles"].items()
        }
        mem_details = {
            mid: MemberDetails.from_dict(mem_data)
            for mid, mem_data in data["mem_details"].items()
        }
        return cls(circles, mem_details)


class Life360Store:
    """Life360 storage."""

    _loaded_ok: bool = False
    data: CirclesMembersData

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize storage."""
        self._store = Store[dict[str, Any]](hass, 1, DOMAIN)

    @property
    def loaded_ok(self) -> bool:
        """Return if load succeeded."""
        return self._loaded_ok

    @property
    def circles(self) -> dict[CircleID, CircleData]:
        """Return circles."""
        return self.data.circles

    @circles.setter
    def circles(self, circles: dict[CircleID, CircleData]) -> None:
        """Update circles."""
        self.data.circles = circles

    @property
    def mem_details(self) -> dict[MemberID, MemberDetails]:
        """Return Member static details."""
        return self.data.mem_details

    @mem_details.setter
    def mem_details(self, mem_details: dict[MemberID, MemberDetails]) -> None:
        """Update Member static details."""
        self.data.mem_details = mem_details

    async def load(self) -> bool:
        """Load from storage.

        Should be called once, before data is accessed.
        Returns True if store was read ok.
        Initializes data and returns False otherwise.
        Also sets loaded_ok accordingly.
        """
        if store_data := await self._store.async_load():
            self.data = CirclesMembersData.from_dict(store_data)
            self._loaded_ok = True
        else:
            self.data = CirclesMembersData()
        return self._loaded_ok

    async def save(self) -> None:
        """Write to storage."""
        await self._store.async_save(self.data.as_dict())

    async def remove(self) -> None:
        """Remove storage."""
        await self._store.async_remove()
