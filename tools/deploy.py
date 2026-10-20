# -*- coding: utf-8 -*-
"""
部署 / 调试工具  (在电脑上运行, 不是跑在开发板上)
================================================================
用法:

    python tools/deploy.py                  上传 src/ 里所有 .py 到开发板
    python tools/deploy.py --backup         上传前先把板上同名文件备份到 tools/backup/
    python tools/deploy.py ls               列出开发板上的文件
    python tools/deploy.py run test_sht40   在开发板上运行 tools/tests/test_sht40.py
    python tools/deploy.py repl             打开交互式串口终端 (Ctrl-] 退出)
    python tools/deploy.py reset            软复位开发板
    python tools/deploy.py exec "print(1)"  在开发板上执行一小段代码

默认串口是 COM9, 用 --port 指定其它串口。
依赖: 只需要 mpremote (pip install mpremote)
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "src")
TESTS = os.path.join(HERE, "tests")
BACKUP = os.path.join(HERE, "backup")

DEFAULT_PORT = "COM9"


def mpremote(port, *args, capture=False, check=False):
    cmd = [sys.executable, "-m", "mpremote", "connect", port] + list(args)
    return subprocess.run(cmd, capture_output=capture, check=check)


def list_src():
    return sorted(f for f in os.listdir(SRC) if f.endswith(".py"))


def do_ls(port):
    mpremote(port, "fs", "ls", ":", check=True)


def do_backup(port, files):
    os.makedirs(BACKUP, exist_ok=True)
    saved = 0
    for name in files:
        r = mpremote(port, "fs", "cat", ":" + name, capture=True)
        if r.returncode == 0 and r.stdout and b"Traceback" not in r.stdout[:200]:
            with open(os.path.join(BACKUP, name), "wb") as f:
                f.write(r.stdout)
            saved += 1
    print("  已备份 %d 个文件到 %s" % (saved, os.path.relpath(BACKUP)))


def do_deploy(port, backup=False, files=None):
    files = files or list_src()
    print("串口 %s, 准备上传 %d 个文件:" % (port, len(files)))
    for f in files:
        print("  -", f)

    if backup:
        print("先备份板上同名文件…")
        do_backup(port, files)

    # 用 mpremote 的 "命令 + 命令" 语法一次传完, 少开几次串口
    parts = []
    for name in files:
        parts.append(["fs", "cp", os.path.join(SRC, name), ":" + name])
    args = []
    for i, p in enumerate(parts):
        if i:
            args.append("+")
        args.extend(p)
    mpremote(port, *args, check=True)
    print("上传完成，正在重启开发板…")
    # mpremote 的 fs cp 本身不会复位; 显式 reset 一下, 保证新代码立即生效。
    mpremote(port, "reset", check=True)
    print("开发板已重启，开始运行 main.py。")
    print("想直接看输出: python tools/deploy.py repl")


def do_run(port, script):
    path = os.path.join(TESTS, script)
    if not os.path.isfile(path):
        path = os.path.join(TESTS, script + ".py")
    if not os.path.isfile(path):
        print("找不到测试脚本:", script)
        print("可用脚本:", ", ".join(sorted(
            os.path.splitext(f)[0] for f in os.listdir(TESTS) if f.endswith(".py"))))
        sys.exit(1)
    print("运行", os.path.relpath(path), "(Ctrl-C 结束)")
    mpremote(port, "run", path)


def main():
    ap = argparse.ArgumentParser(
        description="ESP32 空气质量检测仪 部署工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument("action", nargs="?", default="deploy",
                    choices=["deploy", "ls", "run", "repl", "reset", "exec"])
    ap.add_argument("arg", nargs="?", help="run 的脚本名 / exec 的代码")
    ap.add_argument("--port", default=os.environ.get("MP_PORT", DEFAULT_PORT))
    ap.add_argument("--backup", action="store_true", help="上传前备份板上同名文件")
    ap.add_argument("--only", nargs="*", help="只上传指定的文件")
    args = ap.parse_args()

    if shutil.which(sys.executable) is None:
        pass

    if args.action == "deploy":
        do_deploy(args.port, args.backup, args.only)
    elif args.action == "ls":
        do_ls(args.port)
    elif args.action == "run":
        if not args.arg:
            print("run 需要脚本名, 例如: run test_sht40")
            sys.exit(1)
        do_run(args.port, args.arg)
    elif args.action == "repl":
        mpremote(args.port, "repl")
    elif args.action == "reset":
        mpremote(args.port, "reset", check=True)
    elif args.action == "exec":
        mpremote(args.port, "exec", args.arg, check=True)


if __name__ == "__main__":
    main()
