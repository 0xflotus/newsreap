# -*- coding: utf-8 -*-
#
# A common NNTP Database management class for manipulating SQLAlchemy
#
# Copyright (C) 2015 Chris Caron <lead2gold@gmail.com>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

import gevent.monkey
gevent.monkey.patch_all()

from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError

from sqlalchemy import create_engine

from lib.Utils import parse_url

# Logging
import logging
from lib.Logging import NEWSREAP_ENGINE
logger = logging.getLogger(NEWSREAP_ENGINE)


# The Group key used to save the database version
VSP_VERSION_GROUP = u'newsreap.version'
VSP_VERSION_KEYS = (
    (u'major', 1),
    (u'minor', 0),
    (u'patch', 0),
    (u'build', 0),
)

# This one is used for testing since memory is destroyed once the program
# exits
MEMORY_DATABASE_ENGINE = 'sqlite:///:memory:'

# The catch wit SQLLite when referencing paths is:
# sqlite:///relative/path/to/where we are now
# sqlite:////absolute/path/
#
# TODO: When creating an error message, it might make sense to parse
# the database path 'IF using SQLite' to make the error
# messages more feasable
# configure Session class with desired options
Session = sessionmaker(autoflush=True, expire_on_commit=False)


class Database(object):
    """
    A managment class to the NNTP Core Database manipulation.
    """

    def __init__(self, base, vsp, engine=None, reset=None):
        """
        Initialize Database Manager
        """
        # The declaritive_base() object
        self.Base = base

        # The VSP Class Object Specific to the Database in question
        self.Vsp = vsp

        self._engine_str = MEMORY_DATABASE_ENGINE
        if isinstance(engine, basestring):
            self._engine_str = engine

        self._session = None
        self._engine = None

        # Initalizes to None, and is toggled to False/True
        # if the schema is determined to exist.  This allows a
        # call to open() to check if the schema exists right after
        # and handle the situation accordingly
        self._exists = None

        # Used to manage a list of the current version information
        self._version = []

        if reset is not None:
            # Allow reset control if specified
            self.open(reset=reset)

        # Used to track massive write modes
        self._write_mode = False


    def close(self):
        """
        Closes a database connection (if one is present)
        """
        if self._engine is not None:
            logger.debug('Closing database engine %s.' % self)
            self._session.expunge_all()
            self._session.close_all()
            self._engine = None
            self._session = None

            # Reset exists flag
            self._exists = None


    def __del__(self):
        """
        Gracefully clean up any lingering session data
        """
        self.close()


    def open(self, engine=None, reset=None):
        """
        Initializes the a connection to the database.

        if reset == True then the database is flushed (if it
                           exists already) and is recreated from
                           scratch.

        if reset == False then the database is only created if it
                           doesn't already exist

        if reset == None, then no initialization is perfored and it
                            is assued to just be configured correctly.

        The function returns the generated session, and returns None
        if the database and raises the SQLAlchemy error raised higher
        if one is generated in the event there is a serious problem.
        """
        if not engine:
            engine = self._engine_str
            if not engine:
                logger.error('No database engine was specified.')
                return None

        if engine != self._engine_str:
            if self._engine:
                # Close out any existing setup
                self.close()

            # save new engine id
            self._engine_str = engine

        if not self._engine:
            logger.debug('Preparing engine: %s' % self._engine_str)
            # Note: echo is always set to False, logging is handled by NewsReap
            #       and not by SQLAlchemy
            self._engine = create_engine(engine, echo=False)

            # associate it with our custom Session class
            Session.configure(bind=self._engine)

            # Store our session
            self._session = Session()

        if reset is True:
            logger.debug('Destroying existing database...')
            self.Base.metadata.drop_all(self._engine)

            # Force Initalization
            reset = False

            # Reset our version
            self._version = []

            # Reset _exists flag
            self._exists = False

        if self._exists is None:
            # The below works, but it's too hacky and requires us to identify
            # every single database type and handle them differently.
            # Surely there is a way we can test the schema (perhaps a version
            # stored in the database controlled by this class) and if the
            # versions don't match (or can't be retrieved, then we can handle
            # messaging that the database isn't initialized.
            try:
                self._version = [ (v.key, int(v.value)) \
                                 for v in self._session.query(self.Vsp)\
                    .filter_by(group=VSP_VERSION_GROUP)\
                    .order_by(self.Vsp.order).all() ]

                # Toggle Exists Flag
                self._exists = True

            except (ValueError, TypeError):
                # int() failed; what the heck does this database have in
                # it anyway?
                logger.warning('The database appears to have an integrity issue.')
                # Toggle Exists Flag; if there really is a problem,
                # let it fail elsewhere... we've given warning already
                self._exists = True

            except OperationalError:
                # Table doesn't exist
                self._exists = False

        if reset is False:
            if len(self._version) == 0 or \
               len(set(self._version) - set(VSP_VERSION_KEYS)):
                # TODO: if self._exists and the version is newer, research how
                #       to do a schema migration. Alternatively; definitely spit
                #       out an error if the version is older!
                logger.debug('Applying any database schema changes...')
                try:
                    self.Base.metadata.create_all(self._engine)

                except OperationalError, e:
                    logger.error('Database Initialization failed.')
                    logger.debug('Database Initialization error: %s' % str(e))
                    raise

                for v in VSP_VERSION_KEYS:
                    if not self._session.query(self.Vsp)\
                            .filter(self.Vsp.group == VSP_VERSION_GROUP)\
                            .filter(self.Vsp.key == v[0])\
                            .update({self.Vsp.value: v[1]}):

                        self._session.add(
                            self.Vsp(group=VSP_VERSION_GROUP, key=v[0], value=v[1]),
                        )

                # update our version listing
                self._version = list(VSP_VERSION_KEYS)

                self._session.commit()
                self._exists = True

        if self._exists is True:
            # Return our session object
            return self._session

        return None


    def session(self):
        """
        Returns a SQLAlchemy Database session if possible, otherwise
        it returns None
        """

        if self._session:
            return self._session

        if self._engine_str and not self._engine:
            if not self.open():
                return None

        return self._session


    def escape_search_str(self):
        """
        Escapes a search string for searching the database
        """

    def parse_url(self):
        """
        Returns details of the database already parsed

        """
        return parse_url(self._engine_str)
