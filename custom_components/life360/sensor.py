"""Life360 sensor platform."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import ATTRIBUTION
from .coordinator import CirclesMembersDataUpdateCoordinator, L360ConfigEntry
from .helpers import (
    DeviceIssue,
    DrivingStats,
    MemberID,
    ScheduledAlert,
    TripData,
    UserProfile,
)

_LOGGER = logging.getLogger(__name__)

# Update interval for driving stats (less frequent than location)
STATS_UPDATE_INTERVAL = timedelta(minutes=15)


@dataclass(frozen=True)
class Life360SensorEntityDescription(SensorEntityDescription):
    """Describes Life360 sensor entity."""

    value_fn: str = ""


DRIVING_SENSORS: tuple[Life360SensorEntityDescription, ...] = (
    Life360SensorEntityDescription(
        key="total_distance",
        name="Weekly Distance",
        native_unit_of_measurement=UnitOfLength.MILES,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:map-marker-distance",
        value_fn="total_distance",
    ),
    Life360SensorEntityDescription(
        key="total_trips",
        name="Weekly Trips",
        native_unit_of_measurement="trips",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:car-multiple",
        value_fn="total_trips",
    ),
    Life360SensorEntityDescription(
        key="max_speed",
        name="Max Speed This Week",
        native_unit_of_measurement=UnitOfSpeed.MILES_PER_HOUR,
        device_class=SensorDeviceClass.SPEED,
        icon="mdi:speedometer",
        value_fn="max_speed",
    ),
    Life360SensorEntityDescription(
        key="hard_brakes",
        name="Hard Brakes This Week",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:car-brake-alert",
        value_fn="hard_brakes",
    ),
    Life360SensorEntityDescription(
        key="rapid_accelerations",
        name="Rapid Accelerations This Week",
        native_unit_of_measurement="events",
        state_class=SensorStateClass.TOTAL,
        icon="mdi:car-traction-control",
        value_fn="rapid_accelerations",
    ),
    Life360SensorEntityDescription(
        key="phone_usage",
        name="Phone Usage While Driving",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL,
        icon="mdi:cellphone-off",
        value_fn="phone_usage_while_driving",
    ),
    Life360SensorEntityDescription(
        key="driving_score",
        name="Driving Score",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:shield-car",
        value_fn="score",
    ),
)


class DrivingStatsCoordinator(DataUpdateCoordinator[dict[MemberID, DrivingStats]]):
    """Coordinator for driving statistics."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Driving Stats",
            update_interval=STATS_UPDATE_INTERVAL,
        )
        self._coordinator = coordinator
        self.data: dict[MemberID, DrivingStats] = {}

    async def _async_update_data(self) -> dict[MemberID, DrivingStats]:
        """Fetch driving stats for all members."""
        stats: dict[MemberID, DrivingStats] = {}

        for mid in self._coordinator.data.mem_details:
            # Get circles this member is in
            for cid in self._coordinator.mem_circles.get(mid, set()):
                member_stats = await self._coordinator.get_driving_stats(cid, mid)
                if member_stats:
                    stats[mid] = member_stats
                    break  # Got stats, no need to check other circles

        return stats


class CrashDetectionCoordinator(DataUpdateCoordinator[bool | None]):
    """Coordinator for crash detection status."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Crash Detection",
            update_interval=timedelta(hours=1),  # Check hourly
        )
        self._coordinator = coordinator
        self.data: bool | None = None

    async def _async_update_data(self) -> bool | None:
        """Fetch crash detection status."""
        return await self._coordinator.get_crash_detection_status()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: L360ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Life360 sensors."""
    coordinator = entry.runtime_data.coordinator

    # Create driving stats coordinator
    driving_coordinator = DrivingStatsCoordinator(hass, coordinator)
    await driving_coordinator.async_config_entry_first_refresh()

    # Create crash detection coordinator
    crash_coordinator = CrashDetectionCoordinator(hass, coordinator)
    await crash_coordinator.async_config_entry_first_refresh()

    # Create trip history coordinator
    trip_coordinator = TripHistoryCoordinator(hass, coordinator)
    await trip_coordinator.async_config_entry_first_refresh()

    # Create scheduled alerts coordinator
    scheduled_coordinator = ScheduledAlertsCoordinator(hass, coordinator)
    await scheduled_coordinator.async_config_entry_first_refresh()

    # Create device issues coordinator
    issues_coordinator = DeviceIssuesCoordinator(hass, coordinator)
    await issues_coordinator.async_config_entry_first_refresh()

    # Create user profile coordinator
    profile_coordinator = UserProfileCoordinator(hass, coordinator)
    await profile_coordinator.async_config_entry_first_refresh()

    entities: list[SensorEntity] = []

    # Add crash detection sensor
    entities.append(CrashDetectionSensor(crash_coordinator))

    # Add device issues sensor
    entities.append(DeviceIssuesSensor(issues_coordinator))

    # Add user profile sensor
    entities.append(UserProfileSensor(profile_coordinator))

    # Add driving stats, trip history, and scheduled alerts sensors for each member
    for mid in coordinator.data.mem_details:
        member_name = coordinator.data.mem_details[mid].name
        for description in DRIVING_SENSORS:
            entities.append(
                DrivingStatsSensor(
                    driving_coordinator,
                    mid,
                    member_name,
                    description,
                )
            )
        # Add trip history sensor for each member
        entities.append(
            TripHistorySensor(
                trip_coordinator,
                mid,
                member_name,
            )
        )
        # Add scheduled alerts sensor for each member
        entities.append(
            ScheduledAlertsSensor(
                scheduled_coordinator,
                mid,
                member_name,
            )
        )

    async_add_entities(entities)


class DrivingStatsSensor(
    CoordinatorEntity[DrivingStatsCoordinator], SensorEntity
):
    """Sensor for driving statistics."""

    _attr_attribution = ATTRIBUTION
    entity_description: Life360SensorEntityDescription

    def __init__(
        self,
        coordinator: DrivingStatsCoordinator,
        mid: MemberID,
        member_name: str,
        description: Life360SensorEntityDescription,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._mid = mid
        self._member_name = member_name
        self.entity_description = description
        self._attr_unique_id = f"{mid}_{description.key}"
        self._attr_name = f"{member_name} {description.name}"

    @property
    def native_value(self) -> float | int | None:
        """Return the state of the sensor."""
        if self._mid not in self.coordinator.data:
            return None
        stats = self.coordinator.data[self._mid]
        return getattr(stats, self.entity_description.value_fn, None)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._mid in self.coordinator.data


class CrashDetectionSensor(
    CoordinatorEntity[CrashDetectionCoordinator], SensorEntity
):
    """Sensor for crash detection status."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:car-emergency"
    _attr_name = "Life360 Crash Detection"
    _attr_unique_id = "life360_crash_detection"

    def __init__(self, coordinator: CrashDetectionCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if self.coordinator.data is None:
            return "unknown"
        return "enabled" if self.coordinator.data else "disabled"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "enabled": self.coordinator.data,
        }


class TripHistoryCoordinator(DataUpdateCoordinator[dict[MemberID, list[TripData]]]):
    """Coordinator for trip history."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Trip History",
            update_interval=timedelta(minutes=30),  # Less frequent updates
        )
        self._coordinator = coordinator
        self.data: dict[MemberID, list[TripData]] = {}

    async def _async_update_data(self) -> dict[MemberID, list[TripData]]:
        """Fetch trip history for all members."""
        trips: dict[MemberID, list[TripData]] = {}

        for mid in self._coordinator.data.mem_details:
            # Get circles this member is in
            for cid in self._coordinator.mem_circles.get(mid, set()):
                member_trips = await self._coordinator.get_trip_history(cid, mid, limit=5)
                if member_trips:
                    trips[mid] = member_trips
                    break  # Got trips, no need to check other circles

        return trips


class TripHistorySensor(
    CoordinatorEntity[TripHistoryCoordinator], SensorEntity
):
    """Sensor for trip history."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:car-clock"

    def __init__(
        self,
        coordinator: TripHistoryCoordinator,
        mid: MemberID,
        member_name: str,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._mid = mid
        self._member_name = member_name
        self._attr_unique_id = f"{mid}_trip_history"
        self._attr_name = f"{member_name} Recent Trips"

    @property
    def native_value(self) -> int:
        """Return the number of recent trips."""
        if self._mid not in self.coordinator.data:
            return 0
        return len(self.coordinator.data[self._mid])

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "trips"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if self._mid not in self.coordinator.data:
            return {}

        trips = self.coordinator.data[self._mid]
        if not trips:
            return {"trips": []}

        # Return last 5 trips with details
        trip_list = []
        for trip in trips[:5]:
            trip_list.append({
                "start_time": trip.start_time.isoformat(),
                "end_time": trip.end_time.isoformat(),
                "start_address": trip.start_address,
                "end_address": trip.end_address,
                "distance_miles": trip.distance,
                "duration_minutes": round(trip.duration / 60, 1),
                "max_speed_mph": trip.max_speed,
                "hard_brakes": trip.hard_brakes,
                "rapid_accelerations": trip.rapid_accelerations,
            })

        # Calculate totals
        total_distance = sum(t.distance for t in trips)
        total_duration = sum(t.duration for t in trips)

        return {
            "trips": trip_list,
            "total_distance_miles": round(total_distance, 1),
            "total_duration_minutes": round(total_duration / 60, 1),
            "last_trip_end": trips[0].end_time.isoformat() if trips else None,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._mid in self.coordinator.data


class ScheduledAlertsCoordinator(
    DataUpdateCoordinator[dict[MemberID, list[ScheduledAlert]]]
):
    """Coordinator for scheduled alerts."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Scheduled Alerts",
            update_interval=timedelta(hours=1),
        )
        self._coordinator = coordinator
        self.data: dict[MemberID, list[ScheduledAlert]] = {}

    async def _async_update_data(self) -> dict[MemberID, list[ScheduledAlert]]:
        """Fetch scheduled alerts for all members."""
        alerts: dict[MemberID, list[ScheduledAlert]] = {}

        for mid in self._coordinator.data.mem_details:
            for cid in self._coordinator.mem_circles.get(mid, set()):
                member_alerts = await self._coordinator.get_scheduled_alerts(cid, mid)
                if member_alerts:
                    alerts[mid] = member_alerts
                    break

        return alerts


class ScheduledAlertsSensor(
    CoordinatorEntity[ScheduledAlertsCoordinator], SensorEntity
):
    """Sensor for scheduled check-in alerts."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self,
        coordinator: ScheduledAlertsCoordinator,
        mid: MemberID,
        member_name: str,
    ) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)
        self._mid = mid
        self._member_name = member_name
        self._attr_unique_id = f"{mid}_scheduled_alerts"
        self._attr_name = f"{member_name} Scheduled Alerts"

    @property
    def native_value(self) -> int:
        """Return the number of scheduled alerts."""
        if self._mid not in self.coordinator.data:
            return 0
        return len(self.coordinator.data[self._mid])

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "alerts"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if self._mid not in self.coordinator.data:
            return {}

        alerts = self.coordinator.data[self._mid]
        if not alerts:
            return {"alerts": []}

        alert_list = []
        for alert in alerts:
            alert_list.append({
                "alert_id": alert.alert_id,
                "time": alert.schedule_time,
                "days": alert.days,
                "enabled": alert.enabled,
                "last_check_in": (
                    alert.last_check_in.isoformat() if alert.last_check_in else None
                ),
            })

        return {"alerts": alert_list}

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self._mid in self.coordinator.data


class DeviceIssuesCoordinator(DataUpdateCoordinator[list[DeviceIssue]]):
    """Coordinator for device issues."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 Device Issues",
            update_interval=timedelta(hours=1),
        )
        self._coordinator = coordinator
        self.data: list[DeviceIssue] = []

    async def _async_update_data(self) -> list[DeviceIssue]:
        """Fetch device issues."""
        return await self._coordinator.get_device_issues()


class DeviceIssuesSensor(CoordinatorEntity[DeviceIssuesCoordinator], SensorEntity):
    """Sensor for device issues."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:alert-circle"
    _attr_name = "Life360 Device Issues"
    _attr_unique_id = "life360_device_issues"

    def __init__(self, coordinator: DeviceIssuesCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)

    @property
    def native_value(self) -> int:
        """Return the number of device issues."""
        return len(self.coordinator.data)

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "issues"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {"issues": []}

        issue_list = []
        for issue in self.coordinator.data:
            issue_list.append({
                "device_id": issue.device_id,
                "device_name": issue.device_name,
                "type": issue.issue_type,
                "message": issue.message,
                "severity": issue.severity,
                "timestamp": (
                    issue.timestamp.isoformat() if issue.timestamp else None
                ),
            })

        return {"issues": issue_list}


class UserProfileCoordinator(DataUpdateCoordinator[UserProfile | None]):
    """Coordinator for user profile."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: CirclesMembersDataUpdateCoordinator,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="Life360 User Profile",
            update_interval=timedelta(hours=24),  # Profile rarely changes
        )
        self._coordinator = coordinator
        self.data: UserProfile | None = None

    async def _async_update_data(self) -> UserProfile | None:
        """Fetch user profile."""
        return await self._coordinator.get_user_profile()


class UserProfileSensor(CoordinatorEntity[UserProfileCoordinator], SensorEntity):
    """Sensor for user profile."""

    _attr_attribution = ATTRIBUTION
    _attr_icon = "mdi:account"
    _attr_name = "Life360 Account"
    _attr_unique_id = "life360_user_profile"

    def __init__(self, coordinator: UserProfileCoordinator) -> None:
        """Initialize sensor."""
        super().__init__(coordinator)

    @property
    def native_value(self) -> str:
        """Return the user's name."""
        if not self.coordinator.data:
            return "Unknown"
        profile = self.coordinator.data
        name = f"{profile.first_name} {profile.last_name}".strip()
        return name or "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}

        profile = self.coordinator.data
        return {
            "user_id": profile.user_id,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "email": profile.email,
            "phone": profile.phone,
            "avatar": profile.avatar,
            "created_at": (
                profile.created_at.isoformat() if profile.created_at else None
            ),
        }

    @property
    def entity_picture(self) -> str | None:
        """Return the entity picture."""
        if self.coordinator.data:
            return self.coordinator.data.avatar
        return None
