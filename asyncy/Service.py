# -*- coding: utf-8 -*-
import asyncio
import os
import signal
import socket

import click

import prometheus_client

import tornado
from tornado import web

from asyncy.Apps import Apps
from . import Version
from .App import App
from .Config import Config
from .Logger import Logger
from .http_handlers.StoryEventHandler import StoryEventHandler
from .processing.Services import Services
from .processing.internal import File, Http, Log

_ONE_DAY_IN_SECONDS = 60 * 60 * 24

config = Config()
server = None
logger = Logger(config)
logger.start()


class Service:

    @click.group()
    def main():
        pass

    @staticmethod
    @main.command()
    @click.option('--port',
                  help='Set the port on which the HTTP server binds to',
                  default=os.getenv('PORT', '8084'))
    @click.option('--prometheus_port',
                  help='Set the port on which metrics are exposed',
                  default=os.getenv('METRICS_PORT', '8085'))
    @click.option('--debug',
                  help='Sets the engine into debug mode',
                  default=False)
    def start(port, debug, prometheus_port):
        global server

        Services.set_logger(logger)

        # Init internal services.
        File.init()
        Log.init()
        Http.init()
        Services.log_internal()

        logger.log('service-init', Version.version)
        signal.signal(signal.SIGTERM, Service.sig_handler)
        signal.signal(signal.SIGINT, Service.sig_handler)

        web_app = tornado.web.Application([
            (r'/story/event', StoryEventHandler)
        ], debug=debug)

        config.engine_host = socket.gethostname()
        config.engine_port = port

        server = tornado.httpserver.HTTPServer(web_app)
        server.listen(port)

        prometheus_client.start_http_server(port=int(prometheus_port))

        logger.log('http-init', port)

        loop = asyncio.get_event_loop()
        loop.create_task(Apps.init_all(config, logger))

        tornado.ioloop.IOLoop.current().start()

        logger.log_raw('info', 'Shutdown complete!')

    @staticmethod
    def sig_handler(*args, **kwargs):
        logger.log_raw('info', f'Signal {args[0]} received.')
        tornado.ioloop.IOLoop.instance().add_callback(Service.shutdown)

    @classmethod
    async def shutdown_app(cls):
        logger.log_raw('info', 'Unregistering with the gateway...')
        await app.destroy()

        io_loop = tornado.ioloop.IOLoop.instance()
        io_loop.stop()
        loop = asyncio.get_event_loop()
        loop.stop()

    @classmethod
    def shutdown(cls):
        logger.log_raw('info', 'Shutting down...')

        server.stop()
        loop = asyncio.get_event_loop()
        loop.create_task(cls.shutdown_app())
