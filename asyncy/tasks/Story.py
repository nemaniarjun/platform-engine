# -*- coding: utf-8 -*-
import time

from .Handler import Handler
from ..models import Applications, Stories, db


class Story:

    @staticmethod
    def save(config, app, story, environment, start):
        """
        Saves the narration and the results for each line.
        """
        mongo = Handler.init_mongo(config.mongo)
        mongo_story = mongo.story(app.id, story.id)
        narration = mongo.narration(mongo_story, app.initial_data, environment,
                                    story.version, start,
                                    time.time())
        mongo.lines(narration, story.results)

    @staticmethod
    def execute(config, logger, app, story, environment):
        line_number = '1'
        while line_number:
            line_number = Handler.run(logger, line_number, story, environment)
            if line_number:
                if line_number.endswith('.story'):
                    line_number = Story.run(config, logger, app.id,
                                            line_number)

    @classmethod
    def run(cls, config, logger, app_id, story_name, *, story_id=None,
            app=None, parent_story=None):
        logger.log('task-start', app_id, story_name, story_id)
        db.from_url(config.database)
        if app is None:
            app = Applications.get(Applications.id == app_id)
        story = app.get_story(story_name)
        story.build(app, config.github['app_identifier'],
                    config.github['pem_path'], parent=parent_story)
        environment = Handler.make_environment(story, app)
        start = time.time()
        cls.execute(config, logger, app, story, environment)
        cls.save(config, app, story, environment, start)