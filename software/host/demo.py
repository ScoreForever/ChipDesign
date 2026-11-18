"""Launch an interactive ChipDesign NPU demo."""

import argparse
from importlib import import_module
from pathlib import Path


DEMOS = {
    "mnist": ("手写数字识别", "在画板上写一个数字，停笔后查看识别结果。", "demos.mnist"),
    "kws": ("语音关键词识别", "用麦克风说出 yes、no、up 或 down。", "demos.kws"),
    "ecg": ("ECG 心拍分类", "回放带标注心电记录，显示五类心拍分数。", "demos.ecg"),
    "har": ("人体活动识别", "回放六类活动，或导入手机惯性信号。", "demos.har"),
    "face": ("人脸框选", "电脑 YuNet 或 INT4 NPU 近景框选。", "demos.face"),
    "emotion": ("表情分类", "电脑裁脸，再由 INT4 模型输出七类分数。", "demos.emotion"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("demo", nargs="?", choices=DEMOS, help="skip the demo picker")
    parser.add_argument("--host", default="127.0.0.1", help="OpenOCD Tcl RPC host")
    parser.add_argument("--port", default=6666, type=int, help="OpenOCD Tcl RPC port")
    parser.add_argument("--backend", choices=("fpga", "reference"), default=None,
                        help="use FPGA via JTAG or the local integer reference")
    parser.add_argument("--list", action="store_true", help="list demos without opening a window")
    parser.add_argument("--self-test", action="store_true", help="check published golden outputs without a desktop; default backend is reference")
    parser.add_argument("--input", type=Path, help="headless input: golden JSON, packed BIN, image, WAV, MIT-BIH HEA, UCI ZIP or phone CSV")
    parser.add_argument("--output", type=Path, help="new headless JSON report path")
    parser.add_argument("--limit", type=int, default=16, help="maximum replay/golden inputs (positive)")
    parser.add_argument("--log", type=Path, help="append inference events and packed inputs to local JSONL")
    parser.add_argument("--face-mode", choices=("host", "npu"), default="host")
    parser.add_argument("--yunet", type=Path, default=Path(__file__).resolve().parents[2] / "build/demo_data/yunet.onnx")
    parser.add_argument("--vision-models", type=Path, help="directory containing face/ and emotion/ NPU packages")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--imu-rotation", type=Path, help="JSON 3x3 rotation matrix: phone axes to training waist axes")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.self_test and args.input:
        parser.error("--self-test and --input are mutually exclusive")
    if args.input and not args.demo:
        parser.error("--input requires a demo name")
    args.backend = args.backend or ("reference" if args.self_test else "fpga")
    if args.imu_rotation:
        import json
        try:
            args.imu_rotation = json.loads(args.imu_rotation.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
    if args.list:
        for key, (title, description, _) in DEMOS.items():
            print(f"{key:8s} {title}: {description}")
        return
    if args.self_test or args.input:
        if not args.output:
            parser.error("headless mode requires --output (a new JSON file)")
        from demos.runner import run
        try:
            passed = run(args, DEMOS)
        except (OSError, RuntimeError, ValueError, ImportError, KeyError) as exc:
            parser.exit(1, f"Demo 验证失败：{exc}\n")
        raise SystemExit(0 if passed else 1)

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
