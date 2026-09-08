======
Search
======

The contract
============

``ckan.lib.search.backends.base.SearchBackend`` has nine methods:
``is_available``, ``check_schema``, ``index``, ``delete``, ``commit``,
``clear``, ``search``, ``get_by_reference`` and ``get_all_entity_ids``.
Two data shapes make up the contract, and both are exactly what CKAN has
always used, so plugins are unaffected:

* the **document** built by ``PackageSearchIndex.index_package``: the flat
  dict with ``id``, ``name``, ``title``, ``notes``, ``tags`` (names),
  ``groups``, ``organization``, ``res_*`` parallel lists, ``extras_<key>``
  plus the bare ``<key>``, ``vocab_<name>``, ``*_date`` normalised to ISO
  UTC, ``data_dict`` and ``validated_data_dict`` as JSON strings,
  ``permission_labels``, ``site_id``, ``index_id``... This is what
  ``IPackageController.before_dataset_index`` receives and returns, unchanged.
* the **query params** built by ``PackageSearchQuery.run``: ``q``, ``fq``
  (a list of clauses: the caller's ``fq`` and ``fq_list`` plus
  ``+site_id:"..."``, ``+state:active`` and
  ``+permission_labels:(a OR b)``), ``sort``, ``rows``, ``start``, ``fl``,
  ``facet.field``, ``facet.limit``, ``facet.mincount``, ``qf``, ``df``,
  ``defType``, ``mm``, ``tie``. ``IPackageController.before_dataset_search``
  and ``after_dataset_search`` see the same dicts as before.

The backend returns a ``SearchResponse(count, docs, facets)`` where
``facets`` is already ``{field: {value: count}}``. Everything that used to
be Solr specific in ``ckan/lib/search/query.py`` (the ``rows + 1``
workaround, the translation of pysolr error messages) moved into the
backend, so the module is now engine neutral. The four exceptions
(``SearchError``, ``SearchQueryError``, ``SearchIndexError``,
``SearchConnectionError``) are the error contract.

The PostgreSQL implementation
=============================

Storage
-------

One row per dataset in ``package_search_index``:

* typed columns for what CKAN filters, sorts and facets on all the time:
  ``id``, ``name``, ``title``, ``title_string``, ``state``, ``capacity``,
  ``organization``, ``dataset_type``, ``entity_type``,
  ``metadata_created`` / ``metadata_modified`` (``timestamp``),
  ``tags`` / ``groups`` / ``res_format`` / ``permission_labels``
  (``text[]`` with GIN indexes);
* ``doc jsonb``: the whole document (GIN ``jsonb_path_ops`` index), so
  any other field (``extras_*``, ``vocab_*``, ``res_name``, ``owner_org``,
  fields added by plugins) can be filtered and faceted on;
* ``data_dict`` and ``validated_data_dict`` as text, returned by ``fl``;
* ``fts tsvector`` (GIN index) with weights that mirror the ``copyField``
  rules and the ``qf`` boosts CKAN used: ``name`` and ``title`` weight A,
  ``tags`` / ``groups`` / ``organization`` B, ``notes`` / ``res_name`` /
  ``res_description`` C, everything else that used to be copied into
  ``text`` (extras, vocab tags, URLs, licence, author, maintainer) D.

Writes are ``INSERT ... ON CONFLICT (index_id) DO UPDATE`` on their own
connection and commit immediately, which is the same visibility CKAN had
with ``solr_commit = true`` (the index is written before the dataset's
own transaction commits, as before). ``commit()`` is a no-op;
``defer_commit`` is accepted and ignored.

Text search configuration
-------------------------

CKAN's Solr schema stemmed English (Snowball) and kept stop words. The
migration creates a ``ckan_english`` PostgreSQL text search configuration
that does exactly that: ``english`` stemming without the stop word
dictionary (searching for "the" or "after" finds datasets, as it always
did). ``ckan.search.postgres.text_config`` selects any other installed
configuration (``spanish``, ``simple`` to disable stemming); change it
and rebuild the index. ASCII folding (``café`` = ``cafe``) is not done;
the ``unaccent`` extension could be wired into a custom configuration by
an operator.

Query language
--------------

``package_search`` takes ``q``, ``fq`` and ``sort`` in Lucene syntax and
extensions rely on that (``fq="organization:x -tags:y"``,
``q="title:water"``). ``lucene.py`` is a hand written tokenizer and
recursive descent parser for the subset CKAN and its ecosystem actually
use:

* ``word``, ``"a phrase"``, ``wild*``, ``wi?ld``, ``*:*``;
* ``field:value``, ``field:"phrase"``, ``field:*``, ``field:(a OR b)``;
* ranges ``field:[a TO b]``, ``field:{a TO b]``, open bounds with ``*``,
  Lucene date math (``NOW``, ``NOW-7DAYS``, ``NOW/DAY``);
* ``+required``, ``-excluded``, ``NOT``, ``!``, ``AND`` / ``&&``,
  ``OR`` / ``||``, parentheses, implicit ``AND`` (CKAN sets ``q.op=AND``);
* boosts (``term^2``) and fuzziness (``term~``) are parsed and ignored.

Anything else (local params ``{!...}``, function queries, malformed
input such as ``--foo``) raises ``SearchQueryError``, which the API maps
to a 400 and the web UI to an "Invalid search query" page. Nothing is
ever passed through to SQL unparsed.

How ``q`` is interpreted follows CKAN's own rule: if ``q`` contains a
colon it is a fielded query and goes through the parser; otherwise it is
free text ("dismax" mode) and is matched against ``fts`` with ``+`` /
``-`` modifiers, quoted phrases and a trailing ``*`` for prefix search.
Every ``fq`` entry is parsed and ANDed.

Compilation
-----------

``compiler.py`` turns the AST into SQL with bound parameters. The only
things interpolated into the SQL text are column names from a fixed
whitelist; any other field name travels as a parameter into
``doc->:field``. Field typing mirrors the old schema:

* ``text``-like fields (``title``, ``notes``, ``res_name``,
  ``res_description``, ``author``, ``maintainer``, ``extras_*``,
  ``res_extras_*``, ``text_*``): stemmed match with ``to_tsvector`` /
  ``plainto_tsquery`` (``phraseto_tsquery`` for phrases, ``to_tsquery``
  with ``:*`` for prefixes); the catch-all ``text`` field is the ``fts``
  column;
* ``name_ngram`` / ``title_ngram`` (autocomplete): ``ILIKE '%term%'`` on
  the column;
* dates (``metadata_*``, ``*_date``): timestamp comparison;
* counters (``views_total``, ``views_recent``, ...): numeric comparison
  guarded by ``jsonb_typeof``;
* everything else: exact string match. Typed columns compare directly,
  array columns use ``@> ARRAY[...]``, JSONB fields use containment
  (``doc @> {"f": "v"}`` or ``doc @> {"f": ["v"]}`` so scalars and lists
  behave the same); wildcards become ``LIKE`` over the unnested values.

Every predicate is wrapped in ``coalesce(..., false)`` so that ``NOT``
matches documents where the field is absent, as Lucene does.

Ranking, sorting, facets
------------------------

* ``score`` is ``ts_rank_cd(fts, query)`` summed over the text parts of
  the query; with no text query it is a constant. Exact parity with
  dismax scoring is not a goal: the set of results and the count are
  what the tests assert, and the weights above reproduce the intent of
  ``qf = name^4 title^4 tags^2 groups^2 text``.
* ``sort`` is ``field asc|desc, ...``. ``score``, typed columns and JSONB
  fields (ordered with JSONB semantics, so numbers sort numerically) are
  all allowed; a missing direction or an unknown token yields
  ``SearchQueryError('Invalid "sort" parameter')`` like before. Missing
  values sort first ascending and last descending, which is what Lucene
  did. ``index_id`` is appended for stable pagination.
* Facets are one ``GROUP BY`` per requested field over the filtered set,
  using ``LATERAL unnest`` for array columns and
  ``jsonb_array_elements_text`` for JSONB fields (scalars are wrapped in a
  one element array), ordered by count then value, honouring
  ``facet.limit`` (``-1`` for all) and ``facet.mincount``.

Known differences from Solr
---------------------------

* Relevance scores differ; result sets do not.
* Stemming is per configuration and applies to every text field alike;
  there are no per-field analyzers.
* No ASCII folding by default (see above).
* ``bf``, ``boost``, ``tie``, ``mm``, ``defType`` are accepted and ignored.
* Local params and function queries are rejected instead of executed.
* ``text_<lang>`` fields written by ``ckanext-multilingual`` are searched
  as ordinary text fields inside ``doc``.

Operations
==========

``ckan db upgrade`` creates the table and the text search configuration;
``ckan search-index rebuild`` fills it. ``ckan search-index check``,
``show``, ``clear``, ``list-orphans``, ``clear-orphans``, ``rebuild-fast``
work unchanged because they only use the ``ckan.lib.search`` facade.
``ckan db clean`` drops the index with the rest of the schema.
