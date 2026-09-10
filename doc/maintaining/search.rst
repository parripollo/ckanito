============
Search index
============

CKAN keeps a search index of the datasets that powers the dataset search
page, the ``package_search`` API action, the facets, the dataset counts
of groups and organizations and the autocomplete. The index lives in the
CKAN database itself, in the ``package_search_index`` table: there is no
search server to install or run.

How it works
============

Every time a dataset is created, updated or deleted, CKAN builds a flat
document out of it (the same dict that
``IPackageController.before_dataset_index`` receives) and writes it to
the index table on its own transaction. The table holds:

* typed columns for the fields that are filtered, sorted and faceted on
  all the time (``name``, ``title``, ``state``, ``organization``,
  ``metadata_modified``, the ``tags``, ``groups`` and ``res_format``
  lists, the permission labels...);
* the whole document as JSONB, so any other field, including custom
  ``extras`` and fields added by plugins, can be filtered and faceted on;
* the stored ``data_dict`` and ``validated_data_dict`` that
  ``package_search`` returns;
* a full text vector with weights (name and title first, then tags,
  groups and organization, then notes and resource names, then the rest).

Searches are SQL statements over that table. The query syntax accepted by
``package_search`` (``q``, ``fq``, ``sort``, ``facet.field``...) is the
Lucene subset described in the :doc:`user guide </user-guide>` and in the
API documentation of :py:func:`~ckan.logic.action.get.package_search`;
queries outside that subset are rejected with an error rather than
executed.

Configuration
=============

``ckan.search.backend``
   Which implementation stores and searches the index. Built in:
   ``postgres`` (the default). Extensions can provide others through the
   ``ISearchBackend`` plugin interface.

``ckan.search.postgres.text_config``
   The PostgreSQL text search configuration used to stem and match
   words. The default, ``ckan_english``, is created by the CKAN
   migrations: English stemming without stop words. Any configuration
   installed in the database can be used instead (``spanish``,
   ``simple`` to disable stemming, or a custom one built with
   ``unaccent`` to ignore accents). Rebuild the index after changing it.

Other options
   ``ckan.search.remove_deleted_packages``, ``ckan.search.default_package_sort``,
   ``ckan.search.rows_max``, ``search.facets`` and ``search.facets.limit``
   are documented in :doc:`configuration`.

Managing the index
==================

The index is part of the database schema: ``ckan db init`` and
``ckan db upgrade`` create it and ``ckan db clean`` drops it with the
rest. Its content is derived from the datasets, so it can always be
rebuilt::

    ckan -c /etc/ckan/default/ckan.ini search-index rebuild

See the ``search-index`` commands in :doc:`cli` for the other
subcommands (``check``, ``show``, ``clear``, ``rebuild`` with
``--only-missing``). A rebuild is needed after upgrading from a CKAN
version that used an external search engine, after changing
``ckan.search.postgres.text_config``, and after installing a plugin that
changes what gets indexed.

Backups of the CKAN database include the index; replicas of the database
can serve searches.

Extending
=========

Plugins keep using the same hooks as always:
``IPackageController.before_dataset_index`` to change what is indexed,
``before_dataset_search`` / ``after_dataset_search`` to change queries
and results, ``IFacets`` for facets and ``IPermissionLabels`` for
visibility. A plugin that needs another search engine implements
``ckan.lib.search.backends.base.SearchBackend`` and registers it with
``ISearchBackend``; ``ckan.search.backend`` then selects it by name.
