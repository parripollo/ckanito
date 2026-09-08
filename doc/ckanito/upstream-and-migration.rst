==========================================
Upstream, migration and extensions
==========================================

Tracking ckan/ckan
==================

The repository is the full history of ``ckan/ckan`` (tags included) with
CKANito's commits on top of ``master``. ``upstream`` is a fetch-only
remote; changes are brought in with ``git merge upstream/master`` at the
end of each phase, never with rebases, and nothing is ever sent back to
``ckan/ckan``. To keep those merges cheap:

* new behaviour lives in new files (``ckan/lib/search/backends/``,
  ``ckan/lib/jobqueue/``, ``ckan/lib/kvstore.py``, the new model and
  migration files);
* upstream files are edited surgically and every one of them is listed in
  ``CKANITO.md`` with the reason, so a conflict can be resolved knowing
  what CKANito needs from that file;
* changelog fragments go in ``changes/`` with the ``+`` orphan prefix,
  as there are no pull request numbers.

Migrating a CKAN site
=====================

1. Install CKANito in place of CKAN. ``pysolr``, ``rq`` and ``redis`` are
   no longer dependencies.
2. ``ckan db upgrade``: migrations 110 and 111 add the
   ``package_search_index``, ``background_job``, ``session_store`` and
   ``kv_store`` tables and the ``ckan_english`` text search configuration.
3. ``ckan search-index rebuild``.
4. Stop Solr and Redis. ``solr_url``, ``solr_user``, ``solr_password``,
   ``solr_timeout``, ``ckan.search.solr_commit``,
   ``ckan.search.solr_allowed_query_parsers`` and ``ckan.redis.url`` in an
   existing ``ckan.ini`` are ignored with a warning; ``CKAN_SOLR_URL`` and
   ``CKAN_REDIS_URL`` are no longer read.
5. Keep running ``ckan jobs worker`` under Supervisor as before; it now
   polls the database.

Extension compatibility
=======================

Extensions that only use the public API (``package_search`` with the
supported query subset, ``toolkit.enqueue_job``, ``get_job_queue``,
``job_from_id``, ``IPackageController`` hooks, ``IPermissionLabels``,
``IFacets``) work unchanged. The ones that reached into Solr or Redis
need small edits:

.. list-table::
   :header-rows: 1

   * - CKAN name
     - CKANito name
   * - ``ckan.lib.search.SolrConnectionError``
     - ``ckan.lib.search.SearchConnectionError``
   * - ``ckan.lib.search.common.make_connection``
     - removed; ``ckan.lib.search.backends.get_backend()``
   * - ``ckan.lib.search.check_solr_schema_version``
     - ``ckan.lib.search.check_schema``
   * - ``ckan.lib.search.convert_legacy_parameters_to_solr``
     - ``ckan.lib.search.convert_legacy_parameters``
   * - ``ckan.lib.search.query.solr_literal``
     - ``ckan.lib.search.query.search_literal``
   * - ``ckan.lib.search.query.VALID_SOLR_PARAMETERS``
     - ``ckan.lib.search.query.VALID_SEARCH_PARAMETERS``
   * - ``ckan.lib.search.index.SOLR_FIELDS``
     - ``ckan.lib.search.index.INDEX_FIELDS``
   * - ``from rq import get_current_job``
     - ``from ckan.lib.jobs import get_current_job``
   * - ``from rq.timeouts import JobTimeoutException``
     - ``from ckan.lib.jobs import JobTimeoutException``
   * - ``ckan.lib.redis.connect_to_redis``
     - ``ckan.lib.kvstore`` (different, smaller API)
   * - ``reset_redis`` / ``clean_redis`` fixtures
     - ``reset_kvstore`` / ``clean_kvstore`` (old names still work)

No compatibility aliases with the old names are kept in the code: an
import error is clearer than a name that pretends Solr is still there.

Extensions that send Solr-only syntax (local params, function queries,
``bf``) or that ship their own Solr schema fields need real changes; the
search backend rejects what it cannot execute with a ``SearchQueryError``
rather than guessing.

Demo instance
=============

``ckan ckanito seed-demo`` fills an empty instance with users (``admin``
is a sysadmin), organizations with members, groups, a tag vocabulary,
datasets in every state, resources linked and uploaded in many formats,
datastore tables, resource views, relationships, collaborators and
followers. It is idempotent and uses only the action API, so it doubles
as a smoke test of the whole stack.
