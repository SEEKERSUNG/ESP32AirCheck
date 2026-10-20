# -*- coding: utf-8 -*-
"""
VOC 算法回归测试 (在电脑上跑, 不需要开发板)
================================================================
验证 voc.py 的移植是否保持了 Sensirion 官方算法的三个关键特性:

  1. 前 45 次采样是 blackout, 指数恒为 0
  2. 恒定信号输入时, 指数会收敛到基线值 100 附近
  3. 浓度阶跃上升时, 指数先明显冲高, 再随着基线学习慢慢回落

另外验证基线持久化相关接口:
  4. export_state() / import_state() 恢复后, 后续轨迹与不中断完全一致
  5. mUptime 每个采样都累加 (不能停在 blackout 的 46 秒)
  6. 版本/采样间隔不匹配的状态会被拒绝

用法: python tools/host_tests/test_voc.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "src"))

import voc  # noqa: E402


def feed(algo, sraw, n):
    out = []
    for _ in range(n):
        out.append(algo.process(sraw))
    return out


def main():
    ok = True

    # ---- 1. blackout ----
    algo = voc.VocAlgorithm()
    first = feed(algo, 30000, 45)
    if any(first):
        print("FAIL: blackout 期间指数不是 0 ->", first[:5])
        ok = False
    else:
        print("PASS: 前 45 次采样为 blackout (指数 = 0)")

    # ---- 2. 收敛到 100 附近 ----
    algo = voc.VocAlgorithm()
    series = feed(algo, 30000, 4000)
    tail = series[-500:]
    avg = sum(tail) / len(tail)
    print("INFO: 恒定输入 30000, 4000 秒后指数均值 = %.1f (范围 %d~%d)"
          % (avg, min(tail), max(tail)))
    if not (80 <= avg <= 130):
        print("FAIL: 没有收敛到 100 附近")
        ok = False
    else:
        print("PASS: 收敛到基线 100 附近")

    # ---- 3. 阶跃响应 ----
    # 注意 SGP40 的原始值是"越大空气越好":
    # VOC 浓度升高 -> 传感层电阻下降 -> SRAW_VOC 变小 -> VOC 指数变大。
    # (官方的 mox 模型里分母带负号, 就是为了这个极性。)
    algo = voc.VocAlgorithm()
    feed(algo, 30000, 2000)                  # 先建立基线
    base = algo.process(30000)
    down = feed(algo, 29800, 30)             # 空气变差: 原始值下降
    peak = max(down)
    later = feed(algo, 29800, 3000)[-1]      # 停留 3000 秒, 基线会跟着漂移
    print("INFO: 基线 %d -> 原始值下降后峰值 %d -> 3000 秒后 %d"
          % (base, peak, later))
    if peak <= base + 20:
        print("FAIL: 空气变差时指数没有明显上升")
        ok = False
    elif later >= peak:
        print("FAIL: 指数没有随基线学习而回落")
        ok = False
    else:
        print("PASS: 原始值下降 -> 指数冲高 -> 基线适应后回落")

    # ---- 3b. 反向: 原始值上升(空气变好)指数应低于 100 ----
    algo = voc.VocAlgorithm()
    feed(algo, 30000, 2000)
    up = feed(algo, 30200, 60)[-1]
    print("INFO: 原始值上升到 30200 后指数 = %d" % up)
    if up >= 100:
        print("FAIL: 空气变好时指数没有下降")
        ok = False
    else:
        print("PASS: 原始值上升 -> 指数低于基线")

    # ---- 4. 一致性: 同样输入多次运行结果必须完全一致 ----
    a = feed(voc.VocAlgorithm(), 32768, 500)
    b = feed(voc.VocAlgorithm(), 32768, 500)
    if a != b:
        print("FAIL: 算法不确定(同样输入结果不同)")
        ok = False
    else:
        print("PASS: 算法确定性一致, 500 秒后指数 = %d" % a[-1])

    # ---- 5. 状态导出/导入: 恢复后继续运行应与不中断完全一致 ----
    a = voc.VocAlgorithm()
    feed(a, 30000, 2000)
    state = a.export_state()
    b = voc.VocAlgorithm()
    restored = b.import_state(json.loads(json.dumps(state)))
    tail_a = feed(a, 30000, 300)
    tail_b = feed(b, 30000, 300)
    if not restored:
        print("FAIL: 基线状态导入失败")
        ok = False
    elif tail_a != tail_b:
        print("FAIL: 恢复基线后算法轨迹与不中断不一致")
        ok = False
    else:
        print("PASS: 导出/导入基线后继续计算一致 (idx=%d)" % tail_a[-1])

    # ---- 6. mUptime 应持续累加, 不能停在 blackout 阶段 ----
    algo = voc.VocAlgorithm()
    feed(algo, 30000, 100)
    if abs(algo.mUptime - 100.0) > 0.01:
        print("FAIL: mUptime = %.1f, 期望 100" % algo.mUptime)
        ok = False
    else:
        print("PASS: mUptime 正常累加 (100 次采样后 = 100s)")

    # ---- 7. 版本/类型不匹配的状态必须拒绝 ----
    bad = dict(state)
    bad["version"] = 999
    if voc.VocAlgorithm().import_state(bad):
        print("FAIL: 版本不匹配的状态被接受了")
        ok = False
    else:
        print("PASS: 版本不匹配的状态被拒绝")

    bad = dict(state)
    bad["interval"] = 2.0
    if voc.VocAlgorithm().import_state(bad):
        print("FAIL: 采样间隔不匹配的状态被接受了")
        ok = False
    else:
        print("PASS: 采样间隔不匹配的状态被拒绝")

    print("\n结果:", "全部通过" if ok else "有失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
