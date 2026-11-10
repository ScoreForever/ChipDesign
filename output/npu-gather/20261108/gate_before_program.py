from pathlib import Path
import hashlib,json,re
root=Path(__file__).resolve().parent
repo=root/'checkout_forward';build=repo/'build/gather_forward_50mhz'
text=(build/'summary.md').read_text()
assert 'Result: PASS' in text
match=re.search(r'WNS: ([\d.-]+) ns; TNS: ([\d.-]+) ns; WHS: ([\d.-]+) ns',text)
assert match and float(match[1])>=0 and float(match[2])==0 and float(match[3])>=0
for manifest in (json.loads((root/'forward_sources_sha256.json').read_text()),
                 json.loads((root/'forward_production.json').read_text())['sources_sha256'],
                 json.loads((build/'provenance.json').read_text())['source_sha256']):
    for name,digest in manifest.items():
        assert hashlib.sha256((repo/name).read_bytes()).hexdigest()==digest,name
review=json.loads((root/'cdc_forward/review.json').read_text())
assert review['dynamic_simulation']=='PASS' and review['raw_cdc15_warnings']==109
assert review['reviewed_request_bits']==72 and review['reviewed_response_bits']==37
assert review['unexpected_warning_rows']==0 and review['waivers_created'] is False
for path,digest in review['sha256'].items():
    if '/route_' in path.replace('\\','/'):
        assert hashlib.sha256((build/Path(path.replace('\\','/')).name).read_bytes()).hexdigest()==digest,path
bit_sha=hashlib.sha256((build/'fpga_top.bit').read_bytes()).hexdigest()
record=dict(passed=True,bitstream_sha256=bit_sha,wns_ns=float(match[1]),whs_ns=float(match[3]),
            scope='same-build source, routed timing and CDC gate; board lease released by sweep chat')
(root/'program_gate.json').write_text(json.dumps(record,indent=2)+'\n')
print(json.dumps(record),flush=True)
