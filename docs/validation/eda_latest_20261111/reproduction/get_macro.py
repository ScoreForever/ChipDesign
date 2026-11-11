from pathlib import Path
import sys,os
sys.path.insert(0,str(Path(os.environ['EDA_RUN'])/'src/hardware/asic/optimize'))
from flow import libraries
print(libraries()['macro_verilog'])
