#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


from datetime import datetime

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile
from models import ProfileMiniForm
from models import ProfileForm
from models import BooleanMessage
from models import Conference
from models import ConferenceForm
from models import ConferenceForms
from models import ConferenceQueryForm
from models import ConferenceQueryForms
from models import TeeShirtSize
from models import Session
from models import SessionForm
from models import SessionForms
from models import WishList

from utils import getUserId

from settings import WEB_CLIENT_ID

from models import StringMessage

import pickle
import json

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS = {
         'CITY': 'city',
         'TOPIC': 'topics',
         'MONTH': 'month',
         'MAX_ATTENDEES': 'maxAttendees',
         }

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_BY_TYP_REQUEST = endpoints.ResourceContainer(
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2)
)

FEATURED_SPEAKER_FOR_CONF = endpoints.ResourceContainer(
    webSafeConferenceKey=messages.StringField(1)
)

SESSION_BY_SPK_REQUEST = endpoints.ResourceContainer(
    speaker=messages.StringField(1)
)

CONF_BY_CONTXT_REQUEST = endpoints.ResourceContainer(
    containsTxt=messages.StringField(1)
)

CONF_BY_MNTH_REQUEST = endpoints.ResourceContainer(
    month=messages.IntegerField(1)
)

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    sessionkey=messages.StringField(1)
)

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -


@endpoints.api(name='conference', version='v1',
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf

    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        # TODO 2: add confirmation email sending task to queue
        taskqueue.add(params={'email': user.email(),
                      'conferenceInfo': repr(request)},
                      url='/tasks/send_confirmation_email'
                      )
        return request

    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)

    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)

    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, user_id))
        prof = ndb.Key(Profile, user_id).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )

    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q

    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)

    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )

    @endpoints.method(CONF_BY_CONTXT_REQUEST, ConferenceForms,
                      path='conferences/contains/{containsTxt}',
                      http_method='GET', name='getConferenceByConTxt')
    def getConferenceByConTxt(self, request):
        """
        Search for Conference using text in Conference Name or Description.
        """
        confs = Conference.query()

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in confs]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        return ConferenceForms\
            (items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])
                    for conf in confs
                    if any(request.containsTxt.lower() in Text for Text in
                           [conf.name.lower(), str(conf.description or "NoneNoneNoneNone").lower()])]
             )

    @endpoints.method(CONF_BY_MNTH_REQUEST, ConferenceForms,
                      path='conferences/month/{month}',
                      http_method='GET', name='getConferenceByMonth')
    def getConferenceByMonth(self, request):
        """
        Get Conference By Month. Accepts Integer value for Month.
        Only works when Organizer has provided Start Date while Creating Conference.
        """
        print request.month
        confs = Conference.query(Conference.month == request.month)
        print confs
        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in confs]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])
                   for conf in confs])

    # - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf

    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = getUserId(user)
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile

    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
            prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)

    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()

    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)

    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
                               )

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)

    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement

    @endpoints.method(message_types.VoidMessage, StringMessage,
                      path='conference/announcement/get',
                      http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        # TODO 1
        # return an existing announcement from Memcache or an empty string.
        announcement = memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY)
        if not announcement:
            announcement = ""
        return StringMessage(data=announcement)

    @staticmethod
    def _cacheConfBySpeaker(sessions, conference):
        print "Inside cache conf by speaker method:"
        sessions = pickle.loads(sessions)
        conference = pickle.loads(conference)
        speaker = [session.speaker for session in sessions]
        speaker = speaker[0]
        conf = conference.name
        SESSION_BY_SPEAKER_AND_CONF_KEY = speaker + ' ' + conf
        if len(sessions) >= 2:
            featuredSpeaker = '%s %s' % (
                    'Speaker: ' + speaker + ', ',
                    'Sessions: ' + ', '.join(session.name for session in sessions))
            memcache.set(SESSION_BY_SPEAKER_AND_CONF_KEY, featuredSpeaker)
        else:
            memcache.delete(SESSION_BY_SPEAKER_AND_CONF_KEY)
            featuredSpeaker = ""
        return featuredSpeaker

    @endpoints.method(FEATURED_SPEAKER_FOR_CONF, StringMessage,
                      path='session/announcement/{webSafeConferenceKey}/get',
                      http_method='GET', name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """
        Get Featured Speaker from memcache using conference key.
        """
        conf_key = ndb.Key(urlsafe=request.webSafeConferenceKey)
        conf_name = conf_key.get().name
        sessions = Session.query(ancestor=conf_key)
        SESSION_BY_SPEAKER_AND_CONF_KEY = set()
        for session in sessions:
            SESSION_BY_SPEAKER_AND_CONF_KEY.add(session.speaker.title() + ' ' + conf_name)
        announcements = {}
        count = 0
        for key in SESSION_BY_SPEAKER_AND_CONF_KEY:
            if memcache.get(key) is not None:
                count += 1
                announcements[count] = memcache.get(key)
        if len(announcements) == 0:
            announcements = ""
        else:
            announcements = json.dumps(announcements)
        return StringMessage(data=announcements)

# - - - - - - - - - Session - - - - - - - - - -

    def _copySessionToForm(self, session):
        se = SessionForm()
        print session
        for field in se.all_fields():
            print field.name
            print session[field.name] is not None
            if session[field.name]:
                if field.name == 'date' or field.name == 'start_time' or field.name == 'duration':
                    setattr(se, field.name, str(session[field.name]))
                else:
                    setattr(se, field.name, session[field.name])
        se.check_initialized()
        print se
        return se

    def _copySessionObjectToForm(self, session):
        se = SessionForm()
        for field in se.all_fields():
            if hasattr(session, field.name):
                if field.name == 'date':
                    setattr(se, field.name, str(getattr(session, field.name)))
                elif field.name == 'start_time':
                    setattr(se, field.name, str(getattr(session, field.name)))
                elif field.name == 'duration':
                    setattr(se, field.name, str(getattr(session, field.name)))
                else:
                    setattr(se, field.name, getattr(session, field.name))
        se.check_initialized()
        print se
        return se

    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
                      path='session/{websafeConferenceKey}',
                      http_method='POST', name='createSession')
    def createSession(self, request):
        """
        Create New Session in Conference.
        """
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        conf_user_id = conf.organizerUserId
        if user_id != conf_user_id:
            raise endpoints.ForbiddenException('Only conference organizer is authorized to create session')

        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        if data['date']:
            try:
                data['date'] = datetime.strptime(data['date'][:10], "%Y-%m-%d").date()
            except ValueError:
                raise endpoints.BadRequestException("Date should be in format YYYY-MM-DD")

        if data['start_time']:
            try:
                data['start_time'] = datetime.strptime(data['start_time'][:6], "%H:%M").time()
            except ValueError:
                raise endpoints.BadRequestException("Start Time should be in format HH:mm")

        if data['duration']:
            try:
                data['duration'] = datetime.strptime(data['duration'][:6], "%H:%M").time()
            except ValueError:
                raise endpoints.BadRequestException("Duration should be in format HH:mm")

        if data['speaker']:
            data['speaker'] = data['speaker'].title()

        del data['websafeConferenceKey']

        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey)
        session_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        session_key = ndb.Key(Session, session_id, parent=conf_key)
        data['key'] = session_key
        Session(**data).put()

        sessions = Session.query(ancestor=conf_key)
        sessions = sessions.filter(Session.speaker == data['speaker'].title()).fetch()
        conf = conf_key.get()
        taskqueue.add(params={'sessions': pickle.dumps(sessions),
                              'conference': pickle.dumps(conf)},
                      url='/tasks/add_session_by_speaker_to_cache'
                      )

        return self._copySessionToForm(data)

    @endpoints.method(CONF_GET_REQUEST, SessionForms,
                      path='session/{websafeConferenceKey}',
                      http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """
        Get Session for Conference using Conference Key.
        """
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        sessions = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))
        return SessionForms(sessions=
                            [self._copySessionObjectToForm(session)
                             for session in sessions]
                            )

    @endpoints.method(SESSION_BY_TYP_REQUEST, SessionForms,
                      path='session/{websafeConferenceKey}/{typeOfSession}',
                      http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """
        Get Sessions for Conference using Conference Key and Type of Sesssion.
        """
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        query = Session.query(ancestor=ndb.Key(urlsafe=request.websafeConferenceKey))
        sessions = query.filter(Session.typeOfSession == request.typeOfSession)
        return SessionForms(sessions=
                            [self._copySessionObjectToForm(session)
                             for session in sessions]
                            )

    @endpoints.method(SESSION_BY_SPK_REQUEST, SessionForms,
                      path='session/speaker/{speaker}',
                      http_method='GET', name='getSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """
        Get Session By Name of Speaker.
        """
        query = Session.query()
        sessions = query.filter(Session.speaker == request.speaker.title())
        return SessionForms(sessions=
                            [self._copySessionObjectToForm(session)
                             for session in sessions]
                            )

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='session/nonworkshop/beforeseven',
                      http_method='GET', name='getNonWorkshopSesBeforeSeven')
    def getNonWorkshopSesBeforeSeven(self, request):
        """
        Get Non Workshop Sessions Before 7 PM.
        """
        sessions_nw = Session.query()
        sessions_before7 = Session.query()
        if not sessions_nw and sessions_before7:
            raise endpoints.NotFoundException(
                'No sessions found')
        sessions_nw = sessions_nw.filter(Session.typeOfSession != "workshop")
        sessions_nw_keys = sessions_nw.fetch(None, keys_only=True)
        print(sessions_nw_keys)

        sessions_before7 = sessions_before7.filter(Session.start_time < datetime.strptime("19:00", "%H:%M").time())
        sessions_before7_keys = sessions_before7.fetch(None, keys_only=True)
        print(sessions_before7_keys)

        sessions_keys = list(set(sessions_nw_keys) & set(sessions_before7_keys))
        print(sessions_keys)

        # sessions = list()
        #
        # for key in sessions_keys:
        #     print(key)
        #     sessions.append(key.get())

        sessions = ndb.get_multi(sessions_keys)
        print(sessions)

        return SessionForms(sessions=
                            [self._copySessionObjectToForm(session)
                             for session in sessions]
                            )

# - - - - - - - - - - WishList - - - - - - - -

    @endpoints.method(WISHLIST_POST_REQUEST, SessionForm,
                      path='wishlist/add/{sessionkey}',
                      http_method='POST', name='addSessionToWishlist')
    def addSessionToWishlist(self, request):
        """
        Add Session to Wishlist.
        """
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)
        try:
            se = ndb.Key(urlsafe=request.sessionkey).get()
        except:
            raise endpoints.NotFoundException(
                'Please check session key: %s' % request.sessionkey)

        if not se:
            raise endpoints.NotFoundException(
                'No session found with key: %s' % request.sessionkey)
        elif not isinstance(se, Session):
            raise endpoints.NotFoundException(
                'session key is incorrect: %s' % request.sessionkey)

        data = {}
        data['user_id'] = user_id

        wl = WishList.query(ancestor=ndb.Key(Profile, user_id)).get()

        if not wl:
            p_key = ndb.Key(Profile, user_id)
            w_id = WishList.allocate_ids(size=1, parent=p_key)[0]
            w_key = ndb.Key(WishList, w_id, parent=p_key)
            data['key'] = w_key
            data['session_key'] = [request.sessionkey]
            WishList(**data).put()
        else:
            wl.session_key.append(request.sessionkey)
            wl.put()

        return self._copySessionObjectToForm(se)

    @endpoints.method(message_types.VoidMessage, SessionForms,
                      path='wishlist',
                      http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishlist(self, request):
        """
        Get Sessions in wishlist for logged in User.
        """
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        wlist = WishList.query(ancestor=ndb.Key(Profile, user_id)).get()
        if not wlist:
            raise endpoints.NotFoundException(
                'No wishlist found for user: %s' % user.nickname())

        skeys = wlist.session_key
        sessions = []

        for key in skeys:
            sessions.append(ndb.Key(urlsafe=key).get())

        return SessionForms(sessions=
                            [self._copySessionObjectToForm(session)
                             for session in sessions]
                            )

    @endpoints.method(WISHLIST_POST_REQUEST, message_types.VoidMessage,
                      path='wishlist/delete/{sessionkey}',
                      http_method='POST', name='deleteSessionInWishlist')
    def deleteSessionInWishlist(self, request):
        """
        Delete Session From Wishlist.
        """
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = getUserId(user)

        wlist = WishList.query(ancestor=ndb.Key(Profile, user_id)).get()
        if not wlist:
            raise endpoints.NotFoundException(
                'No wishlist found for user: %s' % user.nickname())

        skeys = wlist.session_key

        if request.sessionkey in skeys:
            wlist.session_key.remove(request.sessionkey)
            wlist.put()

        return message_types.VoidMessage()


api = endpoints.api_server([ConferenceApi]) # register API
