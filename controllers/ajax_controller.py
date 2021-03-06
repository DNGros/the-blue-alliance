import os
import urllib2
import json
import time

from base_controller import CacheableHandler, LoggedInHandler
from google.appengine.api import memcache
from google.appengine.ext import ndb
from google.appengine.ext.webapp import template
from helpers.model_to_dict import ModelToDict
from helpers.mytba_helper import MyTBAHelper
from models.account import Account
from models.event import Event
from models.favorite import Favorite
from models.sitevar import Sitevar
from models.typeahead_entry import TypeaheadEntry


class AccountFavoritesHandler(LoggedInHandler):
    """
    For getting an account's favorites
    """
    def get(self, model_type):
        if not self.user_bundle.user:
            self.response.set_status(401)
            return

        favorites = Favorite.query(
            Favorite.model_type==int(model_type),
            ancestor=ndb.Key(Account, self.user_bundle.user.user_id())).fetch()
        self.response.out.write(json.dumps([ModelToDict.favoriteConverter(fav) for fav in favorites]))


class AccountFavoritesAddHandler(LoggedInHandler):
    """
    For adding an account's favorites
    """
    def post(self):
        if not self.user_bundle.user:
            self.response.set_status(401)
            return

        model_type = int(self.request.get("model_type"))
        model_key = self.request.get("model_key")
        user_id = self.user_bundle.user.user_id()

        fav = Favorite(
            parent=ndb.Key(Account, user_id),
            user_id=user_id,
            model_key=model_key,
            model_type=model_type
        )
        MyTBAHelper.add_favorite(fav)


class AccountFavoritesDeleteHandler(LoggedInHandler):
    """
    For deleting an account's favorites
    """
    def post(self):
        if not self.user_bundle.user:
            self.response.set_status(401)
            return

        model_key = self.request.get("model_key")
        user_id = self.user_bundle.user.user_id()

        MyTBAHelper.remove_favorite(user_id, model_key)


class LiveEventHandler(CacheableHandler):
    """
    Returns the necessary details to render live components
    Uses timestamp for aggressive caching
    """
    CACHE_VERSION = 1
    CACHE_KEY_FORMAT = "live-event:{}:{}"  # (event_key, timestamp)
    CACHE_HEADER_LENGTH = 60 * 10

    def __init__(self, *args, **kw):
        super(LiveEventHandler, self).__init__(*args, **kw)
        self._cache_expiration = self.CACHE_HEADER_LENGTH

    def get(self, event_key, timestamp):
        if int(timestamp) > time.time():
            self.abort(404)
        self._partial_cache_key = self.CACHE_KEY_FORMAT.format(event_key, timestamp)
        super(LiveEventHandler, self).get(event_key, timestamp)

    def _render(self, event_key, timestamp):
        self.response.headers['content-type'] = 'application/json; charset="utf-8"'

        event = Event.get_by_id(event_key)

        matches = []
        for match in event.matches:
            matches.append({
                'name': match.short_name,
                'alliances': match.alliances,
                'order': match.play_order,
                'time_str': match.time_string,
            })

        event_dict = {
#             'rankings': event.rankings,
#             'matchstats': event.matchstats,
            'matches': matches,
        }

        return json.dumps(event_dict)


class TypeaheadHandler(CacheableHandler):
    """
    Currently just returns a list of all teams and events
    Needs to be optimized at some point.
    Tried a trie but the datastructure was too big to
    fit into memcache efficiently
    """
    CACHE_VERSION = 2
    CACHE_KEY_FORMAT = "typeahead_entries:{}"  # (search_key)
    CACHE_HEADER_LENGTH = 60 * 60 * 24

    def __init__(self, *args, **kw):
        super(TypeaheadHandler, self).__init__(*args, **kw)
        self._cache_expiration = self.CACHE_HEADER_LENGTH

    def get(self, search_key):
        search_key = urllib2.unquote(search_key)
        self._partial_cache_key = self.CACHE_KEY_FORMAT.format(search_key)
        super(TypeaheadHandler, self).get(search_key)

    def _render(self, search_key):
        self.response.headers['content-type'] = 'application/json; charset="utf-8"'

        entry = TypeaheadEntry.get_by_id(search_key)
        if entry is None:
            return '[]'
        else:
            self._last_modified = entry.updated
            return entry.data_json


class WebcastHandler(CacheableHandler):
    """
    Returns the HTML necessary to generate the webcast embed for a given event
    """
    CACHE_VERSION = 1
    CACHE_KEY_FORMAT = "webcast_{}_{}"  # (event_key)
    CACHE_HEADER_LENGTH = 60 * 5

    def __init__(self, *args, **kw):
        super(WebcastHandler, self).__init__(*args, **kw)
        self._cache_expiration = 60 * 60 * 24

    def get(self, event_key, webcast_number):
        self._partial_cache_key = self.CACHE_KEY_FORMAT.format(event_key, webcast_number)
        super(WebcastHandler, self).get(event_key, webcast_number)

    def _render(self, event_key, webcast_number):
        self.response.headers.add_header('content-type', 'application/json', charset='utf-8')

        output = {}
        if not webcast_number.isdigit():
            return json.dumps(output)
        webcast_number = int(webcast_number) - 1

        event = Event.get_by_id(event_key)
        if event and event.webcast:
            webcast = event.webcast[webcast_number]
            if 'type' in webcast and 'channel' in webcast:
                output['player'] = self._renderPlayer(webcast)
        else:
            special_webcasts_future = Sitevar.get_by_id_async('gameday.special_webcasts')
            special_webcasts = special_webcasts_future.get_result()
            if special_webcasts:
                special_webcasts = special_webcasts.contents
            else:
                special_webcasts = {}
            if event_key in special_webcasts:
                webcast = special_webcasts[event_key]
                if 'type' in webcast and 'channel' in webcast:
                    output['player'] = self._renderPlayer(webcast)

        return json.dumps(output)

    def _renderPlayer(self, webcast):
        webcast_type = webcast['type']
        template_values = {'webcast': webcast}

        path = os.path.join(os.path.dirname(__file__), '../templates/webcast/' + webcast_type + '.html')
        return template.render(path, template_values)

    def memcacheFlush(self, event_key):
        keys = [self._render_cache_key(self.CACHE_KEY_FORMAT.format(event_key, n)) for n in range(10)]
        memcache.delete_multi(keys)
        return keys
