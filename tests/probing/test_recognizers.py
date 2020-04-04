from unittest import mock
from dataclasses import asdict
from dataclasses import astuple

import numpy as np
import pytest
import tensorflow as tf

import aspect_based_sentiment_analysis as absa
from aspect_based_sentiment_analysis import AspectSpan
from aspect_based_sentiment_analysis import utils
from aspect_based_sentiment_analysis.probing import AttentionPatternRecognizer
tf.random.set_seed(1)


@pytest.fixture
@utils.cache_fixture
def inputs(request):  # The cache function uses the `request` parameter.
    nlp = absa.load('absa/classifier-rest-0.1',
                    output_attentions=True,
                    output_hidden_states=True)
    text = ("We are great fans of Slack, but we wish the subscriptions "
            "were more accessible to small startups.")
    aspect = 'slack'
    aspect_spans = nlp.preprocess(pairs=[(text, aspect)])
    input_batch = nlp.batch(aspect_spans)
    output_batch = nlp.predict(input_batch)
    outputs = [tensor[0] for tensor in astuple(output_batch)]

    # Covert AspectSpan and EagerTensor's to the native python objects
    # and facilitate the serialization process.
    aspect_span, *_ = aspect_spans
    raw_aspect_span = asdict(aspect_span)
    raw_model_outputs = [tensor.numpy().tolist() for tensor in outputs]
    return raw_aspect_span, raw_model_outputs


def test_integration(inputs):
    aspect_span, outputs = inputs
    aspect_span = AspectSpan(**aspect_span)
    outputs = [tf.convert_to_tensor(o) for o in outputs]
    scores, *details = outputs

    recognizer = AttentionPatternRecognizer()
    aspect_repr, patterns = recognizer(aspect_span, *details)

    index = np.argmax(np.abs(aspect_repr.look_at))
    assert aspect_repr.tokens[index] == 'slack'
    tokens = np.array(aspect_repr.tokens)
    look_at = np.array(aspect_repr.look_at)
    most_important = tokens[look_at != 0].tolist()
    assert most_important == ['great', 'fans', 'of', 'slack', '.']

    come_from = np.array(aspect_repr.come_from)
    most_important = tokens[come_from != 0].tolist()
    assert most_important == ['great', 'fans', 'accessible', 'small']

    assert len(patterns) == 8
    pattern_1, *_ = patterns
    assert pattern_1.impact == 1
    weights = np.round(pattern_1.weights, decimals=2).tolist()
    assert weights[:6] == [0.14, 0.06, 0.24, 0.87, 0.09, 1.0]
    assert np.allclose(weights[6:], 0)


def test_get_interest():
    recognizer = AttentionPatternRecognizer(percentile_mask=80)
    attentions = tf.random.normal([10, 10, 3, 3])
    attention_grads = tf.random.normal([10, 10, 3, 3])
    # Calculate partial results here by the hand.
    raw_interest = tf.reduce_sum(attentions * attention_grads, axis=(0, 1))

    # Check how test data looks like. We use two times the `tolist` method
    # due to the floating point issues.
    value = np.round(raw_interest.numpy().tolist(), decimals=2).tolist()
    assert value == [[-0.12, -6.08, -9.42],
                     [-11.29, -7.93, 7.31],
                     [2.78, 9.1, 12.83]]

    interest = recognizer.get_interest(attentions, attention_grads)
    value = np.round(interest.tolist(), decimals=2).tolist()
    assert value == [[0.0, 0.0, -9.42],
                     [-11.29, -7.93, 7.31],
                     [0.0, 9.1, 12.83]]

    information = np.sum(np.abs(interest))
    total_information = np.sum(np.abs(raw_interest))
    ratio = information / total_information
    assert np.isclose(ratio, 0.87, atol=0.01)


def test_get_patterns():
    def get_ratio(patterns):
        information = np.sum([p.weights for p in patterns])
        weights = interest[[1, 2, 3, 4], :][:, [1, 2, 3, 4]]
        weights = weights / weights.max()
        return information / np.sum(weights)

    recognizer = AttentionPatternRecognizer(percentile_information=50)
    aspect_span = mock.MagicMock()
    aspect_span.tokens = ['CLS', 'this', 'is', 'a', 'test', 'SEP', 'test',
                          'SEP']
    aspect_span.text_tokens = ['this', 'is', 'a', 'test']
    interest = np.arange(64).reshape([8, 8])

    patterns = recognizer.get_patterns(aspect_span, interest)
    pattern_1, pattern_2 = patterns
    assert pattern_1.tokens == pattern_2.tokens == ['this', 'is', 'a', 'test']
    assert np.abs(pattern_1.impact) == 1
    assert pattern_1.weights == [0.917, 0.944, 0.972, 1.0]
    assert np.abs(pattern_2.impact) == 0.75
    assert pattern_2.weights == [0.694, 0.722, 0.75, 0.778]
    assert get_ratio(patterns) > 0.5

    recognizer = AttentionPatternRecognizer(percentile_information=80)
    patterns = recognizer.get_patterns(aspect_span, interest)
    assert len(patterns) == 3
    assert 0.9 > get_ratio(patterns) > 0.8


def test_get_aspect_representation():
    recognizer = AttentionPatternRecognizer()
    aspect_repr = mock.MagicMock()
    aspect_repr.tokens = ['CLS', 'this', 'is', 'a', 'test', 'SEP', 'test',
                          'SEP']
    aspect_repr.text_tokens = ['this', 'is', 'a', 'test']
    interest = np.arange(64).reshape([8, 8])

    aspect_pattern = recognizer.get_aspect_representation(aspect_repr, interest)
    assert aspect_pattern.tokens == ['this', 'is', 'a', 'test']
    assert aspect_pattern.come_from == [0.942, 0.962, 0.981, 1.0]
    assert aspect_pattern.look_at == [0.368, 0.579, 0.789, 1.0]


def test_mask_noise():
    recognizer = AttentionPatternRecognizer  # Static method
    interest = np.array([[1, -1, 5, 6],
                         [2, -1, 2, 1],
                         [0, -1, -5, 1]])

    clean_interest = recognizer.mask_noise(interest, percentile=70)
    assert clean_interest.tolist() == [[0, 0, 5, 6],
                                       [2, 0, 2, 0],
                                       [0, 0, -5, 0]]
    magnitude = lambda x: np.sum(np.abs(x))
    ratio = magnitude(clean_interest) / magnitude(interest)
    assert round(ratio, 2) == 0.77

    clean_interest = recognizer.mask_noise(interest, percentile=20)
    ratio = magnitude(clean_interest) / magnitude(interest)
    assert round(ratio, 2) == 0.23


def test_get_indices():
    recognizer = AttentionPatternRecognizer  # Static method
    aspect_span = mock.MagicMock()
    aspect_span.tokens = ['CLS', 'this', 'is', 'a', 'test', 'SEP', 'test',
                          'SEP']
    cls_id, text_ids, aspect_id = recognizer.get_indices(aspect_span)
    assert cls_id == 0
    assert text_ids == [1, 2, 3, 4]
    assert aspect_id == 6


def test_normalize():
    recognizer = AttentionPatternRecognizer  # Static method
    interest = np.array([[1, -1, 5, 6],
                         [2, -1, 2, 1],
                         [0, -1, -5, 1]])
    normalized = np.round(recognizer.normalize(interest), decimals=2).tolist()
    assert normalized == [[0.17, -0.17, 0.83, 1.0],
                          [0.33, -0.17, 0.33, 0.17],
                          [0.0, -0.17, -0.83, 0.17]]


def test_get_key_mixtures():
    recognizer = AttentionPatternRecognizer  # Static method
    impacts = np.array([1, 2, 3])
    mixtures = np.arange(12).reshape(3, 4)

    key_impacts, key_mixtures = recognizer.get_key_mixtures(
        impacts, mixtures, percentile=80)
    assert len(key_impacts) == len(key_mixtures) == 2
    assert key_impacts == (3, 2)
    mixtures_1, mixtures_2 = key_mixtures
    assert mixtures_1 == [8, 9, 10, 11]
    assert mixtures_2 == [4, 5, 6, 7]

    key_impacts, key_mixtures = recognizer.get_key_mixtures(
        impacts, mixtures, percentile=10)
    assert len(key_impacts) == 1
    assert key_impacts == (3,)


def test_construct_patterns():
    recognizer = AttentionPatternRecognizer  # Static method
    aspect_span = mock.MagicMock()
    impacts = [1, 2, 3]
    mixtures = np.arange(12).reshape(3, 4).tolist()
    patterns = recognizer.construct_patterns(aspect_span, impacts, mixtures)
    assert len(patterns) == 3
    pattern_1, pattern_2, pattern_3 = patterns
    assert pattern_2.impact == 2
    assert pattern_2.weights == [4, 5, 6, 7]
