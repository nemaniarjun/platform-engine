# -*- coding: utf-8 -*-
from playhouse import db_url

from .Config import Config
from .models import db


class Handler:
    """
    Handles various task-related things.
    """

    @staticmethod
    def init_db():
        """
        Init the database
        """
        db.init(db_url.parse(Config.get('database')))

    @staticmethod
    def build_story(story):
        """
        Build a storytree, given a story
        """
        app_identifier = Config.get('github.app_identifier')
        pem_path = Config.get('github.pem_path')
        story.provider(app_identifier, pem_path)
        story.build_tree()

    @staticmethod
    def run(line, context):
        """
        Run the story
        """
        return line['ln']
