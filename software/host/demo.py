"""Launch an interactive ChipDesign NPU demo."""

import argparse
from importlib import import_module


DEMOS = {
    "mnist": ("手写数字识别", "在画板上写一个数字，停笔后查看识别结果。", "demos.mnist"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo", nargs="?", choices=DEMOS, help="skip the demo picker")
    parser.add_argument("--host", default="127.0.0.1", help="OpenOCD Tcl RPC host")
    parser.add_argument("--port", default=6666, type=int, help="OpenOCD Tcl RPC port")
    parser.add_argument("--backend", choices=("fpga", "reference"), default="fpga",
                        help="use FPGA via JTAG or the local integer reference")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    try:
        import tkinter as tk
        from demos.window import DemoWindow
    except ImportError as exc:
        parser.exit(1, f"无法加载 Tkinter，请为当前 Python 安装 Tk 支持。\n{exc}\n")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        parser.exit(1, f"无法打开桌面窗口，请检查图形桌面或 WSLg。\n{exc}\n")

    app = DemoWindow(root, DEMOS, args, import_module)
    if args.demo:
        app.open_demo(args.demo)
    root.mainloop()


if __name__ == "__main__":
    main()
