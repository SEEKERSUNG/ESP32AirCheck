# ESP32 空气质量检测仪 (ESP32AirCheck)

基于 **ESP32 经典款** + **MicroPython** 的桌面级空气质量监测仪。
一块 1.2 寸 OLED 上同时显示 **温湿度 / 甲醛 / VOC 指数**，
支持**上电自动识别传感器**、**预热状态提示**、**运行中热插拔**。

---

## 目录

1. [功能一览](#1-功能一览)
2. [硬件清单](#2-硬件清单)
3. [接线表（GPIO 引脚编号）](#3-接线表gpio-引脚编号)
4. [目录结构](#4-目录结构)
5. [部署与运行](#5-部署与运行)
6. [屏幕说明](#6-屏幕说明)
7. [传感器说明](#7-传感器说明)
8. [报警阈值](#8-报警阈值)
9. [常见问题](#9-常见问题)
10. [开发笔记](#10-开发笔记)
11. [接入 Home Assistant (MQTT)](#11-接入-home-assistant-mqtt)

---

## 1. 功能一览

| 功能 | 说明 |
|------|------|
| 三路环境测量 | SHT40 温湿度、ZE08-CH2O 甲醛、SGP40 VOC 指数 |
| 上电识别 | 开机直接进主界面；没接上的那行显示 `----`，具体状态看第 2 页 |
| 预热标记 | 传感器在线但读数还不可信时，第 2 页显示 `WARM`，且不参与警告/报警 |
| 热插拔 | 没接上的传感器每 10 秒自动重找；在线传感器掉线后第 2 页自动变回 `N/A` |
| 单通道开关 | 某个传感器暂时没有时，置 `*_ENABLED = False`，该行隐藏，第 2 页显示 `OFF` |
| 非阻塞 | 全部任务跑在 `asyncio` 里，采集不会卡住屏幕刷新 |
| 基线持久化 | VOC 基线每 10 分钟存入 ESP32 NVS，断电重启后继续学习 |
| 三页界面 | 主界面 / 运行信息 / 原始数据，按键切换 |
| 自动息屏 | 10 秒无操作关屏，按 BOOT 键唤醒（唤醒那次不翻页） |
| 长按重置基线 | 长按 BOOT 3 秒清除 VOC 学习状态，以当前环境重新学习 |
| Home Assistant | 可选 Wi-Fi + MQTT Discovery，自动生成温度/湿度/甲醛/VOC/报警实体 |
| 中文字库 | 16×16 点阵，由 `tools/gen_font.py` 从系统字体自动生成 |
| 警告/报警 | 警告显示 `!`；报警显示闪烁 `*` + 板载 LED 快闪 +（可选）蜂鸣器鸣叫 |

---

## 2. 硬件清单

| 器件 | 型号 | 接口 | 备注 |
|------|------|------|------|
| 主控 | ESP32 经典款（ESP32-WROOM-32 / DevKitC） | — | 已刷 MicroPython v1.29.0 |
| 显示屏 | 1.2 寸 OLED，128×64 | I2C @ 0x3C | SSD1306 或 SH1106，见[常见问题](#9-常见问题) |
| 温湿度 | SHT40 | I2C @ 0x44 | 3.3V |
| 甲醛 | ZE08-CH2O（炜盛） | UART 9600 8N1 | **需要 5V 供电**，信号是 3V TTL |
| VOC 指数 | SGP40 | I2C @ 0x59 | 3.3V，需配合 VOC 算法才能得到指数 |
| 按键 | 板上自带 **BOOT** 键（可选） | GPIO0 → GND | 内部上拉，短按翻页/唤醒，长按 3 秒重置 VOC 基线 |
| 指示灯 | 板载蓝色 LED（可选） | GPIO2 | 高电平点亮 |
| 蜂鸣器 | 有源蜂鸣器（可选） | GPIO25 | 默认关闭 |

---

## 3. 接线表（GPIO 引脚编号）

### 3.1 电源

| 电源轨 | 接到 | 说明 |
|--------|------|------|
| **3V3** | OLED、SHT40、SGP40 | 这几个模块都是 3.3V 器件，**不要接 5V** |
| **VIN / 5V** | ZE08-CH2O | 甲醛模块要求 3.7~5.5V，接 5V 最稳 |
| **GND** | 所有模块的 GND | **必须共地**，否则 I2C / UART 都不通 |

### 3.2 I2C 总线（三个模块并联）

OLED、SHT40、SGP40 的地址各不相同（0x3C / 0x44 / 0x59），
所以可以直接并到同一组 SDA/SCL 上，不需要任何地址冲突处理。

| ESP32 引脚 | 接到 | 说明 |
|-----------|------|------|
| **GPIO32** | 所有 I2C 模块的 **SDA** | I2C 数据线 |
| **GPIO33** | 所有 I2C 模块的 **SCL** | I2C 时钟线，总线频率 400 kHz |

> 接线用**并联**：模块 A 的 SDA↔模块 B 的 SDA↔GPIO32，SCL 同理。
> 长线（>20cm）建议在 SDA、SCL 各加一个 4.7kΩ 上拉到 3V3；短线一般不用。
>
> **本项目的板子（定制板）没有引出 GPIO21**，所以 I2C 不用 ESP32 最常见的 21/22，
> 而改用 GPIO32/33。这两个脚在排针上是有的，也不与其它外设冲突。

### 3.3 ZE08-CH2O 甲醛模块（UART2）

跨接线，注意 TX 接 RX：

| ESP32 引脚 | 方向 | 接到 ZE08 | 说明 |
|-----------|------|-----------|------|
| **GPIO17** | 输出 → | **RXD** | ESP32 的 TX，发命令给模块 |
| **GPIO16** | 输入 ← | **TXD** | ESP32 的 RX，收模块的数据 |
| **VIN（5V）** | 电源 → | **VCC** | 供电 |
| **GND** | 电源 → | **GND** | 共地 |

波特率固定 **9600，8 数据位，无校验，1 停止位**（出厂默认）。
模块的 UART 是 **3V TTL** 电平，可以直接连 ESP32，不用电平转换。

### 3.4 指示灯 / 按键 / 蜂鸣器

| 功能 | ESP32 引脚 | 接法 | 默认 |
|------|-----------|------|------|
| 状态指示灯 | **GPIO2** | 板载 LED，高电平点亮 | 开启（`STATUS_LED = 2`） |
| 翻页/唤醒/长按重置按键 | **GPIO0** | 板上自带的 BOOT 键（一端 GPIO0，一端 GND），内部上拉 | 开启 |
| 蜂鸣器 | **GPIO25** | 有源蜂鸣器正极接 GPIO25，负极接 GND | **关闭**（`BUZZER_ENABLED = False`） |

### 3.5 接线总览（ASCII 图）

```
                  ESP32 DevKitC
                ┌────────────────┐
                │                │
   OLED  SDA ───┤ GPIO32         │
   OLED  SCL ───┤ GPIO33         │
                │                │
   ZE08  RXD ←──┤ GPIO17 (TX)    │
   ZE08  TXD ──→┤ GPIO16 (RX)    │
                │                │
   板载 LED     │ GPIO2          │
   按键 → GND ──┤ GPIO0          │
   蜂鸣器     ──┤ GPIO25         │
                │                │
        3V3 ────┤ 3V3  → OLED/SHT40/SGP40
        VIN/5V ─┤ VIN  → ZE08
        GND ────┤ GND  → 所有模块
                └────────────────┘
```

### 3.6 为什么选这些引脚

* **GPIO32 / GPIO33** 用作 I2C。ESP32 上最常用的 I2C 默认引脚其实是 GPIO21/22，
  但**本项目的定制板没有引出 GPIO21**，所以改用 32/33 —— 这两个脚在排针上有，
  而且和本项目其它外设（GPIO0/2/16/17/25）完全不冲突。
  它们既不是 strapping 脚，也不是只输入脚，做 I2C 很合适（32 还是 ESP32 内部
  ADC1_CH4，将来要接模拟量传感器也方便）。
* **GPIO16 / GPIO17** 是 UART2 的默认引脚，正好留给 ZE08。
  （注意：如果是带 PSRAM 的 **ESP32-WROVER** 模组，GPIO16/17 被 PSRAM 占用，
  需要改 `config.py` 里的 `ZE08_TX` / `ZE08_RX`，例如换成 25/26。）
* **避开了 GPIO6~GPIO11**：这几个脚连着板载 SPI Flash，一旦驱动会直接死机。
* **避开了 GPIO34~GPIO39**：这几个脚只能做输入，不能做输出。
* **GPIO12 / GPIO15 不用**：它们是 strapping 脚，上电瞬间的电平会决定启动模式，
  接外设容易导致偶尔启动失败。
* **GPIO0 用作按键**：它也是 strapping 脚（决定是否进下载模式），但板上自带的
  **BOOT** 键本来就接在 GPIO0 上，配内部上拉当输入完全没问题。
  唯一要注意的是：**上电/复位那一刻不要按着它**，否则芯片会停在下载模式、不跑程序。
  GPIO2 同样与启动模式相关，但只是下载模式，驱动一颗 LED 是安全的
  （官方 DevKitC 的板载 LED 就是 GPIO2）。

> 想改引脚只需要改 `src/config.py` 里的 `I2C_SDA` / `I2C_SCL` / `ZE08_TX` / `ZE08_RX` 等常量，
> 其它代码不用动。

### 3.7 本板可用引脚对照

本项目的定制板排针共引出以下引脚：

```
GPIO:  0  2  4  5 12 13 14 15 16 17 18 19 22 23 25 26 27 32 33 34 35
                                                        ↑  ↑
                                                     SDA SCL
```

（**没有引出 GPIO21**；GPIO6~11 是 SPI Flash 保留脚，GPIO1/3 是 UART0 串口引脚，都没有引出。）

| 用途 | 引脚 | 在板上 |
|------|------|--------|
| I2C SDA | GPIO32 | ✅ |
| I2C SCL | GPIO33 | ✅ |
| ZE08 TX | GPIO17 | ✅ |
| ZE08 RX | GPIO16 | ✅ |
| 状态灯 | GPIO2 | ✅ |
| 按键 | GPIO0 | ✅（板载 BOOT 键） |
| 蜂鸣器（可选） | GPIO25 | ✅ |

余下空闲：`GPIO4 / 5 / 12 / 13 / 14 / 15 / 18 / 19 / 22 / 23 / 26 / 27 / 34 / 35`
（其中 12、15 是 strapping 脚，上电电平会影响启动模式；
34、35 只能做输入。要用的话注意一下。）

---

## 4. 目录结构

```
ESP32AirCheck/
├── README.md                 本文档
├── src/                      部署到开发板的代码
│   ├── boot.py               开机引导(执行 main.py 之前)
│   ├── main.py               主程序: asyncio 任务调度
│   ├── config.py             ★ 所有可配置项都在这里(引脚/地址/阈值/预热时间)
│   ├── secrets.py            本地 Wi-Fi/MQTT 凭据(被 .gitignore 忽略)
│   ├── sensors.py            传感器状态机: 探测 / 预热 / 采集 / 热插拔
│   ├── ui.py                 屏幕渲染(三页界面 + 中英文混排)
│   ├── cfont.py              16×16 中文字库(自动生成)
│   ├── voc.py                Sensirion VOC 指数算法(C 代码的 Python 移植)
│   ├── net.py                Wi-Fi / MQTT / Home Assistant Discovery
│   ├── sht40.py              SHT40 驱动
│   ├── sgp40.py              SGP40 驱动
│   ├── ze08.py               ZE08-CH2O 驱动
│   ├── ssd1306.py            SSD1306 OLED 驱动
│   └── sh1106.py             SH1106 OLED 驱动
└── tools/                    在电脑上运行的工具
    ├── deploy.py             部署 / 运行测试 / 打开终端
    ├── gen_font.py           生成中文字库
    ├── layout_preview.py     把界面排版渲染成 PNG(不用接屏幕也能看)
    ├── backup/               上传前的板上文件备份
    ├── host_tests/
    │   ├── test_voc.py       VOC 算法回归测试(纯 PC 上跑)
    │   ├── test_net.py       Home Assistant Discovery JSON/主题测试(不联网)
    │   ├── run_board_test.py 在 PC 上跑开发板测试(带 machine/framebuf 垫片)
    │   └── mpstub/           MicroPython 模块的最小替身
    └── tests/                在开发板上跑的硬件测试
        ├── test_i2c.py       I2C 总线体检(引脚/接反/频率/电平/点名)
        ├── test_ui.py        界面排版测试(用假屏幕)
        ├── test_oled.py      交替显示 SSD1306 / SH1106 便于判断型号
        ├── test_sht40.py     SHT40 单测
        ├── test_sgp40.py     SGP40 单测
        ├── test_ze08.py      ZE08 单测(真串口)
        ├── test_ze08_parse.py ZE08 帧解析回归测试(假串口, 不需要接线)
        ├── test_voc_state.py VOC 基线持久化回归测试(独立 NVS, 不影响主状态)
        └── test_state_machine.py 状态机/报警/总线恢复/息屏/ZE08问答/长按重置回归测试
```

---

## 5. 部署与运行

### 5.1 准备

```powershell
# 只需要 mpremote；生成字库时需要 Pillow
python -m pip install mpremote
python -m pip install Pillow          # 仅改界面文字时才需要
```

默认串口是 **COM9**，可以用 `--port` 指定别的。

### 5.2 上传代码

```powershell
cd ESP32AirCheck
python tools/deploy.py                # 上传 src/ 里所有 .py 到开发板
python tools/deploy.py --backup       # 上传前先把板上同名文件备份到 tools/backup/
```

上传完成后开发板会自动软复位，直接开始跑 `main.py`，屏幕显示开机画面。

> **用充电宝独立运行**：上传完成后不需要电脑或串口，充电宝给 USB 口（或 VIN/GND）
> 供电就会自动启动 `main.py`。注意 ZE08 接的是 VIN/5V，整机电流约 100~200mA，
> 选一个不会因小电流自动断电的充电宝；上电/复位时不要按住 GPIO0 上的 BOOT 键，
> 否则芯片会停在下载模式。如果 USB 供电时偶尔进不了主程序（CH340 的 DTR/RTS
> 会影响 GPIO0/EN），改用 VIN/GND 的 5V 供电即可。

### 5.3 调试

```powershell
python tools/deploy.py ls             # 列出开发板上的文件
python tools/deploy.py repl           # 打开交互式串口终端 (Ctrl-] 退出)
python tools/deploy.py reset          # 软复位
python tools/deploy.py exec "print(__import__('gc').mem_free())"
```

### 5.4 跑测试

```powershell
# 在开发板上运行 tools/tests/ 里的某个脚本
python tools/deploy.py run test_i2c       # I2C 体检：接错/接反/频率/上拉 一次排掉
python tools/deploy.py run test_oled      # 交替显示两种 OLED 驱动，判断屏幕型号
python tools/deploy.py run test_sht40
python tools/deploy.py run test_sgp40
python tools/deploy.py run test_ze08
python tools/deploy.py run test_ze08_parse  # ZE08 解析回归测试(不用接传感器)
python tools/deploy.py run test_voc_state    # VOC 基线保存/恢复回归测试
python tools/deploy.py run test_state_machine  # 状态机/报警/总线恢复/息屏/ZE08问答/长按重置回归测试

# 界面排版测试：自动跑测试并把画面渲染成 PNG，不用接屏幕
python tools/layout_preview.py            # 生成 tools/ui_layout.png

# 纯 PC 端测试
python tools/host_tests/test_voc.py       # VOC 算法/基线状态回归测试
python tools/host_tests/test_net.py       # Home Assistant Discovery JSON/主题测试
python tools/host_tests/run_board_test.py tools/tests/test_state_machine.py
                                          # 同一份状态机测试, 不插板子也能跑
```

> `run_board_test.py` 只是给 `time` / `machine` / `framebuf` 套了一层替身，
> 电脑上没有 MicroPython 的 8×8 字库，所以**需要看真实排版的 `test_ui.py`
> 它直接拒绝执行**，那类测试还是得上板跑。

> 板上测试会接管串口并停掉主程序；测完执行 `python tools/deploy.py reset`
> 回到正常运行（用充电宝独立供电前记得复位一次）。

### 5.5 修改界面文字后需要重新生成字库

`src/cfont.py` 是按 `tools/gen_font.py` 顶部的 `CHARS` 列表生成的点阵：
**界面里出现的每一个汉字都必须在那个列表里**，否则会画成一个空心方框占位。
列表是手工维护的，多几个字不影响运行（只多占一点空间），少一个字就会缺字。

改了界面文案后需要重新生成字库（依赖 Windows 自带字体 `msyh.ttc`）：

```powershell
python tools/gen_font.py             # 生成 src/cfont.py
python tools/gen_font.py --preview   # 顺便输出预览图，肉眼确认没有缺字
```

如果没有 `cfont.py`，程序会自动退回到**英文界面**，不会报错。

---

## 6. 屏幕说明

屏幕 128×64，汉字 16×16，英文 8×8。共三页，按 **GPIO0 上的按键**（板上自带的 BOOT 键）循环切换。

超过 `SCREEN_OFF_MS`（默认 10 秒）没有按键操作会自动关屏；按一次 BOOT 键
只唤醒、不翻页，再按才切换页面。关屏只是关闭显示，采集、报警、LED 和蜂鸣器
都继续工作；把 `SCREEN_OFF_MS` 设为 0 可以关闭这个功能。如果关掉了按键
（`BUTTON_ENABLED = False`），息屏也会自动不生效，避免屏幕黑了无法唤醒。

**长按 BOOT 键 3 秒**会清除 VOC 算法状态和已保存的基线，让 SGP40 以当前空气
重新学习（`VOC_RESET_HOLD_MS` 可调，设为 0 关闭）。重置后 TVOC 先显示 `----`，
第 2 页 SGP40 显示 `WARM`，约 45 秒 blackout + 60 秒预热后重新出数。短按仍然
只翻页/唤醒，不会误触发重置。

### 第 1 页：主界面

```
┌────────────────────────────────┐
│ 空气检测                  1/3  │  ← 标题 · 页码
│ TEMP    25.4 C                 │  ← SHT40 温度
│ HUM     48.2 %                 │  ← SHT40 湿度
│ HCHO    0.04mg/m3              │  ← ZE08 甲醛
│ VOC      123 idx               │  ← SGP40 VOC 指数
└────────────────────────────────┘
```

主界面**只显示读数**，不显示连接/预热/异常状态。传感器是否在线、是不是
还在预热、串口有没有坏帧，统一看第 2 页和第 3 页，避免状态提示占满本来就
小的屏幕。

* 标签左对齐，数值从固定列（第 40 像素）开始，四行行距 12 像素。
* 读不到数据时显示 `----`；具体原因（`N/A` / `WARM` / `ERR` / `SCAN`）看第 2 页。
* **警告/报警**：超过警告阈值时数值右侧常亮 `!`；超过报警阈值时闪烁 `*`
  （同时板载 LED 快闪、可选蜂鸣器鸣叫）。`!` / `*` 是这一页仅有的数据标记，
  连接/预热状态不会出现在这一页。
* HCHO 只显示 2 位小数：ZE08 分辨率约 10 ppb（≈0.0125 mg/m³），
  第 3 位小数没有实际意义。
* `idx` 是 VOC 相对指数，不是浓度单位，原因见 [7.7](#77-为什么-tvoc-的单位是-idx-而不是-mgm³)。
* **关掉的传感器整行不画**：把 `config.py` 里对应的 `*_ENABLED` 改成 `False`
  （例如手头还没有 ZE08），那一行数据会一起消失，剩下的行自动往上收；
  第 2 页对应显示 `OFF`。适合"先只调一个传感器"的场景。

### 第 2 页：运行信息

```
┌────────────────────────────────┐
│ 运行 0:12:34              2/3  │
│ SHT40 0x44                OK   │
│ SGP40 0x59                OK   │
│ ZE08  UART               ERR   │
│ I2C 3C 44 59                   │
└────────────────────────────────┘
```

`OK` 正常 / `WARM` 预热中 / `N/A` 未连接 / `ERR` 异常 / `SCAN` 自检中 / `OFF` 已关闭。
**传感器状态以这一页为准**——主界面只负责显示读数。
最后一行是 I2C 总线上扫描到的地址，排查接线时很有用。

### 第 3 页：原始数据

```
┌────────────────────────────────┐
│          原始数据          3/3 │
│ VOCraw 32145                   │
│ VOCidx   123                   │
│ SGP40 lrn   1                  │
│ ZE08  rx  121                  │
│ ZE08  bad   3                  │
│ I2C 3C 44 59                   │
└────────────────────────────────┘
```

用来单独调传感器：`VOCraw` 是 SGP40 的原始信号，`lrn` 是算法就绪标志，
`ZE08 rx/bad` 是串口收到的合法/非法帧计数。`bad` 一直涨通常说明波特率不对或线太长。

### 开机流程

上电后显示约 1.2 秒开机画面（型号 + 版本号），然后**直接进入主界面**，
不再单独显示自检页：

* 已接上的传感器立刻开始预热/读数
* 没接上的传感器，主界面对应行显示 `----`，第 2 页状态是 `N/A`
* 在线但读不到数据的也是 `----`，第 2 页状态是 `ERR`
* 预热中第 2 页显示 `WARM`，自检中显示 `SCAN`

主界面专心看数；想知道哪个没问题、哪个缺席、哪个还在预热，按一下键翻到第 2 页。

---

## 7. 传感器说明

### 7.1 地址与接口

| 传感器 | 接口 | 地址 | 采样周期 |
|--------|------|------|----------|
| SHT40 | I2C | `0x44` | 1 秒 |
| SGP40 | I2C | `0x59` | 1 秒（算法要求） |
| ZE08-CH2O | UART 9600 | — | 100ms 轮询收包，数据本身 1 秒一帧 |

两个 I2C 传感器共用一个 1 秒的主轮询周期（SGP40 的 VOC 算法必须严格按 1 秒喂数据）。
ZE08 单独用 100ms 的任务刷串口缓冲，防止接收缓冲溢出丢帧。

### 7.2 预热时间（以及为什么）

| 传感器 | 预热 | 依据 |
|--------|------|------|
| SHT40 | 1 秒 | 上电后第一次测量即可用，几乎不需要预热 |
| SGP40 | 60 秒 | 算法前 45 秒是 blackout 期；均值/方差时间常数约 12 小时，之后才完全收敛 |
| ZE08 | 180 秒 | 数据手册标明预热时间 ≤ 3 分钟；首次上电还需 24~48 小时充分稳定 |

**预热期间第 2 页状态显示 `WARM`，并且不参与警告/报警判定** ——
避免刚上电就乱报警。预热还剩多久目前不在屏幕上显示（主界面不放状态，
第 2 页只看状态），但预热时长到了会自动变成 `OK`。

> 主界面在预热期间仍会显示读数，但这些读数不可信；判断是否预热完成请看第 2 页。
> ZE08 的 3 分钟只是上电预热，数据手册另要求首次使用先连续通电 24~48 小时。

SGP40 额外有一条：即使过了 60 秒可以读数，VOC 指数在 1 小时内仍在学习基线
（国际通用做法）。这期间第 3 页的 `SGP40 lrn` 会显示 `1`。
`VOC_LEARN_S = 3600`，在 `config.py` 里可调。

### 7.3 上电检测与热插拔

* **I2C 传感器**：先扫地址，再做一次**功能探测**（读序列号 / 读一帧数据并校验 CRC），
  两者都通过才算“已连接”。只看地址容易被总线干扰误判。
* **ZE08**：监听 UART，收到一帧校验正确的数据就算已连接。
  为了加快检测，如果 500ms 内没等到主动上传帧，会每 500ms 发一次问答读取命令；
  2.5 秒内仍然没有合法帧就判定为未连接。
* **运行中**：没接上的传感器每 `RESCAN_MS = 10000`（10 秒）重找一次；
  已在线传感器连续 3 次 I2C 读失败会被标记为掉线。

所以**分批采购、单独调试都很方便**：插上就能用，拔掉后第 2 页状态自动变成 `N/A`。

### 7.4 总线卡死自恢复

在**通电状态下**插拔 I2C 设备时，从机有可能卡在半截传输里、一直把 SDA 拉低不放。
这时候主机怎么扫都扫不到东西，而且**光重新扫描是救不回来的**——实测就是这样：
OLED 明明插好了，扫描却一直是空，必须断电重启才行。

所以 `sensors.py` 里加了一段恢复逻辑：

1. `scan_bus()` 发现**上一轮还扫得到设备、这一轮整条总线却全空**，就判定总线卡死；
2. 丢掉所有 I2C 驱动实例；
3. `i2c_unstick()`：把 SDA/SCL 切成普通开漏 GPIO，手动打 9 个时钟把从机顶开，
   再补一个 STOP 条件，让总线回到空闲；
4. 重新创建 I2C 外设，然后重扫一遍。

于是热插拔之后**不用断电重启**，最多等一轮 10 秒就会自愈。

> 注意：只有在"之前扫到过、现在全空"这个**边沿**上才会触发。恢复成功且总线回到
> 空闲后就不再重复重建；如果恢复失败（总线仍被拉低），下一轮扫描会继续尝试，
> 不会把总线永久放弃。一个传感器都没接、总线本身空闲时不会反复重建。

### 7.5 ZE08 的单位换算

模块有两种工作模式，本驱动**两种帧都能解析**，所以不管模块出厂是什么模式都能用：

* 主动上传帧：`FF 17 04 00 <浓度高> <浓度低> <量程高> <量程低> <校验>`，浓度单位 ppb
* 问答应答帧：`FF 86 <ug/m3 高> <ug/m3 低> 00 00 <ppb 高> <ppb 低> <校验>`

校验值 = `(~(Byte1+…+Byte7) + 1) & 0xFF`。
换算：**1 ppb 甲醛 = 0.00125 mg/m³**。模块手册取 1 ppm ≈ 1.25 mg/m³
（约 20~25°C）；严格理想气体 25°C 约 1.228 mg/m³，差约 1.8%，代码按手册的
1.25 计算。

主动上传模式下模块每秒自动发帧；问答模式下不会主动发，驱动会每 900ms
发一次 `FF 01 86 00 00 00 00 00 79` 读取命令。

### 7.6 SGP40 的原始值方向（容易搞反）

SGP40 输出的原始量 **`SRAW_VOC` 越大，表示空气越干净**；
也就是说 **VOC 越高 → SRAW_VOC 越小 → VOC 指数越大**。
和 Sensirion 官方 mox 模型一致，网上不少移植代码把方向搞反了。
本项目已按官方算法（`sensirion_gas_index_algorithm/sensirion_gas_index_algorithm.c`，
v3.2.0）比对验证。
`tools/host_tests/test_voc.py` 里有针对这一点的回归测试。

### 7.7 为什么 VOC 指数没有 mg/m³ 单位

因为 **SGP40 测不出绝对浓度**，它只是一个相对变化的金属氧化物传感器：

* 传感器输出的是原始电阻信号 `SRAW_VOC`（0~65535），没有浓度单位；
* Sensirion 的 VOC 算法会持续学习当前环境的基线，把基线定义为 **100**，
  输出 0~500 的 **VOC 指数（VOC Index）**。指数只表示"相对基线变好/变差
  了多少"，算法本身已经扣掉了绝对偏移；
* 不同气体的灵敏度差异很大，温湿度也会影响读数。想把 idx 换算成
  mg/m³，必须用标准气体（例如乙醇）做标定，而且只能代表那种气体，
  不能当成总 TVOC 浓度。因此**没有官方换算公式**，硬换出来的数字
  没有意义，也不能拿来对照 GB/T 18883 的浓度限值。

如果确实需要 mg/m³ 级别的绝对浓度，要换传感器：

| 方案 | 输出 | 说明 |
|------|------|------|
| SGP30 | TVOC **ppb** | 仍是估计值；ppb 换算 mg/m³ 还需假定分子量 |
| CCS811 | eTVOC **ppb** | 需要较长 burn-in / 基线保存 |
| PID 光离子传感器 | TVOC **ppm** | 相对接近真实总量，价格高、需异丁烯标定 |
| 实验室 GC/MS | 各组分量 | 真值，但不适合桌面设备 |

所以本项目保留 `idx`，阈值也用指数（见[第 8 节](#8-报警阈值)）。
主界面标签 `VOC` 的实际含义是 **VOC 指数**，不是总挥发性有机物浓度；
`config.py` 里的 `TVOC_WARN/ALARM` 是沿用旧命名，含义同样是 VOC 指数。

### 7.8 断电后保留 VOC 基线

VOC 算法的基线（均值、方差、低通、gating）跑在 **ESP32** 上，SGP40 自己
**不保存任何基线**（SGP30 才有 `get_baseline` / `set_baseline`，SGP40 没有）。
所以跨断电保存写的是 **ESP32 的 NVS**，跟传感器内部的 flash 无关。

工作方式：

* 每 `VOC_STATE_SAVE_MS`（默认 10 分钟）把 `VocAlgorithm.export_state()` 的 JSON
  写入 NVS（命名空间 `aircheck`，键 `vocstate`，约 0.5KB）；
* SGP40 连接（含热插拔重连）时自动 `import_state()` 恢复；
* 掉线/拔掉 SGP40 前会先补存一次（距上次保存至少 60 秒，避免接触不良时高频擦写）；
* 恢复后仍然走正常的 60 秒预热，只是基线不用从零开始；
* `mUptime` 一起恢复，所以第 3 页的 `SGP40 lrn` 不会重新计满 1 小时。

注意：

* 状态格式带版本号，算法类型/采样间隔/调参值不一致时会拒绝旧状态并重新学习；
* Sensirion 官方建议 `get_states/set_states` 只在连续运行 ≥3 小时、中断 ≤10 分钟时
  使用；本项目为了桌面使用从启动 10 分钟起就保存，长期断电后请按下面的方法清除；
* 长时间断电后环境可能完全变化，或者更换了 SGP40 模块，旧基线未必合适；
* 想强制重新学习：**长按 BOOT 键 3 秒**（`VOC_RESET_HOLD_MS`）；或在 REPL 执行
  `import sensors; sensors._erase_state()` 后复位/重启。只把 `VOC_STATE_PERSIST`
  改成 `False` 只是本次不读不写，旧状态还在；
* NVS 有磨损均衡和掉电保护，10 分钟一次约 5 万次/年；如果 NVS 不可用，
  会自动退回写板上的 `/voc_state.json`（临时文件 + 改名，避免写一半掉电）。

---

## 8. 报警阈值

在 `config.py` 里调整。

| 指标 | 警告 | 报警 | 依据 |
|------|------|------|------|
| 甲醛 HCHO | 0.08 mg/m³ | 0.10 mg/m³ | 0.08 参考 GB/T 18883-2022（1 小时均值）；0.10 为项目报警线，留一点余量 |
| VOC 指数 | 150 | 250 | Sensirion 定义：100 为基线，越大越差 |

达到“警告”时：主界面对应数值右侧常亮 `!`，不响蜂鸣器。
达到“报警”时：数值右侧闪烁 `*`、板载 LED 快速闪烁、（若启用）蜂鸣器鸣叫。
`!` / `*` 是主界面仅有的数据标记，连接/预热状态不会出现在这一页。

> 代码比较的是瞬时值，而 GB/T 的 0.08 mg/m³ 是 1 小时均值，严格对照标准需要
> 做滑动平均；这里把 0.08 作为警告、0.10 作为报警，属于桌面设备的工程折中。

**预热期间的传感器不参与警告/报警判定。**

---

## 9. 常见问题

### 屏幕点不亮 / 整幅图像向右错位 2 像素

改 `config.py`：

```python
OLED_CONTROLLER = "sh1106"     # 部分 1.3 寸 / 早期 1.2 寸模块用的是 SH1106
```

不确定是哪种的话，跑一下这个脚本，它会交替用两种驱动显示，看哪个正常：

```powershell
python tools/deploy.py run test_oled
```

### 屏幕完全没反应

1. 确认 I2C 扫到了 `0x3C`（第 2 页最下面一行会显示扫描结果）。
   扫不到就检查 SDA/SCL 有没有接反、有没有共地、模块是不是 3.3V 供电。
2. 极少数模块的地址是 `0x3D`，改 `OLED_ADDR`。
3. 图像整个上下颠倒的话，把 `OLED_ROTATE` 改成 `True`。

### 屏幕自己黑了

这是正常的自动息屏：默认 10 秒没有按键操作就关闭显示（`SCREEN_OFF_MS`）。
按一下 BOOT 键唤醒，第一次只亮屏、不翻页。报警时屏幕不会自动亮，但板载 LED
和蜂鸣器照常工作；想一直亮着，把 `SCREEN_OFF_MS` 改成 `0`。

### I2C 扫不到任何设备

先跑这个体检脚本，它会一次把引脚、接反、频率、上拉、地址都过一遍：

```powershell
python tools/deploy.py run test_i2c
```

它会分别用 400k/100k/50k 扫，还会自动把 SDA/SCL 对调试一次，
并读引脚电平判断线是否被拉死。然后按输出逐条排查：

* 检查模块是不是都接在 **3V3** 上（不是 5V）。
* 检查 **GND 是否共地**。
* 确认接的是 **GPIO32 / GPIO33**，不是别的脚。
* 串口里会打印 `[i2c] 总线上没有任何设备, 请检查接线和供电`，可以据此确认。

如果**之前能扫到、突然全扫不到**（比如刚在通电状态下插了设备），
那是总线被从机拉死了：程序会在下一轮扫描时自动做总线恢复
（见 [7.4](#74-总线卡死自恢复)），等 10 秒左右就会自己好，不用断电重启。
日志里会打印 `[i2c] 已重建 I2C 外设, 总线回到空闲`。

### 甲醛读数一直是 `----`

* ZE08 **必须接 5V**（VIN），只接 3.3V 可能不工作。
* **TX/RX 要对调**：ESP32 GPIO17 → 模块 RXD，ESP32 GPIO16 ← 模块 TXD。
* 看第 3 页的 `ZE08 bad` 计数：如果一直涨，说明收到了数据但校验不过，
  通常是波特率不对或者杜邦线太长。
* 模块刚上电需要 3 分钟预热，这期间读数不可信，第 2 页状态会显示 `WARM`。

### 断电重启后 VOC 指数要重新学习吗

默认不用。VOC 基线每 10 分钟保存在 ESP32 的 NVS 里，上电时自动恢复（见
[7.8](#78-断电后保留-voc-基线)）。只有第一次运行、手动清除了状态、或者保存的
状态与当前算法不兼容时才会从零学习。恢复的只是算法基线，SGP40 本身仍要正常预热。

### VOC 指数为什么长时间不是 100

`100` 是算法**学到的当前环境基线**，不是固定目标值。SGP40 的均值/方差时间常数
约 12 小时，所以：

* 刚上电/刚重置时，前 45 分钟学习较快，指数会很快接近 100；
* 之后基线继续慢慢收敛，完全稳定大约要 12 小时；
* 空气比基线干净时指数 <100，比基线差时 >100；
* 搬动位置或断电后恢复了旧环境的基线，指数可能长时间偏离 100。

想立刻以当前空气为新基线：**长按 BOOT 键 3 秒**（会清除旧基线并自动重新学习），
或在 REPL 执行 `import sensors; sensors._erase_state()` 后复位。这样会清除旧基线，
重新从当前环境开始学习（前 45 分钟会快速收敛）。

### 汉字显示成乱码 / 左右错位

这是 `framebuf` 位序的坑，本项目已经处理好了（见[开发笔记](#10-开发笔记)）。
如果你自己改 `ui.py` 里 `MONO_GLYPH` 相关代码，请注意这个差异。

### 报 `MemoryError`

`gc.collect()` 在 `boot.py` 里已经调用。如果还是不够，把 `config.py` 里的
`DEBUG` 和 `DEBUG_SCAN` 关掉可以省下一些内存。

### 串口打不开：`failed to access COM9`

先看 Windows 给的具体原因，两种完全不同的处理：

```powershell
python -c "import serial; serial.Serial('COM9', 115200)"
```

| 报错 | 含义 | 处理 |
|------|------|------|
| `PermissionError(13, '拒绝访问')` | 端口被别的程序占着 | 关掉 Thonny / 串口助手 / 另一个 `mpremote` |
| `PermissionError(13, '连到系统上的设备没有发挥作用', None, 31)` | CH340 芯片自己卡死了 | **拔插一次 USB 线** |

第二种在设备管理器里看着一切正常（`pnputil /enum-devices` 也报 OK），但
`SetCommState` 一直失败，任何波特率都打不开，系统里也没有进程占用它 ——
软件层救不回来（普通权限也用不了 `pnputil /restart-device`），拔插 USB 即可，
板子上的程序不用重烧。

> 实测触发场景：连续几十秒不停地高速打印后、或者传给板子的数据流中途被打断。
> 我只遇到过一次，拔插后立刻恢复。

---

## 10. 开发笔记

### 10.1 MicroPython 的 `MONO_HLSB` / `MONO_HMSB` 位序并不统一

字库 `cfont.py` 里的点阵是按 **LSB-first** 生成的（每字节的 bit0 是最左边的像素）。
但是在 **ESP32 v1.29.0** 上实测：

| 格式 | `pixel(0,0,1)` 之后 buffer[0] |
|------|-------------------------------|
| `MONO_HLSB` | `0x80`（bit7 在最左） |
| `MONO_HMSB` | `0x01`（bit0 在最左） |

名字和实际位序和直觉是反的。一旦选错，汉字会**按字节对称错位**，看起来就是一堆乱码，
而且 ASCII 文本完全正常（因为那是内置字库画的），非常容易误判成字库生成错了。

所以 `ui.py` 不猜，直接在运行的时候画一个像素试出来（`ui.MONO_GLYPH`）：

```python
def _lsb_first_mono_format():
    for name in ("MONO_HMSB", "MONO_HLSB"):
        fmt = getattr(framebuf, name, None)
        if fmt is None:
            continue
        buf = bytearray(2)
        fb = framebuf.FrameBuffer(buf, 16, 1, fmt)
        fb.pixel(0, 0, 1)
        if buf[0] == 0x01:
            return fmt
    return framebuf.MONO_HLSB
```

这样字库文件不用改，换固件也不会翻车。

### 10.2 中文排版的行距

汉字是 16 像素高，所以**同一屏里两行汉字之间至少要隔 16 像素**，否则会叠字。
64 / 16 = 4，一屏最多放 4 行汉字。

主界面据此分区：顶部标题（汉字，y=0~15）占一条 16 像素的带，
下面 48 像素留给 4 行读数（英文/数字，8 像素高），按 12 像素行距排：
y=16 / 28 / 40 / 52。

> 数据行用固定列对齐：标签画在 x=0，数值统一从 x=40 开始。
> `HCHO 0.04mg/m3` 的数值是 9 个字符 = 72 像素，从 x=40 到 x=112 结束，
> 右边到 x=120 还有 8 像素给警告/报警标记 `!` / `*`（x=120）。
> 状态信息已经不在这页，所以不用再担心"汉字 + 数值超宽"的问题。

### 10.3 `tools/layout_preview.py`

不接屏幕也能检查排版：它在开发板上跑 `tools/tests/test_ui.py`（用假 framebuffer），
把返回的 ASCII 画面渲染成 `tools/ui_layout.png`。
改了界面布局之后跑一下，比接上屏幕来回看快得多。

### 10.4 MicroPython 的 `bytearray` 不支持 `del buf[i]`

`ze08.py` 一开始用 `del self.buf[0]` 从接收缓冲里丢弃字节。
这在电脑上的 CPython 完全合法，`py_compile` 也查不出来，
但 **MicroPython 的 `bytearray` 不支持元素删除**，一执行就
`TypeError: 'bytearray' object doesn't support item deletion`。

只能切片重建：

```python
self.buf = self.buf[1:]        # 丢掉帧头一个字节
self.buf = self.buf[9:]        # 丢掉一整帧
self.buf = self.buf[-64:]      # 只保留最后 64 字节
```

这个 bug 只在**串口真的收到字节**时才触发，所以如果 ZE08 没接线、
RX 悬空没噪声，程序看起来一切正常，非常容易漏掉。
因此 `tools/tests/test_ze08_parse.py` 用**假 UART** 在真正的 MicroPython 上
跑解析逻辑（包含垃圾字节、超长缓冲、坏校验、分包到达等场景），
就是为了让这类“主机能跑、板上翻车”的差异无处可藏。

> 同样的道理：凡是 `del obj[i]` / `list.pop(0)` / `bytes.hex()` 这类
> 在 CPython 上理所当然的写法，移植到 MicroPython 前都要留个心。

### 10.5 ESP32 上 `I2C(0, ...)` 是单例

在 ESP32 的 MicroPython 上，`I2C(0, sda=..., scl=...)` 重复调用返回的是
**同一个对象**（`a is b` 为真），只有换 bus id 才会拿到新对象。
实测：

```python
a = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
b = I2C(0, sda=Pin(32), scl=Pin(33), freq=400000)
a is b        # True
```

这对"重建总线"是好事：重建时所有已经持有了这个对象的驱动（OLED、各传感器）
自动继续有效，不用到处替换引用。所以 `_rebuild_bus()` 里那次重扫能直接成功。

另外两点实测结论，写代码时别想当然：

* `i2c.init(sda=..., scl=..., freq=...)` 会报
  `OSError: I2C operation not supported` —— **不要**用它做原地重初始化；
  直接再写一次 `I2C(0, ...)` 即可（走的是构造函数路径，能正常重配）。
* `i2c.deinit()` 之后同一个对象仍然能 `scan()`，不要指望 deinit 会把对象废掉。

`src/ui.py` 里保留了 `rebind_i2c()`、`main.py` 里保留了 `rebind_i2c` 回调，
是**纯防御**：万一以后某个固件改成每次返回新对象，屏幕也不至于因为用着旧对象而黑掉。

### 10.6 状态机的初始状态必须有人负责"消化"（一个真 bug）

`Channel.__init__` 把通道初值设成 `ST_PROBE`（"正在探测"），本意是开机先显示
一屏"自检中"（后来按用户要求改成开机直接进主界面，但初值的问题一样存在）。
两个 I2C 传感器的步进函数里，探测分支写的是：

```python
if ch.state in (ST_ABSENT, ST_FAULT):     # ← 少了 ST_PROBE
```

结果 `ST_PROBE` 这个状态**没有任何代码能把它转出去**：

* SHT40 / SGP40 永远不会被探测 → 永远读不到数
* 第 2 页状态永远停在 `SCAN`，主界面数据行一直是 `----`

这个 bug 只有在真正接上传感器之后才会暴露，纯看代码很容易漏。修法就是把
`ST_PROBE` 一起放进探测分支：

```python
if ch.state in (ST_PROBE, ST_ABSENT, ST_FAULT):
```

**教训**：给状态机加一个新的初始状态时，一定要问一句"谁负责把它转走"，
并且拿真实硬件跑一遍 —— 没有外设时所有通道都停在初始状态，
看起来和"设备没接"是一模一样的。

### 10.7 报警判定不能挂在屏幕刷新上

原先 `_eval_alarm()` 只在 `snapshot()` 里调用，而 `snapshot()` 只在
**屏幕对象存在**时才被调用：

```python
if self.view is not None:
    self.view.render(self.mgr.snapshot())     # ← 报警判定藏在里面
```

也就是说 OLED 一旦没接上（或者连续 5 次刷新失败被停用），`self.alarm`
就永远是初始的全 `False`：指示灯只会心跳、蜂鸣器永远不响 —— 报警功能
在"没有屏幕"的情况下静默失效。

改成 `any_alarm()` 自己先算一遍，谁问谁负责：

```python
def any_alarm(self):
    self._eval_alarm()
    return any(self.alarm.values())
```

**教训**：安全相关的判定（报警）不要寄生在显示路径上，显示只是它的一个消费者。

### 10.8 `learn_end` 是所有通道共用的，界面要自己区分

`begin_warmup()` 会给**每一个**通道都设 `learn_end = now + VOC_LEARN_S`，
但"学习基线"只有 SGP40 才有这回事。`Channel.snapshot()` 里如果不区分，
SHT40 / ZE08 在就绪后也会一直带着 `learning=True`：

* 当时主界面每行右侧会一直显示 `~`，看起来像"一直在学习"
* 更糟的是 `_data_row()` 里 `learning` 分支排在 `is_alarm` 前面，
  会把报警闪烁星号 `*` 盖掉 —— 报警看不见了

现在主界面不再显示 `learning`（状态统一看第 2 页，学习标志看第 3 页的
`SGP40 lrn`），但 `snapshot()` 仍按通道名过滤，避免以后重新使用这个字段时
再踩同一个坑：

```python
"learning": (self.state == ST_READY
             and self.key == "sgp40"
             and ticks_diff(self.learn_end, now) > 0),
```

### 10.9 一个 `enabled` 有多个消费者，漏一个就不一致

`config.py` 里的 `SHT40_ENABLED` / `SGP40_ENABLED` / `ZE08_ENABLED` 决定某个通道
要不要参与采集。界面上有多个地方会消费这个标志：

| 位置 | 不判断会怎样 |
|------|--------------|
| 主界面数据行 | 关掉的通道仍占一行，显示 `----` 或过期数值 |
| 第 2 页状态 | 关掉的通道应显示 `OFF`，否则会被误判成 `N/A` / `ERR` |
| （历史）状态灯 / 状态栏 | 关掉的通道也占灯位、一直报"未连接"，把真正的问题挤掉 |

移除 CO2 时就踩到了第一个：状态灯和状态栏都跳过 SCD40 了，唯独 CO2 那一行还照画。
`tools/tests/test_ui.py` 里"通道关闭"那个用例、以及 `layout_preview.py` 渲染出的
排版图，都能一眼看到多出来的那一行 —— 这也是留着这些测试的价值。

现在 `snapshot()` 会给每个通道带上 `enabled`，每个消费者统一用
`ch.get("enabled", True)` 判断；主界面数据行再按剩余通道自动上收：

```python
y = 16
for key, label, text in rows:
    if not chans[key].get("enabled", True):
        continue
    self._value_row(y, label, text, alarm.get(key))
    y += 12
```

**教训**：一个开关如果有多个消费者，要么在源头统一过滤，要么每个消费者都判一遍
并且都写进测试 —— 混着来必然漏一个。

### 10.10 基线保存写在 ESP32 上，以及一个 `mUptime` 的 bug

SGP40 只负责输出原始值，VOC 算法（含基线）跑在 ESP32 上；SGP40 内部没有
用户可访问的基线存储。所以 `sensors.py` 把状态写进 **ESP32 NVS**，写坏了也
不会碰传感器。状态里带版本号、算法类型、采样间隔和调参值，恢复时逐项校验，
避免固件升级或改配置后拿旧基线硬套。

实现持久化时顺手调整了 `voc.py` 的 `mUptime`：原来它只在 blackout 分支里累加，
45 秒后就停在 46 左右。这个字段只参与 blackout 判断，所以不影响指数输出；
但恢复基线后要用它扣减 1 小时学习期标记，因此改成每次采样都累加。
`test_voc.py` 里加了对应断言。

> 注意：这个 `mUptime` 的语义是“本项目累计采样秒数”，与官方 C 实现里只用于
> blackout 的 uptime 不同；它只影响学习期显示和状态持久化，不参与指数计算。

---

## 11. 接入 Home Assistant (MQTT)

米家是封闭生态，DIY ESP32 不能直接变成米家原生设备；本项目选择 Home Assistant
本地接入。HA 的 MQTT Discovery 会自动创建设备和实体，不需要手写
`configuration.yaml`。

### 11.1 准备

* HA 已安装并配置 MQTT 集成，Mosquitto / MQTT broker 可从局域网访问（默认 1883）。
* ESP32 和 HA 在同一个 Wi-Fi 网段。
* 板子固件包含 `umqtt.simple`。当前板子已确认有；如果提示缺少：

  ```powershell
  python -m mpremote connect COM9 mip install umqtt.simple
  ```

### 11.2 填凭据（不要提交）

`src/secrets.py` 已被 `.gitignore` 忽略，填 Wi-Fi 和 MQTT。如果从 git 重新
clone 后没有这个文件，新建一个同名文件即可（不要提交）：

```python
WIFI_SSID = "..."
WIFI_PASSWORD = "..."
MQTT_HOST = "192.168.1.10"      # HA/Mosquitto 的局域网地址
MQTT_PORT = 1883
MQTT_USER = "..."
MQTT_PASSWORD = "..."
```

`MQTT_USER/PASSWORD` 留空表示匿名连接（取决于 broker 配置）。`WIFI_SSID` 或
`MQTT_HOST` 留空时网络功能完全关闭，本地采集、屏幕、按键都不受影响。

### 11.3 部署与发现

```powershell
python tools/deploy.py
```

`deploy.py` 会把 `secrets.py` 一起上传（它只被 git 忽略，不会被跳过）。
复位后串口应出现：

```
[net] Wi-Fi 已连接: 192.168.x.x
[net] MQTT 已连接
[net] HA discovery 已发布
```

HA 的 MQTT 集成会自动出现设备 **ESP32 AirCheck**，包含：

| 实体 | 单位 |
|------|------|
| 温度 | °C |
| 湿度 | % |
| 甲醛 | mg/m³ |
| VOC 指数 | idx |
| 空气质量报警 | ON / OFF（binary_sensor） |

### 11.4 主题与配置

设备 ID 使用 ESP32 的 MAC：

| 用途 | Topic |
|------|-------|
| 状态 JSON | `esp32aircheck/<mac>/state` |
| 在线状态 | `esp32aircheck/<mac>/availability` |
| Discovery | `homeassistant/sensor/esp32aircheck_<mac>_.../config` |

状态 JSON 字段：`temp`、`rh`、`hcho`、`voc`、`alarm`、`uptime`。
状态和 Discovery 配置都是 retained；掉线时 broker 会发布 `offline`（Last Will）。

可在 `config.py` 调整：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MQTT_BASE` | `"esp32aircheck"` | 主题前缀 |
| `MQTT_PUBLISH_MS` | `10000` | 状态发布周期 |
| `HA_DISCOVERY` | `True` | 是否自动发布 Discovery |
| `HA_DISCOVERY_PREFIX` | `"homeassistant"` | HA Discovery 前缀 |
| `DEVICE_NAME` | `"ESP32 AirCheck"` | HA 设备名 |

默认使用 1883 明文 MQTT，不支持 TLS。HA 看不到设备时，先看串口 `[net]` 日志，
再确认 MQTT 集成、broker 用户权限和 `MQTT_HOST` 是否为 HA 的局域网 IP。

> 米家 App 原生卡片仍然做不到：米家需要认证模组/产品，HA 的米家集成只能把
> 米家设备导入 HA，不能反向把 HA 设备导入米家。小爱同学可以通过云平台桥接，
> 但通常不是米家原生设备。

---

## 附：`config.py` 速查

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `DEBUG` | `True` | 是否往串口打日志 |
| `I2C_SDA` | `32` | I2C 数据引脚 |
| `I2C_SCL` | `33` | I2C 时钟引脚 |
| `I2C_FREQ` | `400000` | I2C 频率 |
| `OLED_CONTROLLER` | `"ssd1306"` | 可改 `"sh1106"` |
| `OLED_ADDR` | `0x3C` | 少数模块是 `0x3D` |
| `OLED_ROTATE` | `False` | 180° 翻转 |
| `SCREEN_OFF_MS` | `10000` | 无操作自动熄屏时间；`0` 关闭 |
| `ZE08_TX` | `17` | ESP32 → ZE08 RXD |
| `ZE08_RX` | `16` | ESP32 ← ZE08 TXD |
| `STATUS_LED` | `2` | 板载 LED，`None` 表示不用 |
| `BUTTON_PIN` | `0` | 翻页按键（板上 BOOT 键） |
| `VOC_RESET_HOLD_MS` | `3000` | 长按 BOOT 重置 VOC 基线的时间；`0` 关闭 |
| `BUZZER_PIN` | `25` | 蜂鸣器，默认 `BUZZER_ENABLED = False` |
| `*_ENABLED` | `True` | `SHT40_ENABLED` / `SGP40_ENABLED` / `ZE08_ENABLED`，关掉后该行隐藏，第 2 页显示 `OFF` |
| `WARMUP_*` | 1/60/180 | SHT40 / SGP40 / ZE08 的预热秒数 |
| `VOC_STATE_PERSIST` | `True` | VOC 基线跨断电保存到 ESP32 NVS |
| `VOC_STATE_SAVE_MS` | `600000` | 基线保存周期（10 分钟） |
| `RESCAN_MS` | `10000` | 热插拔重新探测周期 |
| `HCHO_WARN/ALARM` | 0.08 / 0.10 | mg/m³，警告 / 报警 |
| `TVOC_WARN/ALARM` | 150 / 250 | VOC 指数（界面标签 `VOC`），警告 / 报警 |
| `WIFI_SSID/PASSWORD` | `src/secrets.py` | 留空关闭网络 |
| `MQTT_HOST/PORT/USER/PASSWORD` | `src/secrets.py` | Home Assistant broker |
| `MQTT_BASE` | `"esp32aircheck"` | MQTT 主题前缀 |
| `MQTT_PUBLISH_MS` | `10000` | 状态发布周期 |
| `HA_DISCOVERY` | `True` | Home Assistant 自动发现 |
