"""Independently audit full-set CSV predictions and metrics without PyTorch."""
import argparse
import csv
import gzip
import json
import math
from pathlib import Path
import struct

ROOT=Path(__file__).resolve().parents[2]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,default=ROOT/'verification/fpga/results/2026-11-04-int4-v2/full_dataset_accuracy')
    args=p.parse_args()
    baseline={r['model']:r for r in json.loads((ROOT/'docs/validation/int4_v2/zoo_eval.json').read_text())}
    expected={'ecg','fashion_cnn','gtsrb_l','har','kws_l','kws_m','mnist_l'}
    actual={p.parent.name for p in args.evidence.glob('*/report.json')}
    assert actual==expected,(actual,expected)
    total=0
    for name in sorted(expected):
        report=json.loads((args.evidence/name/'report.json').read_text())
        reference=baseline[name];classes=len(reference['recall'])
        assert report['passed'] and report['published_metrics_equal']
        assert report['golden_input_and_label_matches']==report['golden_output_matches']==16
        confusion=[[0]*classes for _ in range(classes)];count=correct=0
        with gzip.open(args.evidence/name/'predictions.csv.gz','rt',newline='') as f:
            for row in csv.DictReader(f):
                index,label,prediction,matched=[int(row[k]) for k in ('test_index','label','prediction','correct')]
                assert index==count and 0<=label<classes and 0<=prediction<classes
                assert matched==int(label==prediction)
                confusion[label][prediction]+=1;count+=1;correct+=matched
        assert count==report['test_samples']==reference['test_samples']
        assert correct==report['correct'] and confusion==report['confusion']
        counts=[sum(row) for row in confusion]
        recall=[confusion[c][c]/max(1,counts[c]) for c in range(classes)]
        accuracy=struct.unpack('f',struct.pack('f',correct/count))[0]
        assert counts==report['class_counts']
        assert recall==report['recall']==reference['recall']
        assert accuracy==report['int4_test']==reference['int4_test']
        assert math.fsum(recall)/classes==report['int4_test_balanced']==reference['int4_test_balanced']
        total+=count
        print('PASS',name,correct,'/',count,f'{100*accuracy:.4f}%')
    assert total==67245
    print('PASS: 7 complete task test sets, 67245 model-sample evaluations; CSV-derived metrics match published values exactly')


if __name__=='__main__':main()
