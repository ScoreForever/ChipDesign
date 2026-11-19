"""Generic ONNX Frontend + Weight-Stationary 3D Tiling Compiler for ABI 5.0 INT4 NPU."""
from .ir import GraphIR, NodeIR, TensorDesc, infer_tensors, oracle_reference, requant_np, wrap32_np
from .onnx_frontend import float_scale_to_q15, load_onnx_to_ir
from .tiler import HostPostOp, ProgramGroup, TileCall, TiledPlan, compile_graph_to_plan
from .runtime import NpuBackend, ReferenceBackend, RtlBackend, TiledRuntime
from .verify import verify_plan

__all__ = [
    'TensorDesc',
    'NodeIR',
    'GraphIR',
    'infer_tensors',
    'oracle_reference',
    'requant_np',
    'wrap32_np',
    'float_scale_to_q15',
    'load_onnx_to_ir',
    'TileCall',
    'ProgramGroup',
    'HostPostOp',
    'TiledPlan',
    'compile_graph_to_plan',
    'ReferenceBackend',
    'NpuBackend',
    'RtlBackend',
    'TiledRuntime',
    'verify_plan',
]
