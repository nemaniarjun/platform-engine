# -*- coding: utf-8 -*-
import json
import urllib
import uuid
from collections import deque, namedtuple
from urllib import parse

from tornado.httpclient import AsyncHTTPClient

import ujson

from ..Containers import Containers
from ..Exceptions import AsyncyError
from ..Logger import Logger
from ..Types import StreamingService
from ..constants.ContextConstants import ContextConstants
from ..constants.LineConstants import LineConstants
from ..constants.ServiceConstants import ServiceConstants
from ..utils import Dict
from ..utils.HttpUtils import HttpUtils

InternalCommand = namedtuple('InternalCommand',
                             ['arguments', 'output_type', 'handler'])
InternalService = namedtuple('InternalService', ['commands'])

Service = namedtuple('Service', ['name'])
Command = namedtuple('Command', ['name'])
Event = namedtuple('Event', ['name'])


class Services:
    internal_services = {}
    logger = None

    @classmethod
    def set_logger(cls, logger: Logger):
        cls.logger = logger

    @classmethod
    def register_internal(cls, name, command, arguments, output_type, handler):
        service = cls.internal_services.get(name)
        if service is None:
            service = InternalService(commands={})
            cls.internal_services[name] = service

        service.commands[command] = InternalCommand(arguments=arguments,
                                                    output_type=output_type,
                                                    handler=handler)

    @classmethod
    def is_internal(cls, service, command):
        service = cls.internal_services.get(service)
        return service is not None \
            and service.commands.get(command) is not None

    @classmethod
    def last(cls, chain):
        return chain[len(chain) - 1]

    @classmethod
    async def execute(cls, story, line):
        chain = cls.resolve_chain(story, line)
        assert isinstance(chain, deque)
        assert isinstance(chain[0], Service)

        if cls.is_internal(chain[0].name, cls.last(chain).name):
            return await cls.execute_internal(story, line)
        else:
            return await cls.execute_external(story, line)

    @classmethod
    async def execute_internal(cls, story, line):
        service = cls.internal_services[line['service']]
        command = service.commands.get(line['command'])

        resolved_args = {}

        if command.arguments:
            for arg in command.arguments:
                actual = story.argument_by_name(line=line, argument_name=arg)
                resolved_args[arg] = actual

        return await command.handler(story=story, line=line,
                                     resolved_args=resolved_args)

    @classmethod
    async def execute_external(cls, story, line):
        """
        Executes external services via HTTP or a docker exec.
        :return: The output of docker exec or the HTTP call.

        Note: If the Content-Type of an output from an HTTP call
        is application/json, this method will parse the response
        and return a dict.
        """
        service = line[LineConstants.service]
        chain = cls.resolve_chain(story, line)
        command_conf = cls.get_command_conf(story, chain)
        await cls.start_container(story, line)
        if command_conf.get('format') is not None:
            return await Containers.exec(story.logger, story, line,
                                         service, line['command'])
        elif command_conf.get('http') is not None:
            if command_conf['http'].get('use_event_conn', False):
                return await cls.execute_inline(story, line,
                                                chain, command_conf)
            else:
                return await cls.execute_http(story, line, chain, command_conf)
        else:
            raise AsyncyError(message=f'Service {service}/{line["command"]} '
                              f'has neither http nor format sections!',
                              story=story, line=line)

    @classmethod
    def resolve_chain(cls, story, line):
        """
        resolve_chain returns a path (chain) to the current command.
        The command or service in 'line' might be the result of
        an event output, deeply nested. This method returns the
        path to the command described in line.

        Example:
        [Service(slack), Command(bot), Event(hears), Command(reply)]

        In most cases, the output would be:
        [Service(alpine), Command(echo)]

        The first entry in the chain will always be a concrete service,
        and the last entry will always be a command.
        """

        def get_owner(line):
            service = line[LineConstants.service]
            while True:
                parent = line.get(LineConstants.parent)
                assert parent is not None

                line = story.line(parent)
                output = line.get(LineConstants.output)
                if output is not None \
                        and len(output) == 1 \
                        and service == output[0]:
                    return line

        chain = deque()
        parent_line = line

        while True:
            service = parent_line[LineConstants.service]

            if parent_line[LineConstants.method] == 'when':
                chain.appendleft(Event(parent_line[LineConstants.command]))
            else:
                chain.appendleft(Command(parent_line[LineConstants.command]))

            # Is this a concrete service?
            resolved = story.app.services.get(service) is not None \
                or cls.is_internal(service, parent_line['command'])
            if resolved:
                chain.appendleft(Service(service))
                break

            assert parent_line.get(LineConstants.parent) is not None
            parent_line = get_owner(parent_line)
            assert parent_line is not None

        story.logger.debug(f'Chain resolved - {chain}')
        return chain

    @classmethod
    def get_command_conf(cls, story, chain):
        """
        Returns the conf for the command specified by 'chain'.
        """
        next = story.app.services
        for entry in chain:
            if isinstance(entry, Service):
                next = next[entry.name]['configuration']['actions']
            elif isinstance(entry, Command):
                next = next[entry.name]
            elif isinstance(entry, Event):
                next = next['events'][entry.name]['output']['actions']

        return next or {}

    @classmethod
    async def execute_inline(cls, story, line, chain, command_conf):
        assert isinstance(chain, deque)
        command = cls.last(chain)
        assert isinstance(command, Command)

        args = command_conf.get('arguments', {})
        body = {'command': command.name, 'data': {}}

        for arg in args:
            arg_val = story.argument_by_name(line, arg)
            body['data'][arg] = arg_val

        req = story.context[ContextConstants.server_request]
        req.write(ujson.dumps(body) + '\n')

        # HTTP hack
        io_loop = story.context[ContextConstants.server_io_loop]
        if chain[0].name == 'http' and command.name == 'finish':
            io_loop.add_callback(req.finish)
        # HTTP hack

    @classmethod
    async def execute_http(cls, story, line, chain, command_conf):
        assert isinstance(chain, deque)
        assert isinstance(chain[0], Service)
        hostname = await Containers.get_hostname(story, line, chain[0].name)
        args = command_conf.get('arguments')
        body = {}
        query_params = {}
        path_params = {}

        for arg in args:
            value = story.argument_by_name(line, arg)
            location = args[arg].get('in', 'requestBody')
            if location == 'query':
                query_params[arg] = value
            elif location == 'path':
                path_params[arg] = value
            elif location == 'requestBody':
                body[arg] = value
            else:
                raise AsyncyError(f'Invalid location for argument "{arg}" '
                                  f'specified: {location}')

        method = command_conf['http'].get('method', 'post')
        kwargs = {
            'method': method.upper()
        }

        if method.lower() == 'post':
            kwargs['body'] = json.dumps(body)
            kwargs['headers'] = {
                'Content-Type': 'application/json; charset=utf-8'
            }
        elif len(body) > 0:
            raise AsyncyError(
                message=f'Parameters found in the request body, '
                        f'but the method is {method}', story=story, line=line)

        port = command_conf['http'].get('port', 5000)
        path = HttpUtils.add_params_to_url(
            command_conf['http']['path'].format(**path_params), query_params)
        url = f'http://{hostname}:{port}{path}'

        story.logger.debug(f'Invoking service on {url} with payload {kwargs}')

        client = AsyncHTTPClient()
        response = await HttpUtils.fetch_with_retry(
            3, story.logger, url, client, kwargs)

        story.logger.debug(f'HTTP response code is {response.code}')
        if int(response.code / 100) == 2:
            content_type = response.headers.get('Content-Type')
            if content_type and 'application/json' in content_type:
                return ujson.loads(response.body)
            else:
                return response.body
        else:
            raise AsyncyError(message=f'Failed to invoke service!',
                              story=story, line=line)

    @classmethod
    async def start_container(cls, story, line):
        chain = cls.resolve_chain(story, line)
        assert isinstance(chain[0], Service)
        if chain[0].name == 'http':
            return StreamingService(
                name='http',
                command=line[LineConstants.command],
                container_name='gateway',
                hostname=story.app.config.ASYNCY_HTTP_GW_HOST)

        return await Containers.start(story, line)

    @classmethod
    def init(cls, logger):
        cls.logger = logger

    @classmethod
    async def when(cls, s: StreamingService, story, line: dict):
        service = line[LineConstants.service]
        command = line[LineConstants.command]
        conf = story.app.services[s.name][ServiceConstants.config]
        conf_event = Dict.find(
            conf, f'actions.{s.command}.events.{command}')

        port = Dict.find(conf_event, f'http.port', 80)
        subscribe_path = Dict.find(conf_event, 'http.subscribe.path')
        subscribe_method = Dict.find(conf_event,
                                     'http.subscribe.method', 'post')

        event_args = Dict.find(conf_event, 'arguments', {})

        data = {}
        for key in event_args:
            data[key] = story.argument_by_name(line, key)

        # HACK for http - send the DNS name of the app.
        if s.name == 'http':
            data['host'] = story.app.app_dns
        # END HACK for http.

        sub_url = f'http://{s.hostname}:{port}{subscribe_path}'

        story.logger.debug(f'Subscription URL - {sub_url}')

        engine = f'{story.app.config.ENGINE_HOST}:' \
                 f'{story.app.config.ENGINE_PORT}'

        query_params = urllib.parse.urlencode({
            'story': story.name,
            'block': line['ln'],
            'app': story.app.app_id
        })

        sub_id = str(uuid.uuid4())

        sub_body = {
            'endpoint': f'http://{engine}/story/event?{query_params}',
            'data': data,
            'event': command,
            'id': sub_id
        }

        body = {
            'sub_id': sub_id,
            'sub_url': sub_url,
            'sub_method': subscribe_method.upper(),
            'sub_body': sub_body,
            'pod_name': s.container_name,
            'app_id': story.app.app_id
        }

        kwargs = {
            'method': subscribe_method.upper(),
            'body': json.dumps(body),
            'headers': {
                'Content-Type': 'application/json; charset=utf-8'
            }
        }

        client = AsyncHTTPClient()
        story.logger.debug(f'Subscribing to {service} '
                           f'from {s.command} via Synapse...')

        url = f'http://{story.app.config.ASYNCY_SYNAPSE_HOST}:' \
              f'{story.app.config.ASYNCY_SYNAPSE_PORT}' \
              f'/subscribe'

        response = await HttpUtils.fetch_with_retry(3, story.logger, url,
                                                    client, kwargs)
        if int(response.code / 100) == 2:
            story.logger.info(f'Subscribed!')
            story.app.add_subscription(sub_id, s, command, body)
        else:
            raise AsyncyError(
                message=f'Failed to subscribe to {service} from '
                        f'{s.command} in {s.container_name}! '
                        f'http err={response.error}; code={response.code}',
                story=story, line=line)

    @classmethod
    def log_internal(cls):
        for key in cls.internal_services:
            commands = []
            for command in cls.internal_services[key].commands:
                commands.append(command)

            cls.logger.info(f'Discovered internal service {key} - {commands}')

    @classmethod
    async def remove_all(cls, app):
        await Containers.clean_app(app)
