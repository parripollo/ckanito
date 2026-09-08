=======
CKANito
=======

CKANito is CKAN with one dependency: PostgreSQL. Everything CKAN used to
delegate to Solr (search) and Redis (background jobs, server side
sessions, ad hoc key/value state) now runs inside the CKAN database,
behind interfaces that a third party could implement again on any other
engine. CKANito ships only the PostgreSQL implementations.

This section is written for people who know CKAN well and want to review
the changes in depth. It explains what was done, how, and why, and where
the code lives. The short list of touched files is in ``CKANITO.md`` at
the root of the repository; the roadmap and the decisions taken along the
way are in ``PLAN-CKANITO.md``.

.. toctree::
   :maxdepth: 2

   architecture
   search
   jobs
   sessions-and-kvstore
   upstream-and-migration
