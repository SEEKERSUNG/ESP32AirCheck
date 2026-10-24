# -*- coding: utf-8 -*-
"""
Home Assistant MQTT Discovery 配置回归测试 (PC 上运行, 不联网)
================================================================
验证 build_state() / build_discovery() 生成的主题和 JSON:
    python tools/host_tests/test_net.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "src"))

import net  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("PASS: " if cond else "FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    snap = {
        "values": {"temp": 25.4, "rh": 48.2, "hcho_mg": 0.04, "tvoc": 123},
        "alarm": {"sht40": False, "ze08": False, "sgp40": True},
        "uptime": 1234.5,
    }
    state = net.build_state(snap)
    check(state["temp"] == 25.4, "state temp")
    check(state["rh"] == 48.2, "state rh")
    check(state["hcho"] == 0.04, "state hcho")
    check(state["voc"] == 123, "state voc")
    check(state["alarm"] is True, "state alarm")
    check(state["uptime"] == 1234, "state uptime")

    empty = net.build_state({
        "values": {"temp": None, "rh": None, "hcho_mg": None, "tvoc": None},
        "alarm": {"sht40": False, "ze08": False, "sgp40": False},
        "uptime": 0,
    })
    check(empty["temp"] is None and empty["alarm"] is False,
          "无数据时允许 null/false")

    state_topic = "esp32aircheck/aabbccddeeff/state"
    avail_topic = "esp32aircheck/aabbccddeeff/availability"
    configs = net.build_discovery("aabbccddeeff", "ESP32 AirCheck", "1.0.0",
                                  state_topic, avail_topic)
    check(len(configs) == 5, "生成 5 个 Discovery 配置")
    topics = {}
    for topic, payload in configs:
        try:
            topics[topic] = json.loads(payload)
        except Exception as e:
            check(False, "JSON 解析 %s: %s" % (topic, e))
    check("homeassistant/sensor/esp32aircheck_aabbccddeeff_temperature/config"
          in topics, "温度 discovery topic")
    check("homeassistant/sensor/esp32aircheck_aabbccddeeff_humidity/config"
          in topics, "湿度 discovery topic")
    check("homeassistant/sensor/esp32aircheck_aabbccddeeff_hcho/config"
          in topics, "甲醛 discovery topic")
    check("homeassistant/sensor/esp32aircheck_aabbccddeeff_voc/config"
          in topics, "VOC discovery topic")
    check("homeassistant/binary_sensor/esp32aircheck_aabbccddeeff_alarm/config"
          in topics, "报警 binary_sensor topic")

    t = topics.get("homeassistant/sensor/esp32aircheck_aabbccddeeff_temperature/config", {})
    check(t.get("unit_of_measurement") == "°C", "温度单位")
    check(t.get("device_class") == "temperature", "温度 device_class")
    check(t.get("state_topic") == state_topic, "温度 state_topic")
    check(t.get("availability_topic") == avail_topic, "温度 availability_topic")
    check(t.get("device", {}).get("identifiers") ==
          ["esp32aircheck_aabbccddeeff"], "设备 identifiers")
    check("value_json.temp" in t.get("value_template", ""), "温度模板")

    h = topics.get("homeassistant/sensor/esp32aircheck_aabbccddeeff_hcho/config", {})
    check(h.get("unit_of_measurement") == "mg/m³", "甲醛单位")
    v = topics.get("homeassistant/sensor/esp32aircheck_aabbccddeeff_voc/config", {})
    check(v.get("unit_of_measurement") == "idx", "VOC 单位")
    a = topics.get("homeassistant/binary_sensor/esp32aircheck_aabbccddeeff_alarm/config", {})
    check(a.get("payload_on") == "ON" and a.get("payload_off") == "OFF",
          "报警 ON/OFF")
    check(a.get("device_class") == "problem", "报警 device_class")

    print()
    if FAILS:
        print("结果: %d 项失败" % len(FAILS))
        return 1
    print("结果: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
