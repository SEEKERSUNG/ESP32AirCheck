# -*- coding: utf-8 -*-
"""
Wi-Fi / Home Assistant MQTT 接入
================================================================
把传感器的数据通过 MQTT 发布给 Home Assistant, 并用 MQTT Discovery
自动创建设备和实体 (不需要手工写 configuration.yaml)。

启用方法:
    1. 在 src/secrets.py (已被 .gitignore 忽略) 填 Wi-Fi 和 MQTT broker;
    2. 部署后 HA 的 MQTT 集成会自动发现 "ESP32 AirCheck";
    3. 实体: 温度 / 湿度 / 甲醛 / VOC 指数 / 空气质量报警。

留空 secrets.py 里的 Wi-Fi 或 MQTT_HOST 时不启用网络, 不影响本地功能。
"""

import json
import time

try:
    import network
except ImportError:
    network = None

try:
    import ubinascii
except ImportError:
    import binascii as ubinascii

try:
    from umqtt.simple import MQTTClient
except ImportError:
    MQTTClient = None

try:
    import uasyncio as asyncio
except ImportError:
    import asyncio

import config as cfg

try:
    from time import ticks_ms, ticks_diff
except ImportError:
    _MASK = 0x3FFFFFFF

    def ticks_ms():
        return int(time.monotonic() * 1000) & _MASK

    def ticks_diff(a, b):
        d = (a - b) & _MASK
        if d >= 0x20000000:
            d -= 0x40000000
        return d


_TEMPLATE = "{{ value_json.%s if value_json.%s is not none else 'unknown' }}"


def build_state(snap):
    """把 SensorManager.snapshot() 转成 MQTT 状态 JSON。"""
    v = snap.get("values", {})
    alarm = snap.get("alarm", {})
    return {
        "temp": v.get("temp"),
        "rh": v.get("rh"),
        "hcho": v.get("hcho_mg"),
        "voc": v.get("tvoc"),
        "alarm": any(alarm.values()) if alarm else False,
        "uptime": int(snap.get("uptime", 0)),
    }


def build_discovery(device_id, name, version, state_topic, avail_topic,
                    prefix="homeassistant"):
    """生成 Home Assistant MQTT Discovery 的 (topic, payload) 列表。"""
    uid = "esp32aircheck_" + device_id
    device = {
        "identifiers": [uid],
        "name": name,
        "manufacturer": "DIY",
        "model": "ESP32AirCheck",
        "sw_version": version,
    }

    # 实体 key, 状态 JSON 字段, 显示名, 单位, device_class, icon
    entities = (
        ("temperature", "temp", "温度", "°C", "temperature", None),
        ("humidity", "rh", "湿度", "%", "humidity", None),
        ("hcho", "hcho", "甲醛", "mg/m³", None, "mdi:air-filter"),
        ("voc", "voc", "VOC 指数", "idx", None, "mdi:air-filter"),
    )
    out = []
    for key, state_key, label, unit, dclass, icon in entities:
        data = {
            "name": label,
            "unique_id": uid + "_" + key,
            "state_topic": state_topic,
            "availability_topic": avail_topic,
            "value_template": _TEMPLATE % (state_key, state_key),
            "state_class": "measurement",
            "device": device,
        }
        if unit:
            data["unit_of_measurement"] = unit
        if dclass:
            data["device_class"] = dclass
        if icon:
            data["icon"] = icon
        out.append(("%s/sensor/%s_%s/config" % (prefix, uid, key),
                    json.dumps(data)))

    alarm = {
        "name": "空气质量报警",
        "unique_id": uid + "_alarm",
        "state_topic": state_topic,
        "availability_topic": avail_topic,
        "value_template": "{{ 'ON' if value_json.alarm else 'OFF' }}",
        "payload_on": "ON",
        "payload_off": "OFF",
        "device_class": "problem",
        "icon": "mdi:alert",
        "device": device,
    }
    out.append(("%s/binary_sensor/%s_alarm/config" % (prefix, uid),
                json.dumps(alarm)))
    return out


class Net:
    """一个后台 asyncio 任务: 维护 Wi-Fi/MQTT, 周期发布状态。"""

    def __init__(self, snapshot, version="", log=print):
        self.snapshot = snapshot
        self.version = version
        self.log = log
        self.enabled = bool(cfg.WIFI_SSID and cfg.MQTT_HOST)
        self.wlan = None
        self.client = None
        self.device_id = None
        self.state_topic = None
        self.avail_topic = None

    async def run(self):
        if not self.enabled:
            self.log("[net] 未配置 Wi-Fi/MQTT, 跳过")
            return
        if network is None or MQTTClient is None:
            self.log("[net] 缺少 network/umqtt 模块, 跳过")
            return

        try:
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
            self._init_topics()
        except Exception as e:
            self.log("[net] 初始化失败:", e)
            return

        while True:
            try:
                if not self.wlan.isconnected():
                    self._close_mqtt()
                    await self._connect_wifi()
                if self.wlan.isconnected():
                    if self.client is None:
                        self._connect_mqtt()
                    if self.client is not None:
                        self._publish_state()
            except Exception as e:
                self.log("[net] 网络异常:", e)
                self._close_mqtt()
            await asyncio.sleep_ms(cfg.MQTT_PUBLISH_MS)

    def _init_topics(self):
        mac = ubinascii.hexlify(self.wlan.config("mac")).decode()
        self.device_id = mac
        base = cfg.MQTT_BASE + "/" + mac
        self.state_topic = base + "/state"
        self.avail_topic = base + "/availability"

    async def _connect_wifi(self):
        self.log("[net] 连接 Wi-Fi:", cfg.WIFI_SSID)
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        self.wlan.connect(cfg.WIFI_SSID, cfg.WIFI_PASSWORD)
        t0 = ticks_ms()
        while not self.wlan.isconnected():
            if ticks_diff(ticks_ms(), t0) >= 15000:
                self.log("[net] Wi-Fi 连接超时")
                return
            await asyncio.sleep_ms(250)
        self.log("[net] Wi-Fi 已连接:", self.wlan.ifconfig()[0])

    def _connect_mqtt(self):
        self.log("[net] 连接 MQTT:", cfg.MQTT_HOST)
        try:
            import socket
            socket.settimeout(5)        # 避免 broker 不可达时长时间卡住
        except Exception:
            pass
        client = MQTTClient(
            "esp32aircheck_" + self.device_id,
            cfg.MQTT_HOST,
            port=cfg.MQTT_PORT,
            user=cfg.MQTT_USER or None,
            password=cfg.MQTT_PASSWORD or None,
            keepalive=cfg.MQTT_KEEPALIVE,
        )
        client.set_last_will(self.avail_topic, b"offline", retain=True, qos=0)
        client.connect()
        self.client = client
        self.log("[net] MQTT 已连接")
        client.publish(self.avail_topic, b"online", retain=True)
        if cfg.HA_DISCOVERY:
            for topic, payload in build_discovery(
                    self.device_id, cfg.DEVICE_NAME, self.version,
                    self.state_topic, self.avail_topic,
                    cfg.HA_DISCOVERY_PREFIX):
                client.publish(topic, payload.encode(), retain=True)
            self.log("[net] HA discovery 已发布")
        self._publish_state()

    def _publish_state(self):
        if self.client is None:
            return
        payload = json.dumps(build_state(self.snapshot())).encode()
        self.client.publish(self.state_topic, payload, retain=True)

    def _close_mqtt(self):
        client = self.client
        self.client = None
        if client is None:
            return
        try:
            client.publish(self.avail_topic, b"offline", retain=True)
        except Exception:
            pass
        try:
            client.disconnect()
        except Exception:
            pass
