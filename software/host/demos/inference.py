"""One resident model on the selected demo backend."""

import json
from pathlib import Path
import time

from nocpu_jtag import JtagTransport, Npu, TclRpc
from npu_program import Package, reference


class InferenceSession:
    def __init__(self, args, model_name):
        self.args = args
        self.directory = Path(__file__).resolve().parents[2] / "zoo" / "pretrained" / model_name
        self.package = Package.load(self.directory / "c8")
        self.metadata = self.package.manifest["metadata"]
        self.scale = self.metadata["input_scale"]
        self.source = "FPGA · JTAG" if args.backend == "fpga" else "软件参考 · INT4"
        self.rpc = None
        self.npu = None
        self.model = None

    def connect(self):
        self.close()
        if self.args.backend == "reference":
            self.model = json.loads((self.directory / "model.json").read_text(encoding="utf-8"))
            return
        rpc = TclRpc(self.args.host, self.args.port, timeout=6)
        try:
            npu = Npu(JtagTransport(rpc))
            npu.load_model(self.package)
        except (OSError, RuntimeError, ValueError):
            rpc.close()
            raise
        self.rpc, self.npu = rpc, npu

    def infer(self, values):
        started = time.perf_counter()
        if self.args.backend == "reference":
            scores = reference(self.model, values)[self.model["nodes"][-1]["output"]]
        else:
            scores = self.npu.infer_values(values)
        elapsed = (time.perf_counter() - started) * 1000
        if len(scores) != self.package.manifest["output_elements"]:
            raise ValueError("模型输出长度错误")
        return max(range(len(scores)), key=scores.__getitem__), elapsed

    def close(self):
        if self.rpc is not None:
            self.rpc.close()
        self.rpc = self.npu = None
