"""数据划分、平衡、重采样及前处理冻结契约回归。"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'train'))
from dataset import SpeechCommandsDataset, BACKGROUND_SPLIT  # noqa: E402


def speech_paths(dataset):
    return {os.path.normcase(item[0]) for item in dataset.manifest if len(item) == 2}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    train = SpeechCommandsDataset(args.data_dir, 'train', seed=0, augment=False,
                                  task='six_class')
    val = SpeechCommandsDataset(args.data_dir, 'validation', seed=0, augment=False,
                                task='six_class')
    test = SpeechCommandsDataset(args.data_dir, 'test', seed=0, augment=False,
                                 task='six_class')

    assert train.class_counts() == [3228, 3130, 2948, 3134, 3110, 3110]
    assert val.class_counts() == [350] * 6
    assert test.class_counts() == [405] * 6
    train_paths, val_paths, test_paths = map(speech_paths, (train, val, test))
    assert train_paths.isdisjoint(val_paths)
    assert train_paths.isdisjoint(test_paths)
    assert val_paths.isdisjoint(test_paths)
    background_sets = [set(BACKGROUND_SPLIT[name]) for name in ('train', 'validation', 'test')]
    assert background_sets[0].isdisjoint(background_sets[1])
    assert background_sets[0].isdisjoint(background_sets[2])
    assert background_sets[1].isdisjoint(background_sets[2])

    original = list(train.manifest)
    train.set_epoch(1)
    assert train.manifest != original
    replay = SpeechCommandsDataset(
        args.data_dir, 'train', seed=0, augment=False, task='six_class'
    )
    replay.set_epoch(1)
    assert train.manifest == replay.manifest
    feature, _ = val[0]
    assert tuple(feature.shape) == (1, 20, 16)

    four_train = SpeechCommandsDataset(
        args.data_dir, 'train', seed=0, augment=False, task='four_class'
    )
    four_val = SpeechCommandsDataset(
        args.data_dir, 'validation', seed=0, augment=False, task='four_class'
    )
    four_test = SpeechCommandsDataset(
        args.data_dir, 'test', seed=0, augment=False, task='four_class'
    )
    assert four_train.class_counts() == [3228, 3130, 2948, 3134]
    assert four_val.class_counts() == [350] * 4
    assert four_test.class_counts() == [405] * 4
    assert all(len(item) == 2 for item in four_train.manifest)
    print('PASS: dataset split/balance/resampling/preprocess contract')


if __name__ == '__main__':
    main()
