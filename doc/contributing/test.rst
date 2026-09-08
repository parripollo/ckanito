============
Testing CKAN
============

If you're a CKAN developer, if you're developing an extension for CKAN, or if
you're just installing CKAN from source, you should make sure that CKAN's tests
pass for your copy of CKAN. This section explains how to run CKAN's tests.

CKAN's testsuite contains automated tests for both the back-end (Python) and
the front-end (JavaScript). In addition, the correct functionality of the
complete front-end (HTML, CSS, JavaScript) on all supported browsers should be
tested manually.

.. seealso::

   :doc:`CKAN coding standards for tests <testing>`
     Conventions for writing tests for CKAN

--------------
Back-end tests
--------------

Most of CKAN's testsuite is for the backend Python code. You can run
the code in a dockerized environment that replicates GitHub Actions, or you
can use a virtual environment based testing.

~~~~~~~~~~~~~~~~
Dockerized Tests
~~~~~~~~~~~~~~~~

The ``test-infrastructure`` directory contains a configuration using
docker compose replicating the GitHub Actions test process on the local
machine.

Set up the testing environment
==============================
.. parsed-literal::

   cd test-infrastructure
   ./setup.sh

This starts a docker compose environment with the supporting postgres
and redis containers from the GitHub Actions test environment. The
databases are initialized, and the current ckan is installed into a
python container.


Run the tests
=============

.. parsed-literal::

   ./execute.sh

Or, if you wish to run a specific test, for example
``test_get_translated`` in ``test_helpers.py``:

.. parsed-literal::

   docker compose exec ckan pytest --ckan-ini=test-core-ci.ini ckan/tests/lib/test_helpers.py::test_get_translated


Teardown
========

.. parsed-literal::

   ./teardown.sh


~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Virtual Environment based tests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~


Install additional dependencies
===============================

Some additional dependencies are needed to run the tests. Make sure you've
created a config file at |ckan.ini|, then activate your
virtual environment:

.. parsed-literal::

    |activate|

Install pytest and other test-specific CKAN dependencies into your virtual
environment:

.. parsed-literal::

    pip install -r |virtualenv|/src/ckan/dev-requirements.txt

.. _datastore-test-set-permissions:


Set up the test databases
=========================

Create test databases:

.. parsed-literal::

    sudo -u postgres createdb -O |database_user| |test_database| -E utf-8
    sudo -u postgres createdb -O |database_user| |test_datastore| -E utf-8

Set the permissions::

    ckan -c test-core.ini datastore set-permissions | sudo -u postgres psql

When the tests run they will use these databases, because in ``test-core.ini``
they are specified in the ``sqlalchemy.url`` and ``ckan.datastore.write_url``
connection strings.

You should also make sure that the :ref:`Redis database <ckan.redis.url>`
configured in ``test-core.ini`` is different from your production database.




Run the tests
=============

To run CKAN's tests using PostgreSQL as the database, you have to give the
``--ckan-ini=test-core.ini`` option on the command line. This command will
run the tests for CKAN core and for the core extensions::

     pytest --ckan-ini=test-core.ini ckan/ ckanext/

The speed of the PostgreSQL tests can be improved by running PostgreSQL in
memory and turning off durability, as described
`in the PostgreSQL documentation <http://www.postgresql.org/docs/9.0/static/non-durability.html>`_.


~~~~~~~~~~~~~~~~~~~~~
Common error messages
~~~~~~~~~~~~~~~~~~~~~

OperationalError
================

``OperationalError: (OperationalError) no such function: plainto_tsquery ...``
   This error usually results from running a test which involves search functionality, which requires using a PostgreSQL database, but another (such as SQLite) is configured. The particular test is either missing a `@search_related` decorator or there is a mixup with the test configuration files leading to the wrong database being used.



---------------
Front-end tests
---------------
Front-end testing consists of both automated tests (for the JavaScript code)
and manual tests (for the complete front-end consisting of HTML, CSS and
JavaScript).

~~~~~~~~~~~~~~~~~~~~~~~~~~
Automated JavaScript tests
~~~~~~~~~~~~~~~~~~~~~~~~~~

The JS tests are written using the Cypress_ test framework. First you need to install the necessary packages::

    sudo apt-get install npm nodejs-legacy
    sudo npm install

.. _Cypress: https://www.cypress.io/

To run the tests, make sure that a test server is running::

    . /usr/lib/ckan/default/bin/activate
    ckan -c |ckan.ini| run

Once the test server is running switch to another terminal and execute the
tests::

    npx cypress run

~~~~~~~~~~~~
Manual tests
~~~~~~~~~~~~
All new CKAN features should be coded so that they work in the
following browsers:

* Internet Explorer: 11, 10, 9 & 8
* Firefox: Latest + previous version
* Chrome: Latest + previous version

Install browser virtual machines
================================

In order to test in all the needed browsers you'll need access to
all the above browser versions. Firefox and Chrome should be easy
whatever platform you are on. Internet Explorer is a little trickier.
You'll need Virtual Machines.

We suggest you use https://github.com/xdissent/ievms to get your
Internet Explorer virtual machines.

Testing methodology
===================

Firstly we have a primer page. If you've touched any of the core
front-end code you'll need to check if the primer is rendering
correctly. The primer is located at:
http://localhost:5000/testing/primer

Secondly whilst writing a new feature you should endeavour to test
in at least in your core browser and an alternative browser as often
as you can.

Thirdly you should fully test all new features that have a front-end
element in all browsers before making your pull request into
CKAN master.

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Common front-end pitfalls & their fixes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Here's a few of the most common front end bugs and a list of their
fixes.

Reserved JS keywords
====================

Since IE has a stricter language definition in JS it really doesn't
like you using JS reserved keywords method names, variables, etc...
This is a good list of keywords not to use in your JavaScript:

https://developer.mozilla.org/en-US/docs/JavaScript/Reference/Reserved_Words

::

  /* These are bad */
  var a = {
    default: 1,
    delete: function() {}
  };

  /* These are good */
  var a = {
    default_value: 1,
    remove: function() {}
  };

Unclosed JS arrays / objects
============================

Internet Explorer doesn't like it's JS to have unclosed JS objects
and arrays. For example:

::

  /* These are bad */
  var a = {
    b: 'c',
  };
  var a = ['b', 'c', ];

  /* These are good */
  var a = {
    c: 'c'
  };
  var a = ['b', 'c'];
