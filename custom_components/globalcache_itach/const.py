"""Constants for Global Caché iTach integration."""

from typing import Final

DOMAIN: Final = "globalcache_itach"
DEFAULT_PORT: Final = 4998
DEFAULT_CONNECT_TIMEOUT: Final = 10.0
DEFAULT_COMMAND_TIMEOUT: Final = 30.0
DEFAULT_CARRIER_HZ: Final = 38000
DEFAULT_REPEAT: Final = 1
DEFAULT_OFFSET: Final = 1

CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_DEVICE_NAME: Final = "device_name"

CONF_CONNECT_TIMEOUT: Final = "connect_timeout"
CONF_COMMAND_TIMEOUT: Final = "command_timeout"
CONF_DEFAULT_CONNECTOR: Final = "default_connector"
CONF_DEFAULT_FREQ: Final = "default_freq"
CONF_DEFAULT_REPEAT: Final = "default_repeat"
CONF_DEFAULT_OFFSET: Final = "default_offset"
CONF_ID_POLICY: Final = "id_policy"
CONF_FIXED_COMMAND_ID: Final = "fixed_command_id"

CONF_REMOTES: Final = "remotes"
CONF_REMOTE_ID: Final = "remote_id"
CONF_REMOTE_NAME: Final = "name"
CONF_MODULE: Final = "module"
CONF_CONN_PORT: Final = "port"
CONF_IR_COUNT: Final = "ir_count"
CONF_COMMANDS: Final = "commands"
CONF_CMD_NAME: Final = "name"
CONF_CMD_DATA: Final = "data"
CONF_CMD_FORMAT: Final = "format"
# Supported values: pronto (alias pronto_hex), gc_pairs (alias gc_sendir_tail), full_sendir
CONF_CMD_FREQ: Final = "freq"
CONF_CMD_REPEAT: Final = "repeat"
CONF_CMD_OFFSET: Final = "offset"
CONF_CMD_ID: Final = "command_id"

ID_POLICY_AUTO: Final = "auto"
ID_POLICY_FIXED: Final = "fixed"

EVENT_IR_LEARNED: Final = f"{DOMAIN}_ir_learned"
EVENT_IR_RECEIVED: Final = f"{DOMAIN}_ir_received"

SERVICE_SEND_RAW: Final = "send_raw"
SERVICE_SEND_COMMAND: Final = "send_command"
SERVICE_SENDIR: Final = "sendir"
SERVICE_STOP_IR: Final = "stop_ir"
SERVICE_IR_LEARNER_START: Final = "ir_learner_start"
SERVICE_IR_LEARNER_STOP: Final = "ir_learner_stop"
SERVICE_RECEIVE_IR: Final = "receive_ir"
SERVICE_GET_IR: Final = "get_ir"
SERVICE_SET_IR: Final = "set_ir"
SERVICE_GET_DEVICES: Final = "get_devices"
SERVICE_GET_VERSION: Final = "get_version"
SERVICE_GET_NET: Final = "get_net"
SERVICE_SET_LED_LIGHTING: Final = "set_led_lighting"
SERVICE_GET_LED_LIGHTING: Final = "get_led_lighting"

ATTR_DEVICE_ID: Final = "device_id"
ATTR_COMMAND: Final = "command"
ATTR_RESPONSE_LINES: Final = "response_lines"
ATTR_MODULE: Final = "module"
ATTR_PORT: Final = "port"
ATTR_COMMAND_ID: Final = "command_id"
ATTR_FREQUENCY: Final = "frequency"
ATTR_REPEAT: Final = "repeat"
ATTR_OFFSET: Final = "offset"
ATTR_PULSE_PAIRS: Final = "pulse_pairs"
ATTR_MODE: Final = "mode"
ATTR_ENABLED: Final = "enabled"
ATTR_INTENSITY: Final = "intensity"
ATTR_RAMP: Final = "ramp"

MANUFACTURER: Final = "Global Caché"
