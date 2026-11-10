from pathlib import Path
import json,tarfile
root=Path(__file__).resolve().parent
model=root/'board_peek_final';cdc=root/'board_cdc_peek_final'
a=json.loads((model/'report.json').read_text());b=json.loads((cdc/'report.json').read_text())
assert a['passed'] and b['passed'] and len(a['models'])==7 and len(b['checks'])==5
assert all(len(m['runs'])==2 and all(r['matches_expected'] and r['cycles_match_rtl'] for r in m['runs']) for m in a['models'])
assert a['bitstream_sha256']==json.loads((root/'program_gate.json').read_text())['bitstream_sha256']
with tarfile.open(root/'board_peek.tar.gz','w:gz') as t:
    t.add(model,arcname='board_final/'+model.name)
    t.add(cdc,arcname='board_final/'+cdc.name)
    for n in ('openocd_peek_final.log','board_peek_final.log','board_cdc_peek_final.log'):
        t.add(root/n,arcname='board_final/'+n)
print('PASS: 14 model runs and 5 board CDC/recovery checks; bitstream SHA matched')
