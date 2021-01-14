# Copyright (c) 2020 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import paddle
import os
import paddle.nn as nn
import tagspace_net as net
import time
import logging
from utils import load_yaml, get_abs_model, save_model, load_model
from reader_dygraph import TagSpaceDataset
from paddle.io import DistributedBatchSampler, DataLoader
import argparse

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description='paddle-rec run')
    parser.add_argument("-m", "--config_yaml", type=str)
    args = parser.parse_args()
    args.config_yaml = get_abs_model(args.config_yaml)
    return args


def create_feeds(batch, text_len, neg_size):
    text = paddle.to_tensor(batch[0].numpy().astype('int64').reshape(-1,
                                                                     text_len))
    pos_tag = paddle.to_tensor(batch[1].numpy().astype('int64').reshape(-1, 1))
    neg_tag = paddle.to_tensor(batch[2].numpy().astype('int64').reshape(
        -1, neg_size))

    return [text, pos_tag, neg_tag]


def create_model(config):
    vocab_text_size = config.get("hyper_parameters.vocab_text_size")
    vocab_tag_size = config.get("hyper_parameters.vocab_tag_size")
    emb_dim = config.get("hyper_parameters.emb_dim")
    hid_dim = config.get("hyper_parameters.hid_dim")
    win_size = config.get("hyper_parameters.win_size")
    margin = config.get("hyper_parameters.margin")
    neg_size = config.get("hyper_parameters.neg_size")
    text_len = config.get("hyper_parameters.text_len")

    tagspace_model = net.TagspaceLayer(vocab_text_size, vocab_tag_size,
                                       emb_dim, hid_dim, win_size, margin,
                                       neg_size, text_len)
    return tagspace_model


def create_data_loader(dataset, place, config):
    batch_size = config.get('dygraph.batch_size_infer', None)
    loader = DataLoader(dataset, batch_size=batch_size, places=place)
    return loader


def get_acc(x, y, batch_size):
    less = paddle.cast(paddle.less_than(x, y), dtype='float32')
    label_ones = paddle.full(
        dtype='float32', shape=[batch_size, 1], fill_value=1.0)
    correct = paddle.sum(less)
    total = paddle.sum(label_ones)
    acc = paddle.divide(correct, total)
    return acc


def main(args):
    paddle.seed(12345)
    config = load_yaml(args.config_yaml)
    use_gpu = config.get("dygraph.use_gpu", False)
    test_data_dir = config.get("dygraph.test_data_dir", None)
    epochs = config.get("dygraph.epochs", None)
    print_interval = config.get("dygraph.print_interval", None)
    model_load_path = config.get("dygraph.infer_load_path",
                                 "increment_dygraph")
    start_epoch = config.get("dygraph.infer_start_epoch", -1)
    end_epoch = config.get("dygraph.infer_end_epoch", 1)
    batch_size = config.get('dygraph.batch_size_infer', None)
    margin = config.get('hyper_parameters.margin', 0.1)
    neg_size = config.get("hyper_parameters.neg_size")
    text_len = config.get("hyper_parameters.text_len")

    print("***********************************")
    logger.info(
        "use_gpu: {}, test_data_dir: {}, epochs: {}, print_interval: {}, model_load_path: {}".
        format(use_gpu, test_data_dir, epochs, print_interval,
               model_load_path))
    print("***********************************")

    place = paddle.set_device('gpu' if use_gpu else 'cpu')

    tagspace_model = create_model(config)
    # to do init model
    file_list = [
        os.path.join(test_data_dir, x) for x in os.listdir(test_data_dir)
    ]
    print("read data")
    dataset = TagSpaceDataset(file_list)
    test_dataloader = create_data_loader(dataset, place=place, config=config)

    epoch_begin = time.time()
    interval_begin = time.time()

    for epoch_id in range(start_epoch + 1, end_epoch):

        logger.info("load model epoch {}".format(epoch_id))
        model_path = os.path.join(model_load_path, str(epoch_id))
        load_model(model_path, tagspace_model)

        for batch_id, batch in enumerate(test_dataloader()):

            inputs = create_feeds(batch, text_len, neg_size)

            cos_pos, cos_neg = tagspace_model(inputs)
            acc = get_acc(cos_neg, cos_pos, batch_size)

            if batch_id % print_interval == 0:
                logger.info(
                    "infer epoch: {}, batch_id: {}, acc: {}, speed: {:.2f} ins/s".
                    format(epoch_id, batch_id,
                           acc.numpy(), print_interval * batch_size / (
                               time.time() - interval_begin)))
                interval_begin = time.time()

        logger.info("infer epoch: {} done, acc: {}, : epoch time{:.2f} s".
                    format(epoch_id, acc.numpy(), time.time() - epoch_begin))


if __name__ == '__main__':
    args = parse_args()
    main(args)