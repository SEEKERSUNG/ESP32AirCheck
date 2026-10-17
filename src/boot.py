# -*- coding: utf-8 -*-
"""
开机引导 —— MicroPython 上电时先执行 boot.py, 再执行 main.py。
================================================================
这里只做最基础的准备, 真正的业务都在 main.py 里。
"""

import gc

# 关掉 ESP-IDF 底层的调试输出(比如 WiFi 驱动的日志),
# 免得和 print() 的内容混在一起看不清。
try:
    import esp
    esp.osdebug(None)
except Exception:
    pass

gc.collect()
print("boot.py 就绪, 开始执行 main.py")
