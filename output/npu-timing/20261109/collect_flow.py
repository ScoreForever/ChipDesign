from pathlib import Path
import json
root=Path(__file__).resolve().parent
build=root/'checkout_peek/build/timing_peek_50mhz'
text=(build/'console.log').read_text(errors='replace')
commands=[s for s in text.splitlines() if s.startswith('Command: route_design') or s.startswith('Command: phys_opt_design')]
record=dict(commands=commands,route_calls=sum(s.startswith('Command: route_design') for s in commands),
            negative_slack_retry=any('AggressiveExplore' in s for s in commands),
            estimated_timing_lines=[s for s in text.splitlines() if 'Estimated Timing Summary' in s])
assert record['route_calls']==1 and record['negative_slack_retry'] is False
(root/'implementation_flow.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
