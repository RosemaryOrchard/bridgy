"""Unit test utilities.
"""

__author__ = ['Ryan Barrett <bridgy@ryanb.org>']

import base64
import collections
import datetime
import json
import logging
import urlparse

from activitystreams import source as as_source
from models import Response, Source
from tasks import Poll, Propagate
import util
from webutil import testutil

from google.appengine.datastore import datastore_stub_util
from google.appengine.ext import db


def get_task_params(task):
  """Parses a task's POST body and returns the query params in a dict.
  """
  params = urlparse.parse_qs(base64.b64decode(task['body']))
  params = dict((key, val[0]) for key, val in params.items())
  return params


class FakeBase(db.Model):
  """Not thread safe.
  """

  key_name_counter = 1
  # class attr. maps (string source key, type name) to object or list.
  # can't use instance attrs because code fetches FakeSource instances from the
  # datastore.
  data = {}

  def _set(self, name, val):
    FakeBase.data[(str(self.key()), name)] = val

  def _get(self, name):
    return FakeBase.data.get((str(self.key()), name))

  @classmethod
  def new(cls, handler, **props):
    if 'url' not in props:
      props['url'] = 'http://fake/url'
    if 'name' not in props:
      props['name'] = 'fake'
    inst = cls(key_name=str(cls.key_name_counter), **props)
    cls.key_name_counter += 1
    return inst


class FakeAsSource(FakeBase, as_source.Source):

  def set_like(self, val):
    self._set('like', val)

  def get_like(self, user_id, activity_id):
    got = self._get('like')
    return got

  def set_repost(self, val):
    self._set('repost', val)

  def get_repost(self, user_id, activity_id):
    return self._get('repost')


class FakeSource(FakeBase, Source):
  DISPLAY_NAME = 'FakeSource'
  SHORT_NAME = 'fake'

  as_source = FakeAsSource()

  def __init__(self, *args, **kwargs):
    super(FakeSource, self).__init__(*args, **kwargs)
    if not FakeSource.as_source.is_saved():
      FakeSource.as_source.save()

  def set_activities(self, val):
    self._set('activities', val)

  def get_activities(self, fetch_replies=None, count=None):
    return self._get('activities')

  def get_post(self, id):
    return self.get_activities()[int(id)]

  def set_comment(self, val):
    self._set('comment', val)

  def get_comment(self, comment_id, activity_id=None):
    comment = self._get('comment')
    return comment if comment else super(FakeSource, self).get_comment(comment_id)


class HandlerTest(testutil.HandlerTest):
  """Base test class.
  """
  def setUp(self):
    super(HandlerTest, self).setUp()
    self.handler = util.Handler(self.request, self.response)
    # TODO: remove this and don't depend on consistent global queries
    self.testbed.init_datastore_v3_stub(consistency_policy=None)


class ModelsTest(HandlerTest):
  """Sets up some test sources and responses.

  Attributes:
    sources: list of FakeSource
    responses: list of unsaved Response
    taskqueue_stub: the app engine task queue api proxy stub
  """

  def setUp(self):
    super(ModelsTest, self).setUp()

    self.sources = [FakeSource.new(None), FakeSource.new(None)]
    for entity in self.sources:
      entity.save()

    self.activities = [{
      'id': 'tag:source.com,2013:000',
      'object': {
        'objectType': 'note',
        'id': 'tag:source.com,2013:000',
        'url': 'http://source/post/url',
        'content': 'foo http://target1/post/url bar',
        'replies': {
          'items': [{
              'objectType': 'comment',
              'id': 'tag:source.com,2013:1_2_%s' % id,
              'url': 'http://source/comment/url',
              'content': 'foo bar',
              }],
          'totalItems': 1,
          },
        }
      } for id in ('a', 'b', 'c')]
    self.sources[0].set_activities(self.activities)

    self.responses = []
    for activity in self.activities:
      comment = activity['object']['replies']['items'][0]
      self.responses.append(Response(key_name=comment['id'],
                                     type='comment',
                                     activity_json=json.dumps(activity),
                                     response_json=json.dumps(comment),
                                     source=self.sources[0],
                                     unsent=['http://target1/post/url']))
