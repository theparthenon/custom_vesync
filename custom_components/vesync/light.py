"""Support for VeSync bulbs and wall dimmers."""
import logging

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .common import VeSyncDevice, has_feature
from .const import DEV_TYPE_TO_HA, DOMAIN, VS_DISCOVERY, VS_FAN_TYPES, VS_LIGHTS

_LOGGER = logging.getLogger(__name__)

MIN_COLOR_TEMP_KELVIN = 2700
MAX_COLOR_TEMP_KELVIN = 6500

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up lights."""

    coordinator = hass.data[DOMAIN][config_entry.entry_id]["coordinator"]

    @callback
    def discover(devices):
        """Add new devices to platform."""
        _setup_entities(devices, async_add_entities, coordinator)

    config_entry.async_on_unload(
        async_dispatcher_connect(hass, VS_DISCOVERY.format(VS_LIGHTS), discover)
    )

    _setup_entities(
        hass.data[DOMAIN][config_entry.entry_id][VS_LIGHTS],
        async_add_entities,
        coordinator,
    )


@callback
def _setup_entities(devices, async_add_entities, coordinator):
    """Check if device is online and add entity."""
    entities = []
    for dev in devices:
        if DEV_TYPE_TO_HA.get(dev.device_type) in ("walldimmer", "bulb-dimmable"):
            entities.append(VeSyncDimmableLightHA(dev, coordinator))
        if DEV_TYPE_TO_HA.get(dev.device_type) in ("bulb-tunable-white",):
            entities.append(VeSyncTunableWhiteLightHA(dev, coordinator))
        if hasattr(dev, "night_light") and dev.night_light:
            entities.append(VeSyncNightLightHA(dev, coordinator))

    async_add_entities(entities, update_before_add=True)


def _vesync_brightness_to_ha(vesync_brightness):
    try:
        # check for validity of brightness value received
        brightness_value = int(vesync_brightness)
    except ValueError:
        # deal if any unexpected/non numeric value
        _LOGGER.debug(
            "VeSync - received unexpected 'brightness' value from pyvesync api: %s",
            vesync_brightness,
        )
        return None
    # convert percent brightness to ha expected range
    return round((max(1, brightness_value) / 100) * 255)


def _ha_brightness_to_vesync(ha_brightness):
    # get brightness from HA data
    brightness = int(ha_brightness)
    # ensure value between 1-255
    brightness = max(1, min(brightness, 255))
    # convert to percent that vesync api expects
    brightness = round((brightness / 255) * 100)
    return max(1, min(brightness, 100))


class VeSyncBaseLight(VeSyncDevice, LightEntity):
    """Base class for VeSync Light Devices Representations."""

    def __init_(self, light, coordinator):
        """Initialize the VeSync light device."""
        super().__init__(light, coordinator)

    @property
    def brightness(self):
        """Get light brightness."""
        # get value from pyvesync library api,
        return _vesync_brightness_to_ha(self.device.brightness)

    def turn_on(self, **kwargs):
        """Turn the device on."""
        attribute_adjustment_only = False
        # set white temperature
        if (
            self.color_mode == ColorMode.COLOR_TEMP
            and ATTR_COLOR_TEMP_KELVIN in kwargs
        ):
            # get white temperature from HA data (in Kelvin)
            kelvin = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
            # ensure value within supported Kelvin range
            kelvin = max(
                self.min_color_temp_kelvin, min(kelvin, self.max_color_temp_kelvin)
            )
            # convert Kelvin to percent value that api expects
            # (pyvesync scale: 0 = warmest, 100 = coldest — matches Kelvin direction)
            color_temp = round(
                (kelvin - self.min_color_temp_kelvin) / (self.max_color_temp_kelvin - self.min_color_temp_kelvin)
                * 100
            )
            # ensure value between 0-100
            color_temp = max(0, min(color_temp, 100))
            # call pyvesync library api method to set color_temp
            self.device.set_color_temp(color_temp)
            # flag attribute_adjustment_only, so it doesn't turn_on the device redundantly
            attribute_adjustment_only = True
        # set brightness level
        if (
            self.color_mode in (ColorMode.BRIGHTNESS, ColorMode.COLOR_TEMP)
            and ATTR_BRIGHTNESS in kwargs
        ):
            # get brightness from HA data
            brightness = _ha_brightness_to_vesync(kwargs[ATTR_BRIGHTNESS])
            self.device.set_brightness(brightness)
            # flag attribute_adjustment_only, so it doesn't turn_on the device redundantly
            attribute_adjustment_only = True
        # check flag if should skip sending the turn_on command
        if attribute_adjustment_only:
            return
        # send turn_on command to pyvesync api
        self.device.turn_on()


class VeSyncDimmableLightHA(VeSyncBaseLight, LightEntity):
    """Representation of a VeSync dimmable light device."""

    def __init__(self, device, coordinator) -> None:
        """Initialize the VeSync dimmable light device."""
        super().__init__(device, coordinator)

    @property
    def color_mode(self):
        """Set color mode for this entity."""
        return ColorMode.BRIGHTNESS

    @property
    def supported_color_modes(self):
        """Flag supported color_modes (in an array format)."""
        return [ColorMode.BRIGHTNESS]


class VeSyncTunableWhiteLightHA(VeSyncBaseLight, LightEntity):
    """Representation of a VeSync Tunable White Light device."""

    def __init__(self, device, coordinator) -> None:
        """Initialize the VeSync Tunable White Light device."""
        super().__init__(device, coordinator)

    @property
    def color_temp_kelvin(self):
        """Get device white temperature in Kelvin."""
        result = self.device.color_temp_pct
        try:
            color_temp_value = int(result)
        except ValueError:
            _LOGGER.debug(
                "VeSync - received unexpected 'color_temp_pct' value from pyvesync api: %s",
                result,
            )
            return  None

        # ensure value between 0-100
        color_temp_value = max(0, min(color_temp_value, 100))
        # convert percent value to Kelvin
        # (pyvesync scale: 0 = warmest, 100 = coldest — matches Kelvin direction)
        kelvin = round(
            self.min_color_temp_kelvin
            + (self.max_color_temp_kelvin - self.min_color_temp_kelvin)
            * color_temp_value
            / 100
        )
        return max(self.min_color_temp_kelvin, min(kelvin, self.max_color_temp_kelvin))

    @property
    def min_color_temp_kelvin(self):
        """Set device warmest white temperature."""
        return MIN_COLOR_TEMP_KELVIN

    @property
    def max_color_temp_kelvin(self):
        """Set device coldest white temperature."""
        return MAX_COLOR_TEMP_KELVIN

    @property
    def color_mode(self):
        """Set color mode for this entity."""
        return ColorMode.COLOR_TEMP

    @property
    def supported_color_modes(self):
        """Flag supported color_modes (in an array format)."""
        return [ColorMode.COLOR_TEMP]


class VeSyncNightLightHA(VeSyncDimmableLightHA):
    """Representation of the night light on a VeSync device."""

    def __init__(self, device, coordinator) -> None:
        """Initialize the VeSync device."""
        super().__init__(device, coordinator)
        self.device = device
        self.has_brightness = has_feature(
            self.device, "details", "night_light_brightness"
        )

    @property
    def unique_id(self):
        """Return the ID of this device."""
        return f"{super().unique_id}-night-light"

    @property
    def name(self):
        """Return the name of the device."""
        return f"{super().name} night light"

    @property
    def brightness(self):
        """Get night light brightness."""
        return (
            _vesync_brightness_to_ha(self.device.details["night_light_brightness"])
            if self.has_brightness
            else {"on": 255, "dim": 125, "off": 0}[self.device.details["night_light"]]
        )

    @property
    def is_on(self):
        """Return True if night light is on."""
        if has_feature(self.device, "details", "night_light"):
            return self.device.details["night_light"] in ["on", "dim"]
        if self.has_brightness:
            return self.device.details["night_light_brightness"] > 0

    @property
    def entity_category(self):
        """Return the configuration entity category."""
        return EntityCategory.CONFIG

    def turn_on(self, **kwargs):
        """Turn the night light on."""
        if self.device._config_dict["module"] in VS_FAN_TYPES:
            if ATTR_BRIGHTNESS in kwargs and kwargs[ATTR_BRIGHTNESS] < 255:
                self.device.set_night_light("dim")
            else:
                self.device.set_night_light("on")
        elif ATTR_BRIGHTNESS in kwargs:
            self.device.set_night_light_brightness(
                _ha_brightness_to_vesync(kwargs[ATTR_BRIGHTNESS])
            )
        else:
            self.device.set_night_light_brightness(100)

    def turn_off(self, **kwargs):
        """Turn the night light off."""
        if self.device._config_dict["module"] in VS_FAN_TYPES:
            self.device.set_night_light("off")
        else:
            self.device.set_night_light_brightness(0)
