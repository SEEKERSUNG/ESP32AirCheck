# -*- coding: utf-8 -*-
"""
VOC 基线持久化回归测试 (跑在开发板上)
    python tools/deploy.py run test_voc_state

覆盖:
  1. 状态能写入 NVS / 文件并原样读回
  2. 读回后导入新算法, 继续计算的轨迹与不中断完全一致
  3. 删除后读不到状态

用独立的 NVS 命名空间和测试文件, 不会覆盖主程序的基线。
"""

import json

import sensors
import voc

NS = "airchecktest"
KEY = "vocstate"
TEST_FILE = "/voc_state_test.json"

FAILS = []


def check(cond, msg):
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        FAILS.append(msg)


# 文件兜底路径也换成测试文件, 避免碰到主程序的状态
sensors._VOC_STATE_FILE = TEST_FILE

print("存储后端:", "ESP32 NVS" if sensors.esp32 is not None else "文件")

# ---------------------------------------------------------------- 准备
sensors._erase_state(NS, KEY)
check(sensors._load_state_blob(NS, KEY) is None, "初始状态为空")

a = voc.VocAlgorithm()
for i in range(1500):
    a.process(30000 + (i % 9))
check(abs(a.mUptime - 1500.0) < 0.01,
      "算法累计时间 = %.0fs" % a.mUptime)

state = a.export_state()
blob = json.dumps(state).encode()

# ---------------------------------------------------------------- 保存/读取
check(sensors._save_state_blob(blob, NS, KEY), "保存成功")
back = sensors._load_state_blob(NS, KEY)
check(back == blob, "读回的数据与写入一致 (%d 字节)" % len(blob))

# ---------------------------------------------------------------- 恢复
b = voc.VocAlgorithm()
check(b.import_state(json.loads(back.decode())), "导入成功")
check(abs(b.mUptime - a.mUptime) < 0.01, "恢复后的累计时间一致")

x = [a.process(30000) for _ in range(100)]
y = [b.process(30000) for _ in range(100)]
check(x == y, "恢复后继续算 100 次, 结果完全一致 (idx=%d)" % x[-1])

# ---------------------------------------------------------------- 清理
sensors._erase_state(NS, KEY)
check(sensors._load_state_blob(NS, KEY) is None, "删除后状态为空")

print()
if FAILS:
    print("结果: %d 项失败" % len(FAILS))
    for f in FAILS:
        print("  -", f)
else:
    print("结果: 全部通过")
