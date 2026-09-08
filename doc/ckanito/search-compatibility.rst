=================================
Search compatibility and coverage
=================================

This page answers the questions a CKAN expert asks first: how were the
Solr-specific queries handled, what exactly was lost, and why is it
faster.

How the supported subset was decided
====================================

Solr is a general search server; CKAN used a small, stable slice of it.
Instead of guessing, the slice was measured:

* every ``q``, ``fq``, ``fq_list``, ``sort``, ``fl`` and ``facet.*``
  value built by CKAN core (``package_search``, ``package_autocomplete``,
  group and organization dataset counts, ``tag_dictize``, the admin
  trash view, feeds, the search page and its facets, ``search-index``
  commands);
* every value that appears in CKAN's own test suite and in the in-tree
  extensions (datastore, activity, tracking, multilingual);
* the ``fq`` idioms common in the extension ecosystem (``dataset_type``,
  ``organization``, ``groups``, ``tags``, ``res_format``, ``vocab_*``,
  ``extras_*``, negations, date ranges with date math, ``owner_org``
  ids).

The query parser accepts that corpus completely, and refuses everything
else loudly. A search backend that silently returns the wrong rows is
worse than one that says "not supported"; that principle drove every
choice below.

Feature by feature
==================

.. list-table::
   :header-rows: 1
   :widths: 30 14 56

   * - Solr feature
     - Status
     - How
   * - ``field:value``, ``field:"phrase"``
     - supported
     - Typed columns, ``text[]`` arrays with GIN, JSONB containment for
       everything else. Phrases on text fields use ``phraseto_tsquery``.
   * - Free text (``q`` without a colon, "dismax")
     - supported
     - Words, quoted phrases, ``+`` / ``-`` modifiers, trailing ``*``
       prefix, matched against the weighted ``tsvector``.
   * - Boolean operators, grouping, implicit ``AND``
     - supported
     - ``AND`` / ``&&``, ``OR`` / ``||``, ``NOT`` / ``!`` / ``-``,
       parentheses, ``field:(a OR b)``. CKAN sets ``q.op=AND`` and so
       does the parser.
   * - Wildcards ``*`` and ``?``
     - supported
     - ``LIKE`` on exact fields, prefix ``tsquery`` on text fields,
       ``field:*`` as "field is present".
   * - Ranges ``[a TO b]``, ``{a TO b}``, open bounds
     - supported
     - Typed by field: timestamps, numerics, or text comparison.
   * - Date math (``NOW``, ``NOW-7DAYS``, ``NOW/DAY``, months, years)
     - supported
     - Evaluated in Python at compile time.
   * - ``sort`` on any field, ``score``, multiple keys
     - supported
     - Columns, JSONB fields (numbers sort numerically), ``ts_rank_cd``
       for ``score``; missing values first ascending / last descending as
       Lucene did; ``index_id`` appended for stable paging.
   * - Facets: ``facet.field``, ``facet.limit`` (``-1`` = all),
       ``facet.mincount``
     - supported
     - One ``GROUP BY`` per field over the filtered set, arrays unnested,
       JSONB scalars and lists handled alike.
   * - ``fl`` (``id``, ``name``, ``data_dict``, ``validated_data_dict``,
       ``*``)
     - supported
     - Columns or JSONB lookups; ``*`` returns the stored document.
   * - ``rows`` / ``start``, ``ckan.search.rows_max``
     - supported
     - ``LIMIT`` / ``OFFSET``; a separate ``count(*)`` gives the total.
   * - ``permission_labels``, ``site_id``, ``state``, ``capacity`` filters
     - supported
     - Built by ``PackageSearchQuery.run`` exactly as before, then
       compiled like any other ``fq``.
   * - Autocomplete on ``name_ngram`` / ``title_ngram``
     - supported (different technique)
     - Solr used an n-gram analyzer (2 to 10 characters); CKANito uses
       ``ILIKE '%term%'`` on the column. Same results for what
       ``package_autocomplete`` sends; ``pg_trgm`` can index it if a site
       needs speed on a large catalog.
   * - Stemming
     - supported (one configuration for all fields)
     - ``ckan_english`` (Snowball English, no stop words) by default,
       any installed PostgreSQL text search configuration via
       ``ckan.search.postgres.text_config``.
   * - Escaping (``\:``, ``\*``, quotes) and ``search_literal``
     - supported
     - Backslash escapes are honoured by the tokenizer; escaped wildcards
       are literal characters.
   * - ``qf`` (field boosts)
     - approximated
     - The default ``name^4 title^4 tags^2 groups^2 text`` became the
       A/B/C/D weights of the vector at index time. A custom ``qf`` sent
       by a plugin (``ckanext-multilingual`` does) is accepted but does
       not change the weights.
   * - ``mm`` (minimum should match, ``2<-1 5<80%``)
     - approximated
     - All words are required. Solr let one word be missing in queries
       of three to five words and 20% in longer ones; nothing in CKAN or
       its tests depended on that leniency.
   * - Relevance scores
     - different
     - ``ts_rank_cd`` with weights instead of BM25/TF-IDF with dismax
       boosts. The order of results by relevance can differ; the set and
       the count do not. Tests that asserted a relevance order were
       reviewed one by one and only one needed a change (it depended on
       random factory text).
   * - ``bf``, ``boost``, ``tie``, ``defType``, ``df``
     - accepted, ignored
     - Kept in the parameter whitelist so callers do not break.
       ``df`` other than ``text`` is honoured for unfielded terms.
   * - Local params ``{!...}`` (``{!bool}``, ``{!knn}``, ``{!field}``,
       ``{!geofilt}``), magic fields ``_query_`` / ``_val_``
     - rejected
     - ``SearchQueryError``. CKAN already blocked them unless
       ``ckan.search.solr_allowed_query_parsers`` opened them; that
       option is gone.
   * - Function queries in ``sort`` or ``q`` (``sum()``, ``recip()``,
       ``geodist()``)
     - rejected
     - ``SearchQueryError('Invalid "sort" parameter')`` / parse error.
   * - Fuzzy (``term~``), proximity (``"a b"~3``), boosts on terms
       (``term^2``)
     - parsed, ignored
     - The query still runs; the modifier has no effect.
   * - ASCII folding (``café`` matches ``cafe``)
     - not done by default
     - The PostgreSQL ``unaccent`` extension can be added to a custom
       text search configuration by the operator.
   * - Per-field analyzers, ``text_<lang>`` fields with language specific
       stemming (``ckanext-multilingual`` schema)
     - lost
     - One configuration applies to every text field. Multilingual data
       is still indexed and searchable inside the JSONB document, but
       with the site's stemmer, not one per language.
   * - Spatial queries (``ckanext-spatial``: bounding box filters,
       ``{!field f=spatial_geom}``, geo sorting)
     - lost (needs an extension backend)
     - These live in the extension's own Solr schema and query parsers.
       A CKANito-aware spatial extension would filter on PostGIS
       geometry in its ``before_dataset_search`` hook or register a
       search backend of its own.
   * - Highlighting, spellcheck / suggest, more-like-this, grouping,
       stats, pivot facets, ``facet.query``, ``facet.range``
     - not implemented
     - CKAN never used them. A backend implementing the contract could
       add them behind ``after_dataset_search``.
   * - Solr operations: cores, replication, sharding, schema versions,
       ``solr_url`` and authentication, ``check_solr_schema_version``
     - gone
     - The index is a table in the CKAN database and follows its
       migrations, backups and replication.

What was really lost
====================

Being precise about it:

1. **Relevance parity.** Two engines, two scoring models. If a site
   tuned its results by relying on dismax's exact behaviour, the order
   of the first page can change. Filters, counts and facets are the same.
2. **Language specific analysis per field.** Solr's schema could stem
   ``title_es`` in Spanish and ``title_fr`` in French. PostgreSQL text
   search works per configuration, and CKANito applies one to the whole
   document. Sites that need per-language stemming need a custom text
   search configuration or an extension backend.
3. **Accent folding by default.** Solvable with ``unaccent``, but not on
   by default because it changes what "exact" means for strings.
4. **Solr-only extensions.** Anything that shipped Solr schema fields or
   query parsers (spatial search is the important case) does not work
   until it is ported to the backend contract.
5. **Lenient matching (``mm``) and term-level boosts.** Minor in
   practice, absent nonetheless.
6. **Independent scaling of the search tier.** Search load now lands on
   the database. For the sizes most CKAN sites have this is a
   simplification; for a very large catalog with heavy search traffic,
   read replicas of PostgreSQL are the answer, or a dedicated backend
   implementing the contract.

Nothing that CKAN's own test suite exercises was lost: the whole suite
passes on the PostgreSQL backend.

Performance: why it is faster, and where to be careful
=======================================================

The speed-up is expected, and it comes from removing work rather than
from clever code:

* **No HTTP round trip and no JSON.** Every search, every index update
  and every ``package_show`` cache hit used to be an HTTP request to
  Solr with JSON serialized both ways. Now they are SQL statements on a
  pooled connection to a database CKAN is already talking to.
* **No Solr commit per write.** CKAN committed to Solr after every
  dataset change (``ckan.search.solr_commit = true``) so that the change
  was visible immediately; a Solr commit reopens the searcher and is the
  single most expensive operation in that setup. A PostgreSQL insert
  into an indexed table costs milliseconds. This is the main reason the
  test suite and bulk operations (``search-index rebuild``, harvesting,
  scripts creating many datasets) feel much faster.
* **No schema check, no availability ping at startup**, no
  ``rows + 1`` workaround.
* **Indexes do the work.** ``fts`` and ``doc`` have GIN indexes, the
  array columns too, ``metadata_modified`` and ``(site_id, name)`` are
  btree indexed, so the common query shapes are index scans.

Where to be careful, and what is still unmeasured:

* **Facets on large catalogs.** Each facet field is a ``GROUP BY`` over
  the filtered rows on every search page load; Solr cached facet counts
  in memory. Up to tens of thousands of datasets this is not noticeable.
  Beyond that, measure: the fixes are cheap (materialized columns for
  the facet fields, or caching the facet block for anonymous users).
* **Free text over huge documents.** ``ts_rank_cd`` reads the vector of
  every matching row to rank it; with hundreds of thousands of matches
  the ranking step dominates. PostgreSQL handles this well into the
  hundreds of thousands, but the number for a given site should be
  measured, which is planned as the final phase of the project with a
  real production dump.
* **Autocomplete** uses ``ILIKE '%x%'``, a sequential scan on ``name``
  and ``title``; add a ``pg_trgm`` GIN index on those columns on big
  sites.
* **Search load hits the database.** Watch connection counts and use a
  pooler if the web tier is large; a read replica can take the search
  traffic since the index is just a table.
