from pathlib import Path
import sys,runpy
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'source/hardware/asic/optimize'))
sys.argv=[str(root/'postlayout_hierarchical.py'),'--candidate',str(root/'work/area129_icg/candidates/area129_icg_diag75_eco1')]
runpy.run_path(str(root/'postlayout_hierarchical.py'),run_name='__main__')
