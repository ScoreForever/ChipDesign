"""One resident model on the selected demo backend."""

import json
from dataclasses import asdict, dataclass
import math
import operator
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import time

from nocpu_jtag import JtagTransport, Npu, TclRpc
from npu_e2e import PhaseTimer, TimedTransport, measure
from npu_program import Package, compile_model, encode_tensor, reference


@dataclass(frozen=True)
class Prediction:
    scores: tuple
    elapsed_ms: float
    cycles: int | None = None
    counter_ms: float = 0.0
    transfer_ms: float | None = None
    compute_wait_ms: float | None = None

    @property
    def index(self):
        return max(range(len(self.scores)), key=self.scores.__getitem__)

    def __iter__(self):
        # Keep the original MNIST/KWS plugin interface.
        return iter((self.index, self.elapsed_ms))

    def to_dict(self):
        return dict(asdict(self), index=self.index)

    def timing_text(self):
        cycles = "软件参考，无板端周期" if self.cycles is None else f"片上 {self.cycles:,} 周期"
        phases = (f" · 传输 {self.transfer_ms:.1f} ms · 启动/等待 {self.compute_wait_ms:.1f} ms"
                  if self.transfer_ms is not None else "")
        return f"推理往返 {self.elapsed_ms:.1f} ms · {cycles}{phases}"


class InferenceSession:
    def __init__(self, args, model_name, directory=None):
        self.args = args
        self.name = model_name
        self.directory = Path(directory) if directory else Path(__file__).resolve().parents[2] / "zoo" / "pretrained" / model_name
        self.package = Package.load(self.directory / "c8")
        self.metadata = self.package.manifest["metadata"]
        self.scale = self.metadata["input_scale"]
        if not math.isfinite(self.scale) or self.scale <= 0:
            raise ValueError("模型 input_scale 必须是正数")
        self.source = "FPGA · JTAG" if args.backend == "fpga" else "软件参考 · INT4"
        self.rpc = None
        self.npu = None
        self.model = None
        self.timer = PhaseTimer()

    def connect(self):
        self.close()
        if self.args.backend == "reference":
            self.model = json.loads((self.directory / "model.json").read_text(encoding="utf-8"))
            rebuilt = compile_model(self.model, cols=self.package.manifest["cols"])
            if rebuilt.image != self.package.image or rebuilt.manifest.get('metadata') != self.metadata:
                self.model = None
                raise ValueError("model.json 与 FPGA 模型包不一致，请重新编译")
            return
        rpc = TclRpc(self.args.host, self.args.port, timeout=6)
        try:
            npu = Npu(TimedTransport(JtagTransport(rpc), self.timer))
            npu.load_model(self.package)
        except (OSError, RuntimeError, ValueError):
            rpc.close()
            raise
        self.rpc, self.npu = rpc, npu

    def infer(self, values, *, capture_ms=None, preprocess_ms=None):
        values = [operator.index(v) for v in values]
        if len(values) != self.package.manifest["input_elements"]:
            raise ValueError("模型输入长度错误")
        lo, hi = (0, 15) if self.package.manifest["input_dtype"] == "u4" else (-8, 7)
        if any(v < lo or v > hi for v in values):
            raise ValueError("模型输入超出 INT4 范围")
        if self.model is None and self.npu is None:
            raise RuntimeError("请先连接推理后端")
        transfer_ms = compute_wait_ms = None
        started = time.perf_counter()
        if self.args.backend == "reference":
            scores = reference(self.model, values)[self.model["nodes"][-1]["output"]]
        else:
            scores, timing = measure(self.timer, 'input_metadata', lambda: self.npu.infer_values(values))
            compute_wait_ms = sum(phase['wall_ms'] for phase in timing['phases']
                                  if phase['phase'] in ('start', 'completion_poll'))
            transfer_ms = timing['total_wall_ms'] - compute_wait_ms
        elapsed = (time.perf_counter() - started) * 1000
        if len(scores) != self.package.manifest["output_elements"]:
            raise ValueError("模型输出长度错误")
        counter_start = time.perf_counter()
        # Read the passive completed-job counter separately from inference latency.
        cycles = self.npu.read(0x38) if self.npu is not None else None
        result = Prediction(tuple(scores), elapsed, cycles,
                            (time.perf_counter() - counter_start) * 1000 if cycles is not None else 0.0,
                            transfer_ms, compute_wait_ms)
        if getattr(self.args, "log", None):
            path = Path(self.args.log)
            path.parent.mkdir(parents=True, exist_ok=True)
            event = dict(timestamp=datetime.now(timezone.utc).isoformat(), model=self.name,
                         backend=self.args.backend, model_sha256=hashlib.sha256(self.package.image).hexdigest(),
                         input_hex=encode_tensor(values,self.package.manifest["input_dtype"]).hex(),
                         capture_ms=capture_ms, preprocess_ms=preprocess_ms,
                         **result.to_dict())
            with path.open("a",encoding="utf-8") as handle:
                handle.write(json.dumps(event,ensure_ascii=False)+"\n")
        return result

    def close(self):
        if self.rpc is not None:
            self.rpc.close()
        self.rpc = self.npu = None
        self.model = None
