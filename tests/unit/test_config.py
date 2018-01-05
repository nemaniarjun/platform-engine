# -*- coding: utf-8 -*-
from aratrum import Aratrum

from evenflow.Config import Config


def test_config():
    database_url = 'postgresql://postgres:postgres@localhost:5432/database'
    broker_url = 'amqp://:@localhost:5672/'
    assert Config.default['database'] == database_url
    assert Config.default['broker'] == broker_url
    assert Config.default['github']['pem_path'] == 'github.pem'
    assert Config.default['github']['app_identifier'] == '123456789'


def test_config_inheritance():
    assert issubclass(Config, Aratrum)
