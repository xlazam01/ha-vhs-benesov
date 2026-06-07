from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VHSBenesovCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: VHSBenesovCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        VHSMeterIndexSensor(coordinator, entry),
        VHSLastReadingSensor(coordinator, entry),
        VHSCurrentMonthSensor(coordinator, entry),
        VHSMonthlyHistorySensor(coordinator, entry),
    ])


class _VHSBase(CoordinatorEntity[VHSBenesovCoordinator], SensorEntity):
    def __init__(self, coordinator: VHSBenesovCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "VHS Benešov Water Meter",
            "manufacturer": "VHS Benešov / SUEZ",
            "model": "Pracdis Smart Water Meter",
        }


class VHSMeterIndexSensor(_VHSBase):
    """Absolute meter reading — use this for the Energy dashboard (water)."""

    _attr_name = "Water Meter Index"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:water-pump"
    _attr_suggested_display_precision = 3

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_meter_index"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("meter_index")


class VHSLastReadingSensor(_VHSBase):
    """Timestamp of the last meter reading as reported by the portal."""

    _attr_name = "Water Meter Last Reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_last_reading"

    @property
    def native_value(self):
        raw = self.coordinator.data.get("last_reading_date")
        if not raw:
            return None
        # Already parsed to ISO 8601 by the coordinator; HA accepts datetime strings.
        from datetime import datetime, timezone
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None


class VHSCurrentMonthSensor(_VHSBase):
    """Consumption in the current (latest reported) month."""

    _attr_name = "Water Consumption This Month"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:water"
    _attr_suggested_display_precision = 3

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_current_month"

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.get("current_month_consumption")

    @property
    def extra_state_attributes(self) -> dict:
        return {"month": self.coordinator.data.get("current_month_label")}


class VHSMonthlyHistorySensor(_VHSBase):
    """Full monthly history stored as attributes; disabled by default."""

    _attr_name = "Water Monthly History"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_icon = "mdi:chart-bar"
    _attr_entity_registry_enabled_default = False
    _attr_suggested_display_precision = 3

    @property
    def unique_id(self) -> str:
        return f"{self._entry.entry_id}_monthly_history"

    @property
    def native_value(self) -> float | None:
        monthly = self.coordinator.data.get("monthly_consumption", {})
        return list(monthly.values())[-1] if monthly else None

    @property
    def extra_state_attributes(self) -> dict:
        return {"history": self.coordinator.data.get("monthly_consumption", {})}
