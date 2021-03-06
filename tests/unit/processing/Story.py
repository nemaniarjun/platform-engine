# -*- coding: utf-8 -*-
import collections
import time
from unittest import mock

from asyncy import Metrics
from asyncy.Containers import Containers
from asyncy.Exceptions import AsyncyError
from asyncy.Stories import Stories
from asyncy.constants import ContextConstants
from asyncy.processing import Lexicon, Story
from asyncy.utils import Dict

import pytest
from pytest import mark


def test_story_story(patch, app, logger):
    patch.init(Stories)
    story = Story.story(app, logger, 'story_name')
    Stories.__init__.assert_called_with(app, 'story_name', logger)
    assert isinstance(story, Stories)


@mark.asyncio
async def test_story_execute(patch, app, logger, story, async_mock):
    patch.object(Story, 'execute_line', new=async_mock(return_value=None))
    patch.object(Stories, 'first_line')
    story.prepare()
    await Story.execute(logger, story)
    assert Stories.first_line.call_count == 1
    logger.log.assert_called_with('story-execution', None)
    Story.execute_line.mock.assert_called_with(logger,
                                               story, Stories.first_line())


@mark.asyncio
async def test_story_execute_function(patch, logger, story, async_mock):
    line = {'function': 'my_super_awesome_function'}
    patch.many(story, ['function_line_by_name',
                       'context_for_function_call', 'set_context'])
    patch.object(Story, 'execute_block', new=async_mock())
    first_context = {'first': 'context'}

    story.context = first_context
    await Story.execute_function(logger, story, line)

    story.function_line_by_name.assert_called_with(line['function'])
    story.context_for_function_call \
        .assert_called_with(line, story.function_line_by_name())

    assert story.set_context.mock_calls == [
        mock.call(story.context_for_function_call()),
        mock.call(first_context)
    ]

    Story.execute_block.mock \
        .assert_called_with(logger, story, story.function_line_by_name())


@mark.asyncio
async def test_story_execute_line_unknown_method(logger, story):
    story.tree['1']['method'] = 'foo_method'
    with pytest.raises(AsyncyError):
        await Story.execute_line(logger, story, '1')


Method = collections.namedtuple('Method', 'name lexicon_name async_mock')


@mark.parametrize('method', [
    Method(name='if', lexicon_name='if_condition', async_mock=True),
    Method(name='elif', lexicon_name='if_condition', async_mock=True),
    Method(name='else', lexicon_name='if_condition', async_mock=True),
    Method(name='for', lexicon_name='for_loop', async_mock=True),
    Method(name='execute', lexicon_name='execute', async_mock=True),
    Method(name='set', lexicon_name='set', async_mock=True),
    Method(name='function', lexicon_name='function', async_mock=True),
    Method(name='when', lexicon_name='when', async_mock=True)
])
@mark.asyncio
async def test_story_execute_line_generic(patch, logger, story,
                                          async_mock, method):
    if method.async_mock:
        patch.object(Lexicon, method.lexicon_name, new=async_mock())
    else:
        patch.object(Lexicon, method.lexicon_name)

    patch.object(story, 'line', return_value={'method': method.name})
    patch.object(story, 'start_line')
    result = await Story.execute_line(logger, story, '1')

    mock = getattr(Lexicon, method.lexicon_name)
    if method.async_mock:
        mock = mock.mock

    mock.assert_called_with(logger, story, story.line.return_value)
    assert result == mock.return_value

    story.line.assert_called_with('1')
    story.start_line.assert_called_with('1')


@mark.asyncio
async def test_story_execute_line_call(patch, logger, story, async_mock):
    patch.object(Story, 'execute_function', new=async_mock())
    patch.object(story, 'line', return_value={'method': 'call'})
    patch.object(story, 'start_line')
    result = await Story.execute_line(logger, story, '1')

    Story.execute_function.mock.assert_called_with(logger, story,
                                                   story.line.return_value)
    assert result == Story.execute_function.mock.return_value

    story.line.assert_called_with('1')
    story.start_line.assert_called_with('1')


@mark.asyncio
async def test_story_execute_block(patch, logger, story, async_mock):
    story.tree = {
        '1': {'ln': '1', 'next': '2'},
        '2': {'ln': '2', 'next': '3', 'enter': '3', 'output': ['foo_client']},
        '3': {'ln': '3', 'next': '4', 'parent': '2'},
        '4': {'ln': '4', 'next': '5', 'parent': '2', 'enter': '5'},
        '5': {'ln': '5', 'next': '6', 'parent': '4'},
        '6': {'ln': '6', 'parent': '2'}
    }

    patch.object(Story, 'execute_line', new=async_mock())
    patch.object(story, 'next_block', return_value=story.tree['6'])

    line = story.line
    story.context = {
        ContextConstants.service_event: {'data': {'foo': 'bar'}}
    }

    def proxy_line(*args):
        return line(*args)

    patch.object(story, 'line', side_effect=proxy_line)

    await Story.execute_block(logger, story, story.tree['2'])

    assert story.context[ContextConstants.service_output] == 'foo_client'
    assert story.context['foo_client'] \
        == story.context[ContextConstants.service_event]['data']

    story.next_block.assert_called_with(story.tree['4'])
    assert [
        mock.call('3'),
        mock.call('4'),
        mock.call('5'),
        mock.call('6'),
        mock.call(None)
    ] == story.line.mock_calls


@mark.asyncio
async def test_story_run(patch, app, logger, async_mock, magic):
    patch.object(time, 'time')
    patch.object(Story, 'execute', new=async_mock())
    patch.object(Story, 'story')
    assert Metrics.story_run_total is not None
    assert Metrics.story_run_success is not None
    Metrics.story_run_total = magic()
    Metrics.story_run_success = magic()

    await Story.run(app, logger, 'story_name')
    Story.story.assert_called_with(app, logger, 'story_name')
    Story.story.return_value.prepare.assert_called_with(None)
    Story.execute.mock.assert_called_with(logger, Story.story())

    Metrics.story_run_total.labels.assert_called_with(app_id=app.app_id,
                                                      story_name='story_name')
    Metrics.story_run_total.labels.return_value.observe.assert_called_once()

    Metrics.story_run_success.labels \
        .assert_called_with(app_id=app.app_id, story_name='story_name')
    Metrics.story_run_success.labels.return_value.observe.assert_called_once()


@mark.asyncio
async def test_story_run_metrics_exc(patch, app, logger, async_mock, magic):
    patch.object(time, 'time')
    assert Metrics.story_run_total is not None
    assert Metrics.story_run_failure is not None
    Metrics.story_run_total = magic()
    Metrics.story_run_failure = magic()

    def exc():
        raise Exception()

    patch.object(Story, 'execute', new=async_mock(side_effect=exc))
    patch.object(Story, 'story')
    with pytest.raises(Exception):
        await Story.run(app, logger, 'story_name')
    Story.story.assert_called_with(app, logger, 'story_name')
    Story.story.return_value.prepare.assert_called_with(None)
    Story.execute.mock.assert_called_with(logger, Story.story())

    Metrics.story_run_total.labels.assert_called_with(app_id=app.app_id,
                                                      story_name='story_name')
    Metrics.story_run_total.labels.return_value.observe.assert_called_once()

    Metrics.story_run_failure.labels\
        .assert_called_with(app_id=app.app_id, story_name='story_name')
    Metrics.story_run_failure.labels.return_value.observe.assert_called_once()


@mark.asyncio
async def test_story_run_logger(patch, app, logger, async_mock):
    patch.object(Story, 'execute', new=async_mock())
    patch.object(Story, 'story')
    await Story.run(app, logger, 'story_name')
    assert logger.log.call_count == 2


def test_story_save_logger(logger, story):
    story.app_id = 'app_id'
    Story.save(logger, story, None)
    assert logger.log.call_count == 1


@mark.asyncio
async def test_story_run_with_id(patch, app, logger, async_mock):
    patch.object(Story, 'execute', new=async_mock())
    patch.object(Story, 'story')
    await Story.run(app, logger, 'story_name', story_id='story_id')


@mark.asyncio
async def test_story_run_prepare_function(patch, app, logger, async_mock):
    patch.object(Story, 'execute_function', new=async_mock())
    patch.object(Story, 'story')
    function_name = 'function_name'
    await Story.run(app, logger, 'story_name',
                    context='context', function_name=function_name)
    Story.story().prepare.assert_called_with('context')
    Story.story().function_line_by_name.assert_called_with(function_name)
    Story.execute_function.mock \
        .assert_called_with(logger, Story.story(),
                            Story.story().function_line_by_name())


@mark.asyncio
async def test_story_run_prepare_block(patch, app, logger, async_mock):
    patch.object(Story, 'execute_block', new=async_mock())
    patch.object(Story, 'story')
    block = '1'
    await Story.run(app, logger, 'story_name',
                    context='context', block=block)
    Story.story().prepare.assert_called_with('context')
    Story.story().line.assert_called_with(block)
    Story.execute_block.mock \
        .assert_called_with(logger, Story.story(),
                            Story.story().line())


@mark.asyncio
async def test_story_run_prepare(patch, app, logger, async_mock):
    patch.object(Story, 'execute', new=async_mock())
    patch.object(Story, 'story')
    await Story.run(app, logger, 'story_name',
                    context='context')
    Story.story().prepare.assert_called_with('context')
    Story.execute.mock \
        .assert_called_with(logger, Story.story())


@mark.asyncio
async def test_story_execute_does_not_wrap(patch, story, async_mock):
    def exc(*args):
        raise AsyncyError()

    patch.object(Lexicon, 'execute', new=async_mock(side_effect=exc))
    patch.object(story, 'line', return_value={'method': 'execute'})
    with pytest.raises(AsyncyError):
        await Story.execute_line(story.logger, story, '10')
