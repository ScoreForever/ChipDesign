"""Single definition of how the program testbench is compiled.

Every profiling / evaluation script builds tb_npu_program through this module,
so memory configurations and profiling defines cannot drift between tools.

Memory configurations
  behavioral  generic synchronous RAM models (default RTL simulation)
  fpga_mem    CHIPDESIGN_FPGA_MEM: registered BRAM response path used on the EGO1 board
  ra1shd      RA1SHD_2048x32M8 macro interface model for both 8 KiB SRAMs (ASIC netlist path)
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FILELIST = ROOT / 'hardware/soc/sim/filelists/chipdesign_nocpu.f'
TESTBENCH = 'hardware/soc/sim/tb/tb_npu_program.sv'
RA1SHD_MODEL = 'hardware/npu/tb/ra1shd_functional_model.sv'

MEMORY = {
    'behavioral': ([], []),
    'fpga_mem': (['CHIPDESIGN_FPGA_MEM'], []),
    'ra1shd': (['NPU_USE_RA1SHD_MODEL', 'NPU_USE_RA1SHD_ACTIVATION'], [RA1SHD_MODEL]),
}
# Profiling build: synthesis-style trace configuration with per-operator summaries.
PROFILE_DEFINES = ['SYNTHESIS', 'NPU_ARRAY_COLS=8', 'NPU_ENABLE_WAVE_TRACE=0', 'NPU_ENABLE_OP_TRACE=1']


def sources():
    return [s for s in FILELIST.read_text().split() if s]


def program_tb_command(image, *, memory='behavioral', tile=16, profile=True, extra_defines=()):
    """iverilog command for tb_npu_program; run it with cwd=ROOT."""
    if memory not in MEMORY:
        raise ValueError(f'unknown memory configuration {memory!r}; choose from {sorted(MEMORY)}')
    mem_defines, mem_sources = MEMORY[memory]
    defines = (PROFILE_DEFINES + [f'NPU_TILE={tile}'] if profile else []) + mem_defines + list(extra_defines)
    return ['iverilog', '-g2012', '-s', 'tb_npu_program', '-o', str(image),
            *['-D' + d for d in defines], *sources(), *mem_sources, TESTBENCH]


def compile_program_tb(image, log, **kw):
    import run_program_regression as regression
    regression.run(program_tb_command(image, **kw), log)


def watched_sources(memory='behavioral'):
    """Every file a program build reads (RTL, memory model, testbench).

    Profilers hash these before and after a measurement so that editing the
    checkout mid-run invalidates the result instead of silently mixing versions.
    """
    return sources() + MEMORY[memory][1] + [TESTBENCH]
