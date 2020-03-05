import os
import logging
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from typing import Callable
from typing import Dict
from typing import List

import numpy as np
import tensorflow as tf
import transformers

logger = logging.getLogger('absa.callbacks')


class Callback(ABC):

    def on_epoch_begin(self, epoch: int):
        """ """

    def on_epoch_end(self, epoch: int):
        """ """

    def on_train_batch_end(self, i: int, batch, *train_step_outputs):
        """ """

    def on_test_batch_end(self, i: int, batch, *test_step_outputs):
        """ """


@dataclass
class CallbackList(Callback):
    callbacks: List[Callback]

    def on_epoch_begin(self, epoch: int):
        for callback in self.callbacks:
            callback.on_epoch_begin(epoch)

    def on_epoch_end(self, epoch: int):
        for callback in self.callbacks:
            callback.on_epoch_end(epoch)

    def on_train_batch_end(self, *args):
        for callback in self.callbacks:
            callback.on_train_batch_end(*args)

    def on_test_batch_end(self, *args):
        for callback in self.callbacks:
            callback.on_test_batch_end(*args)


@dataclass
class Logger(Callback):
    level: int = 20
    file_path: str = None
    msg_format: str = '%(asctime)s [%(levelname)-6s] [%(name)-10s] %(message)s'

    def __post_init__(self):
        logger.setLevel(self.level)
        logger.propagate = False
        formatter = logging.Formatter(self.msg_format, datefmt='%Y-%m-%d %H:%M:%S')
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        # Handle all messages from the logger (not set the handler level)
        logger.addHandler(console)
        if self.file_path:
            file_handler = logging.FileHandler(self.file_path, mode='w')
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)


@dataclass
class History(Callback, ABC):
    name: str
    epoch: int = 0
    verbose: bool = True
    metric: Callable[[], tf.keras.metrics.Metric] = tf.keras.metrics.Mean
    train: Dict = field(default_factory=dict)
    test: Dict = field(default_factory=dict)
    train_details: Dict = field(default_factory=dict)
    test_details: Dict = field(default_factory=dict)

    def __post_init__(self):
        self.train_metric = self.metric()
        self.test_metric = self.metric()

    def on_epoch_begin(self, epoch: int):
        """ Resets all of the metric state variables. """
        self.epoch = epoch
        self.train_details[epoch] = []
        self.test_details[epoch] = []
        self.train_metric.reset_states()
        self.test_metric.reset_states()

    def on_epoch_end(self, epoch: int):
        self.train[epoch] = self.train_metric.result().numpy()
        self.test[epoch] = self.test_metric.result().numpy()
        if self.verbose:
            message = f'Epoch {epoch:3d} {self.name}:    ' \
                      f'Average Train {self.train[epoch]:.5f}    ' \
                      f'Average Test {self.test[epoch]:.5f}'
            logger.info(message)

    @abstractmethod
    def on_train_batch_end(self, i: int, batch, *train_step_outputs):
        """ """

    @abstractmethod
    def on_test_batch_end(self, i: int, batch, *test_step_outputs):
        """ """


@dataclass
class LossHistory(History):
    metric = tf.keras.metrics.Mean
    name: str = 'Loss'

    def on_train_batch_end(self, i: int, batch, *train_step_outputs):
        loss_value, *model_outputs = train_step_outputs
        self.train_metric(loss_value)
        self.train_details[self.epoch].extend(loss_value.numpy())

    def on_test_batch_end(self, i: int, batch, *test_step_outputs):
        loss_value, *model_outputs = test_step_outputs
        self.test_metric(loss_value)
        self.test_details[self.epoch].extend(loss_value.numpy())


@dataclass
class ModelCheckpoint(Callback):
    model: transformers.TFPreTrainedModel
    loss_history: LossHistory
    home_dir: str = 'checkpoints'
    best_result: float = np.inf
    best_model_dir: str = ''
    verbose: bool = True

    def __post_init__(self):
        """ Create the directory for saving checkpoints. """
        if not os.path.isdir(self.home_dir):
            abs_path = os.path.abspath(self.home_dir)
            text = f'Make a checkpoint directory: {abs_path}'
            logger.info(text)
            os.makedirs(self.home_dir)

    def on_epoch_end(self, epoch: int):
        """ Pass the `ModelCheckpoint` callback after the `LossHistory`. """
        result = self.loss_history.test[epoch]
        if result < self.best_result:
            name = f'epoch-{epoch:02d}-{result:.2f}'
            model_dir = os.path.join(self.home_dir, name)
            os.mkdir(model_dir)
            self.model.save_pretrained(model_dir)
            self.best_result = result
            self.best_model_dir = model_dir

            text = f'The new best result: {result:.2f}'
            if self.verbose:
                logger.info(text)
