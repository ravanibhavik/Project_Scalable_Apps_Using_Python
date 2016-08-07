App Engine application for Course Project For Developing Scalable Apps

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
2. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
3. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
4. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
5. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
6. (Optional) Generate your client library(ies) with [the endpoints tool][6].
7. Deploy your application.


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool


## Tasks Completed as part of assignment

Task 1: Add Session to Conference
----------------------------------

Session Models:	Session, SessionForm
New Methods: getConferenceSessions, getConferenceSessionsByType, getSessionsBySpeaker, createSession (speaker, session name and websafeconferencekey are mandatory fields for creating new Session)


Task 2: Add Session to User WishList
------------------------------------

WishList Model: WishList
New Methods: addSessionToWishlist, getSessionsInWishlist, deleteSessionInWishlist

New Methods allows user to add Sessions to wishlist using Session Key. Also, allows retrival of logged in users wishlist and deleting session
from wishlist using Session Key.


Task 3: Work On Indexes and Queries
-----------------------------------

1) Created required Indexes in index.yaml file.
2) Two new queries (and methods) added: getConferenceByConTxt (To retrieve Conference using text contained in Conference name or description),
getConferenceByMonth (To retrieve Conference using Conference Month (Enter Integer value for month)).
3) New Method: getNonWorkshopSesBeforeSeven (This method returns all non workshop sessions before 7 pm for provided websafeconferencekey).


Task 4: Add a Task
-------------------

Task Added: /tasks/add_session_by_speaker_to_cache (This Task will be created when new session is created (createSession API). It will create new memcache entry or delete entry depending on number of sessions by speaker in given Conference.)
New Method: getFeaturedSpeaker() - This Method returns Featured Speakers (Speakers who have 2 or more session) for given Conference. 
















