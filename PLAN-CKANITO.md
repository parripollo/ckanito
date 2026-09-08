# CKANito - plan de trabajo

Version del plan: 2026-09-08 (v1, borrador para discutir).
Base analizada: CKAN master `0731a7a4ab` (2.13.0a0, 2026-09-02). CKAN 2.12.0
salio el 2026-08-26.

Objetivo: un CKAN que corre SOLO con PostgreSQL. Solr y Redis desaparecen
como requisito, pero lo que hacian queda detras de interfaces (backends)
que un tercero podria reimplementar. Nosotros solo implementamos los
backends PostgreSQL. Todo el test suite y las validaciones de CKAN deben
seguir pasando (con los tests que hablan de Solr/RQ adaptados a la
interfaz).

## 0. Diagnostico: que hay que reemplazar realmente

Relevamiento hecho sobre el arbol (ver detalles en seccion 9).

### 0.1 Solr

Es mucho mas chico de lo que parece. Todo el codigo que habla con Solr
esta en `ckan/lib/search/` (4 archivos, 1273 lineas):

- `common.py`: `make_connection()` (pysolr), `is_available()`, 4 clases de
  excepcion (`SearchError`, `SearchQueryError`, `SearchIndexError`,
  `SolrConnectionError`).
- `index.py`: `PackageSearchIndex.index_package/commit/delete_package` y
  `clear_index()`. Aca se arma el "documento" plano que se indexa.
- `query.py`: `PackageSearchQuery.run/get_index/get_all_entity_ids`. Aca se
  arma la query Solr. `TagSearchQuery` y `ResourceSearchQuery` NO usan Solr
  (van a SQL).
- `__init__.py`: fachada publica (`index_for`, `query_for`, `rebuild`,
  `clear_all`, `check`, `show`, `check_solr_schema_version`).

Todo el resto de CKAN (logic actions, views, CLI, dictization, ckanext)
depende solo de: (a) la fachada `ckan.lib.search`, (b) las 4 excepciones,
(c) la forma del diccionario-documento que reciben los plugins en
`IPackageController.before_dataset_index`, y (d) el vocabulario de
parametros de `package_search` (`q, fq, fq_list, sort, rows, start, fl,
facet, facet.field, facet.limit, facet.mincount, qf, bf, boost, defType,
tie, mm, df, wt, extras`).

**La dificultad real no es hablar con Solr, es que la API publica de
`package_search` acepta sintaxis Lucene cruda** en `q`, `fq` y `sort`.
Extensiones de terceros (scheming, dcat, harvest, spatial...) y usuarios de
la API mandan cosas como `fq=organization:foo +tags:bar
-type:harvest`, `q=title:agua`, `sort=metadata_modified desc`. Para ser
compatibles hay que escribir un **parser de un subconjunto de Lucene que
traduzca a SQL**. Eso es el corazon del proyecto.

Que usa CKAN core de Solr (lo que el backend PG tiene que cubrir si o si):

- `q` texto libre con dismax (`qf = name^4 title^4 tags^2 groups^2 text`,
  `mm = 2<-1 5<80%`), o `q` con campos (`campo:valor`).
- `fq` como lista de clausulas: `+site_id:"x"`, `+state:active`,
  `+capacity:public`, `+permission_labels:(a OR b)`, `+dataset_type:x`,
  `organization:x`, `groups:x`, `tags:"x y"`, `res_format:CSV`,
  `vocab_<nombre>:"x"`, `+state:(active OR draft)`, `extras_<k>:v`.
- `facet.field` lista, con `facet.limit` y `facet.mincount`. Resultado como
  `{campo: {valor: cantidad}}`.
- `sort`: `score desc, metadata_modified desc`, `title_string asc`,
  `name asc`, `views_recent desc` (tracking), campos `extras_*`.
- `fl`: `id`, `name`, `id validated_data_dict`, `id data_dict`.
- Autocomplete: `name_ngram`/`title_ngram` (ngram 2-10).
- `get_index(ref)` por `name` o `id`; `get_all_entity_ids`.
- NO usa: highlighting, spellcheck, `bf`/`boost` (aceptados pero core no los
  setea), local params `{!...}` (bloqueados por defecto).

Documento indexado (`ckan/config/solr/schema.xml`, 40 campos + dinamicos
`extras_*`, `res_extras_*`, `vocab_*`, `*_date`, y un catch-all `*`):
el texto completo va a `text` (copyField de casi todo), `permission_labels`
no se almacena, `data_dict`/`validated_data_dict` son JSON almacenado.

### 0.2 Redis

Tambien mas chico de lo que parece. Redis se usa para exactamente tres
cosas:

1. **Background jobs** via RQ (`ckan/lib/jobs.py`, 327 lineas): `enqueue`,
   `get_queue`, `job_from_id`, `dictize_job`, `Worker` (con fork por job e
   `IForkObserver`), prefijo de colas por `site_id`, `ckan.jobs.timeout`.
   Expuesto a extensiones por `toolkit.enqueue_job`, `get_job_queue`,
   `job_from_id`. Actions `job_list/show/cancel/clear`, CLI `ckan jobs *`.
   **Unica extension in-tree que encola: datastore**, y lo hace tocando RQ
   crudo (`queue.enqueue_in(delay, ..., job_id=deterministico)` y
   `scheduled_job_registry`). Datapusher NO usa RQ (HTTP a servicio
   externo). Tracking no usa nada.
2. **Sesiones server-side**: `SESSION_TYPE=redis` via Flask-Session. El
   default ya es `cookie`, o sea que Redis es OPCIONAL hoy para sesiones.
   Flask-Session ya es pluggable.
3. **Conexion cruda** `ckan.lib.redis.connect_to_redis()` +
   `is_redis_available()` (ping al arrancar, solo loguea CRITICAL). Ninguna
   extension in-tree la usa directo; la doc de best-practices dice a las
   extensiones que pueden usar Redis con prefijo `site_id:extension`.

Las menciones a redis en `pagination.py`, `i18n.py`, `jinja_extensions.py`,
`flask_multistatic.py` son falsos positivos ("Redistribution" en licencias).

### 0.3 Tests y validaciones

- ~2933 tests (`ckan/tests` 2142, `ckanext/*` 791). CI corre pytest en 12
  splits sobre Python 3.10 con `postgres:14`, `ckan/ckan-solr:master`,
  `redis:7`, mas ruff, pyright, cypress (24 specs e2e contra CKAN vivo),
  sphinx docs, towncrier check, CodeQL. No hay flake8 ni mypy en CI.
- Tests que dependen de Solr o RQ de forma directa: ~80 en `lib/search/*`
  (77), `cli/test_search_index.py` (11), `lib/test_jobs.py` (21),
  `cli/test_jobs.py` (16), `config/test_sessions.py` (6 de 9),
  `pytest_ckan/test_fixtures.py` (4), datastore (2). Otros ~64 usos de
  `clean_index` y cientos de `package_search` dependen de que el backend
  funcione, no de Solr en si.
- Fixtures a re-implementar sobre la interfaz: `reset_index`,
  `clean_index`, `reset_redis`, `clean_redis`, `reset_queues`,
  `clean_queues`, `with_test_worker`, `RQTestBase`.
- Esta maquina: Python 3.12.3 (CKAN pide >=3.10, CI usa 3.10), PostgreSQL
  18.4 local corriendo (falta crear roles/DBs), docker disponible, sin venv
  ni CKAN instalado.

## 1. Decisiones de arquitectura (propuesta)

### 1.1 Busqueda: `ckan.lib.search` con backend pluggable

Nuevo paquete `ckan/lib/search/backends/`:

- `base.py`: `class SearchBackend` (abstracta) con el contrato minimo:
  - `index(doc: dict, defer_commit: bool)`, `delete(id)`, `commit()`,
    `clear(site_id)`, `is_available()`, `check_schema()`.
  - `search(params: dict, permission_labels) -> SearchResult(count, docs,
    facets)`, `get_by_reference(ref)`, `all_entity_ids(site_id, limit)`.
  - Recibe SIEMPRE el mismo `params` que hoy llega a Solr (q, fq[], sort,
    rows, start, fl, facet.field...) y el mismo `doc` que hoy se manda a
    Solr. Asi los hooks `before_dataset_index`, `before_dataset_search`,
    `after_dataset_search` e `IFacets` no cambian de forma, y un backend
    Solr de un tercero es casi un copy/paste de lo que hay hoy.
- `postgres.py`: backend PostgreSQL (nuestro unico backend).
- Seleccion por config: `ckan.search.backend = postgres` (default). Un
  tercero registra otro con una interfaz nueva `ISearchBackend` (o entry
  point `ckan.search_backends`). Decision pendiente: interfaz de plugin vs
  entry point; propongo entry point + interfaz para ser consistentes con
  como CKAN ya elige backends del datastore (`IDatastoreBackend`).
- Las 4 excepciones se mantienen (nombre y jerarquia). `SolrConnectionError`
  pasa a ser `SearchConnectionError` con alias `SolrConnectionError` para
  no romper extensiones.
- `solr_url`, `solr_user`, `solr_password`, `solr_timeout`,
  `ckan.search.solr_commit`, `ckan.search.solr_allowed_query_parsers`
  quedan declarados como legacy (ignorados con warning) para que un
  `ckan.ini` de CKAN cargue sin tocar nada.
- `pysolr` sale de `requirements.txt`.

Backend PostgreSQL, diseno:

- Tabla `package_search_index` (migracion alembic 110):
  `index_id PK`, `id`, `site_id`, `entity_type`, `dataset_type`, `name`,
  `title`, `state`, `capacity`, `organization`, `metadata_created`,
  `metadata_modified`, `indexed_ts`, `permission_labels text[]`,
  `tags text[]`, `groups text[]`, `res_format text[]`, ... (los campos
  string del schema.xml como columnas tipadas), `doc jsonb` (documento
  completo, para `extras_*`, `vocab_*`, `res_*`, `*_date` y el catch-all),
  `data_dict text`, `validated_data_dict text`, `fts tsvector` con pesos
  (A: name, title; B: tags, groups, organization; C: notes, res_name,
  res_description; D: resto del `text`), GIN sobre `fts`, GIN sobre `doc`,
  btree sobre `(site_id, state, capacity)`, `metadata_modified`, `name`.
- Autocomplete `name_ngram`/`title_ngram`: `pg_trgm` si esta disponible
  (extension contrib estandar), fallback `ILIKE '%x%'`. Ver riesgo R4.
- Stemming: configuracion `ckan.search.postgres.text_config = english`
  (Solr hoy usa snowball ingles). Documentar `spanish`, `simple`, etc.
- **Parser Lucene -> SQL** (`backends/lucene.py`): subconjunto soportado:
  `campo:valor`, `campo:"frase"`, prefijos `+`/`-`, `AND`/`OR`/`NOT`,
  parentesis, `campo:(a OR b)`, rangos `campo:[a TO b]` (fechas y
  numeros, `*` abierto), `*:*`, `campo:*`, wildcard `valor*`, texto libre
  sin campo (va a `fts`), y date math basico de Solr (`NOW`, `NOW-7DAYS`,
  `NOW/DAY`) porque extensiones lo usan. Escrito con `pyparsing` (ya es
  dependencia). Cualquier cosa fuera del subconjunto -> `SearchQueryError`
  claro, nunca SQL a ciegas. Mapeo de campo: columna tipada si existe,
  sino `doc->>'campo'` / `doc->'campo' ? 'valor'` para multivaluados.
- dismax: el `q` libre se traduce a `websearch_to_tsquery` con semantica
  AND (`q.op=AND` hoy) y ranking `ts_rank_cd` sobre pesos. El `mm`
  ("2<-1 5<80%") se aproxima; no va a haber paridad exacta de ranking con
  Solr y hay que aceptarlo (riesgo R1). `qf` custom (multilingual manda
  `title_<lang>^8 text_<lang>^4`) se respeta cuando los campos existan en
  `doc`.
- Facets: una query por campo facetado con `unnest` sobre el conjunto
  filtrado (CTE compartido), `ORDER BY count DESC, value`, `LIMIT
  facet.limit`, `HAVING count >= facet.mincount`. Facets sobre `extras_*` y
  `vocab_*` via jsonb.
- Sort: lista `campo asc|desc`; `score` -> rank; campo desconocido ->
  `SearchQueryError('Invalid "sort" parameter')` (mismo mensaje que hoy).
  Function queries de Solr en sort: no soportado (documentado).
- `commit()`: no-op (transaccional). `defer_commit` se respeta como flag
  de no-flush por compatibilidad de firma.
- `ckan search-index rebuild/check/show/clear/list-orphans/...` siguen
  funcionando igual (van por la fachada).

### 1.2 Redis: tres abstracciones, no una

Reemplazar "Redis" no es un concepto; lo son sus tres usos. Propongo:

**a) Jobs** - `ckan/lib/jobs/` pasa a paquete con `backends/`:

- Contrato `JobBackend`: `enqueue(queue, func, args, kwargs, title,
  timeout, job_id=None, scheduled_at=None) -> Job`, `fetch(job_id)`,
  `cancel(job_id)`, `list_jobs(queue, limit)`, `empty(queue)`,
  `list_queues(prefix)`, `dequeue(queues, timeout) -> Job|None`,
  `mark_started/finished/failed`. Objeto `Job` propio con `.id .origin
  .meta .args .kwargs .timeout .created_at .save() .delete()` (lo que hoy
  se lee de `rq.job.Job`).
- API publica de `ckan.lib.jobs` y de `toolkit` NO cambia de nombres:
  `enqueue`, `get_queue`, `job_from_id`, `dictize_job`, `Worker`,
  `add/remove_queue_name_prefix`, `DEFAULT_QUEUE_NAME`. Se agrega
  `enqueue_in(delay, ...)` y `cancel` publicos, que es lo que datastore hoy
  hace con RQ crudo; datastore se porta a eso.
- Backend PostgreSQL: tabla `background_job` (`id`, `queue`, `func`
  (dotted path), `args/kwargs jsonb`, `meta jsonb`, `timeout`, `status`
  queued|started|finished|failed, `scheduled_at`, `created_at`,
  `started_at`, `ended_at`, `worker`, `error`). Dequeue con `SELECT ... FOR
  UPDATE SKIP LOCKED` (varios workers sin colisionar). Jobs diferidos:
  `scheduled_at > now()` (no hace falta scheduler aparte). `job_id`
  determinista soportado (datastore lo necesita). Jobs terminados se borran
  (hoy RQ tampoco conserva resultados; se documenta igual).
- Worker: conserva el modelo fork-por-job (para `IForkObserver`, aislar
  la sesion SQLAlchemy y matar por timeout con SIGALRM/SIGKILL al hijo),
  `--burst`, `--max-idle-time`; `--no-scheduler` queda como no-op
  aceptado. Polling con `LISTEN/NOTIFY` opcional + sleep corto.
- `rq` y `redis` salen de `requirements.txt`. Un tercero puede escribir
  `RQJobBackend`.

**b) Sesiones** - ya son pluggable por Flask-Session. Agregar
`CKANPostgresSessionInterface` (`SESSION_TYPE = postgres`) con tabla
`session_store` (`sid PK`, `data bytea/jsonb`, `expiry`), limpieza de
expiradas en `ckan jobs`-like cron o lazy. `cookie` sigue siendo el
default; `redis` deja de estar cableado en `common_middleware.py` (queda
como ejemplo de que un tercero puede registrar un `SessionInterface`).

**c) Key-value** - `ckan/lib/kvstore.py` con contrato minimo
`get/set(ttl)/delete/keys(pattern)/incr` y backend `postgres` (tabla
`kv_store` con `expires_at`). Es lo que le ofrecemos a las extensiones que
hoy hacen `connect_to_redis()` para sus propias claves (harvest, etc).
`ckan.lib.redis` queda como modulo deprecated que importa `redis` lazy y
falla con mensaje claro si no esta instalado/configurado.

### 1.2b Diseno detallado de la fase 3 (decidido 2026-09-08)

**Jobs.** `ckan/lib/jobs.py` queda como fachada publica con los mismos
nombres (`enqueue`, `get_queue`, `get_all_queues`, `job_from_id`,
`dictize_job`, `Worker`, prefijos, `DEFAULT_QUEUE_NAME`) y un paquete
nuevo `ckan/lib/jobqueue/` con el contrato `JobBackend` (`enqueue`,
`fetch`, `delete`, `list_jobs`, `scheduled_job_ids`, `queues`, `empty`,
`dequeue` con `FOR UPDATE SKIP LOCKED`, `mark_failed`, `finish`,
`requeue_stale`) y el backend `postgres` (tabla `background_job`:
id, queue con prefijo de site_id, func como `modulo:nombre`, args/kwargs
en pickle (como RQ; JSON no soporta datetime que datastore pasa), meta
jsonb, timeout, status queued|started|failed, scheduled_at, created_at,
started_at, ended_at, worker, error). Objetos propios `Queue` (`name`,
`jobs`, `job_ids`, `empty`, `delete`, `enqueue_call`, `enqueue_in`,
`scheduled_job_registry.get_job_ids`) y `Job` (`id`, `origin`, `meta`,
`args`, `kwargs`, `timeout`, `created_at`, `func_name`, `save`,
`delete`, `perform`, igualdad por id) que cubren exactamente lo que core,
datastore y tests usan de RQ, asi `ckanext/datastore` no se toca. Jobs
terminados se borran; fallidos quedan con status failed y error. Sin
scheduler aparte: `scheduled_at <= now()` los hace elegibles.

**Worker** en `jobs.py` (el logger `ckan.lib.jobs` lo exigen los tests):
loop de polling (1 s), colas por prioridad de izquierda a derecha,
`burst`, `max_idle_time`, `with_scheduler` aceptado y sin efecto. Fork por
job: antes `Session.remove()` + `engine.dispose()` + `IForkObserver`;
hijo carga environment, ejecuta y sale con `os._exit`; padre espera con
timeout del job (SIGKILL al vencer) y marca failed. `execute_job` (fork)
vs `perform_job` (en proceso) para que `with_test_worker` solo tenga que
parchear `execute_job`. Mensajes info exactos: inicio de worker, inicio
de job, fin de job, fin de worker. Barrido de jobs `started` vencidos
(worker muerto) en cada iteracion.

**Sesiones.** `SESSION_TYPE = postgres` con
`CKANPostgresSessionInterface(ServerSideSessionInterface)` de
flask-session sobre tabla `session_store` (id, data bytea, expiry). El
default sigue siendo `cookie`. `redis` desaparece de
`common_middleware.py`.

**Key-value.** `ckan/lib/kvstore.py` (`get`, `set` con ttl, `delete`,
`keys(pattern)`, `incr`) sobre tabla `kv_store` (key, value jsonb,
expires_at), reemplazo sancionado de `connect_to_redis` para extensiones
y para las fixtures `reset_redis`/`clean_redis`, que pasan a
`reset_kvstore`/`clean_kvstore` (los nombres viejos quedan como alias).

**Eliminaciones.** `ckan/lib/redis.py`, `ckan.redis.url`,
`CKAN_REDIS_URL`, ping de Redis en `environment.py`, `rq` y `redis` de
requirements, servicios Redis de CI/docker/cookiecutter. Una sola
migracion 111 crea las tres tablas.

### 1.3 Migracion CKAN -> CKANito

- Mismo esquema de DB mas 4 tablas nuevas (search index, background_job,
  session_store, kv_store) por alembic. `ckan db upgrade` y listo.
- `ckan search-index rebuild` puebla el indice (igual que hoy al migrar
  Solr).
- `ckan.ini` de CKAN carga sin cambios (claves solr/redis ignoradas con
  warning). Sacar `solr_url` y `ckan.redis.url` es opcional.
- Extensiones: las que usan `toolkit.enqueue_job/job_from_id/get_job_queue`
  y `package_search` con sintaxis dentro del subconjunto siguen andando.
  Rompen las que importan `rq`/`pysolr`/`redis` directo o usan features
  Solr fuera del subconjunto. Se entrega una guia `doc/ckanito/migration.rst`
  con la lista.

## 2. Estrategia de repositorio y compatibilidad con upstream

- **Repo propio en GitHub** con la historia completa de CKAN pusheada:
  `parripollo/ckanito` (creado 2026-09-08). No es un fork de GitHub; a
  nivel git es equivalente y `git merge upstream/master` trae los PRs.
- Remotes en el clon local: `upstream` = `https://github.com/ckan/ckan.git`
  (solo lectura), `origin` = `git@github.com:parripollo/ckanito.git` con
  `core.sshCommand` repo-local apuntando a
  `/data/dev/colmena/.ssh/id_ed25519_parripollo` y `user.name/email` de
  parripollo repo-local (mismo esquema que en colmena).
- **Rama base: `master`** (2.13.0a0), no 2.12. Razon: los PRs entran a
  master y se backportean; si nos paramos en master, traer cada PR nuevo es
  un `merge upstream/master` periodico. Cuando CKAN corte 2.13 cortamos
  CKANito 2.13. Contra: master es inestable y la primera version "usable"
  con extensiones de terceros llega cuando 2.13 salga. Alternativa: base
  2.12 y cherry-pick; mas trabajo continuo. **Decision tuya.**
- Reglas para que los merges de upstream sean baratos:
  1. Codigo nuevo en archivos nuevos (`backends/`, `lucene.py`,
     `kvstore.py`, migraciones). Los archivos existentes se tocan lo minimo
     (reemplazar el cuerpo de 5 funciones por llamadas al backend).
  2. Un `CKANITO.md` en la raiz con la lista exacta de archivos upstream
     modificados y por que, para resolver conflictos rapido.
  3. Nunca rebase/force sobre `master` de ckanito; solo merges de
     `upstream/master`.
  4. Fragmentos `changes/` y tests por cada cambio, como upstream.
  5. **Nunca PRs a ckan/ckan** (decision de andres, 2026-09-08): CKANito
     trabaja en abierto pero 100% aparte. Upstream es solo fuente de
     merges. El costo de mantenimiento se paga con las reglas 1-4: cuanto
     mas chico y aislado el delta, mas barato cada merge.

## 3. Fases

Cada fase termina con la suite completa en verde (con las excepciones
justificadas por escrito) y un merge a `master` de ckanito. Tamanio en
orden de magnitud: S = dias, M = 1-2 semanas, L = varias semanas de
trabajo efectivo.

**Fase 0 - Baseline (S).** Fork y remotes. venv (probar 3.12; si algo
falla, 3.10 via docker), roles y DBs en el Postgres 18 local, `pg_trgm`.
Levantar Solr y Redis en docker SOLO para esta fase y correr la suite
completa tal como esta. Guardar la lista de fallos preexistentes (si los
hay) como `baseline.txt`. Sin baseline no sabemos que rompimos nosotros.
Tambien ruff + pyright en verde como baseline.

**Fase 1 - Interfaz de busqueda extrayendo el Solr actual (M).**
Crear `SearchBackend` y mover el codigo Solr existente a
`backends/solr.py` sin cambiar comportamiento. Reescribir
`index.py/query.py/__init__.py` para que llamen al backend. Fixtures
`reset_index/clean_index` via backend. Suite en verde CONTRA SOLR. Esto
prueba que la interfaz es completa antes de escribir una linea de PG.
Al final de la fase el backend Solr se saca del arbol (queda en la
historia y sirve de referencia a terceros); decision pendiente si lo
dejamos en `contrib/` sin soporte.

**Fase 2 - Backend PostgreSQL de busqueda (L, el nucleo).**
2a. Migracion + modelo de la tabla, indexer (documento -> fila), rebuild.
2b. Parser Lucene (con su propia suite de tests unitarios, exhaustiva).
2c. `search()`: filtros, texto libre, sort, paginacion, `fl`.
2d. Facets.
2e. Autocomplete ngram, `get_by_reference`, orphans.
2f. Correr toda la suite contra PG; adaptar los ~80 tests Solr-especificos
(mensajes de error de pysolr, `check_solr_schema_version`, credenciales en
URL) a la interfaz. Los tests que asertan ORDEN por relevancia se revisan
uno a uno.
2g. Tests de contrato del backend (`tests/lib/search/backend_contract.py`)
parametrizables, para que un tercero valide su backend.

**Fase 3 - Jobs, sesiones y kvstore en PostgreSQL (M-L).**
3a. Contrato `JobBackend` + `Job`; refactor de `jobs.py` (con RQ como
backend transitorio igual que en fase 1, suite verde contra Redis).
3b. Backend PG + Worker fork + CLI. Portar datastore a `enqueue_in/cancel`
publicos. Adaptar `test_jobs.py`, `cli/test_jobs.py`, fixtures
`reset_queues/clean_queues/with_test_worker/RQTestBase`.
3c. `CKANPostgresSessionInterface` + tests de `test_sessions.py`.
3d. `kvstore` + `reset_redis/clean_redis` reimplementados sobre kvstore.
3e. Sacar rq/redis/pysolr de requirements; `ckan.lib.redis` deprecated.

**Fase 4 - Limpieza de infraestructura y validaciones (S-M).**
CI: sacar servicios solr/redis de `pytest.yml`, `cypress.yml`,
`test-infrastructure/docker-compose.yml`, `.devcontainer`, y del
cookiecutter de extensiones. `config_declaration.yaml` (nuevas claves,
legacy). pyright limpio (`reportUnnecessaryTypeIgnoreComment` va a
marcar los `# type: ignore` de pysolr/rq). ruff. Docs sphinx: instalacion
sin Solr/Redis, `doc/ckanito/` (arquitectura, backends, migracion).
Cypress en verde con CKAN levantado solo con PG.

**Fase 5 - Compatibilidad con extensiones de terceros (M).**
Instalar contra CKANito, en este orden: ckanext-scheming, ckanext-dcat,
ckanext-xloader (usa `enqueue_job`), ckanext-harvest (usa Redis crudo y
RQ: caso de "extension hardcodeada"), ckanext-spatial (usa Solr para
bbox: caso que no vamos a soportar), ckanext-pages, ckanext-showcase.
Correr sus suites. Documentar en la guia de migracion que anda, que anda
con cambios y que no.

**Fase 6 - Ensayo de migracion real (M).**
Dump de una instancia CKAN 2.x real -> restore -> `db upgrade` ->
`search-index rebuild` -> comparar resultados de `package_search` entre
CKAN y CKANito con un set de queries grabadas (mismo `count`, mismos
`id`s con tolerancia de orden en relevancia). Medir tiempos con 10k/100k
datasets.

## 4. Riesgos y como los manejo

- **R1 Paridad de ranking.** Imposible replicar dismax exacto. Mitigacion:
  paridad exacta en `count` y conjunto de resultados; ranking "razonable"
  (pesos por campo) documentado; tests de orden por relevancia se
  re-escriben para asertar contencion y no posicion exacta, salvo casos
  triviales (match exacto en `name` primero).
- **R2 Cobertura del subconjunto Lucene.** Extensiones mandan cosas
  raras en `fq`. Mitigacion: parser con suite unitaria grande, errores
  explicitos (nunca silencios), y en fase 5 recolectar todos los `fq`
  reales de las extensiones populares.
- **R3 Rendimiento de facets y jsonb** con catalogos grandes (>100k).
  Mitigacion: CTE unico, indices GIN, medir en fase 6; opcion de
  materializar columnas para campos facetados configurables.
- **R4 pg_trgm no disponible** (hosting restringido). Mitigacion: fallback
  ILIKE, documentado.
- **R5 Idioma del stemming.** Solr hoy stemmea en ingles para todos.
  Mitigacion: configurable; default `english` para paridad, recomendado
  `spanish`/`simple` segun instancia.
- **R6 Semantica del worker** (fork, timeouts, `IForkObserver`) sutil y
  con tests que cuentan mensajes de log exactos. Mitigacion: conservar el
  modelo fork; tests de log se adaptan.
- **R7 Python 3.12 local vs 3.10 en CI.** Mitigacion: si aparecen
  diferencias, correr la suite en el docker de `test-infrastructure` con
  3.10 antes de cada merge.
- **R8 Deriva con upstream** mientras dura el trabajo (semanas). Mitigacion:
  merge de `upstream/master` al final de cada fase, no al final del
  proyecto.
- **R9 Presupuesto de tokens/tiempo.** Trabajo secuencial, sin agentes en
  paralelo, fases chicas verificables, y correr subconjuntos de tests
  (`-k`, por archivo) durante el desarrollo; la suite completa solo al
  cerrar cada sub-fase.

## 5. Que NO vamos a hacer

- Backends Solr/Redis/RQ/Elasticsearch (solo la interfaz y los tests de
  contrato para que otro los haga).
- Soportar local params `{!...}`, function queries, highlighting,
  spellcheck, `bf`/`boost`.
- Cambiar la API publica de `package_search` ni de `toolkit`.
- Reescribir nada de CKAN que no toque Solr/Redis.

## 6. Decisiones que necesito de vos

1. Repo: https://github.com/parripollo/ckanito (creado 2026-09-08; no es
   fork de GitHub, es repo propio con la historia completa pusheada).
2. Rama base: `master` (recomendado) o `2.12`. Sin respuesta aun; sigo
   con `master`.
3. Backend Solr extraido en fase 1: borrarlo del arbol al terminar la fase
   (recomendado, para que `pysolr` no vuelva) o dejarlo en `contrib/` sin
   soporte.
4. Clave parripollo verificada por andres el 2026-09-08 ("Hi
   parripollo!"). Commits y push autorizados en este repo.
5. Postgres: resuelto con docker (`postgres:18` en 127.0.0.1:5433), sin
   depender del Postgres local. Solr y Redis tambien en docker, solo para
   la fase 0 (ver seccion 10).

## 7. Como lo veo (opinion)

Es factible y esta mejor delimitado de lo que el brief teme: el
acoplamiento a Solr y Redis esta concentrado en ~1700 lineas y 3 usos
claros, no desparramado. Lo que hace grande el proyecto no es el
desacople sino (1) el parser Lucene -> SQL con semantica compatible y (2)
la disciplina de mantener 2900 tests en verde en cada paso. Por eso las
fases 1 y 3a extraen la interfaz *manteniendo Solr/RQ funcionando* antes
de escribir Postgres: convierte un problema "big bang" en dos refactors
verificables.

Como no vamos a upstreamear nada, la sostenibilidad de "adaptar cada
PR de CKAN" depende por completo de mantener el delta chico y aislado
(seccion 2). Cada archivo upstream que tocamos es un conflicto potencial
en cada merge; por eso la regla de codigo nuevo en archivos nuevos no es
estetica, es el costo de mantenimiento futuro.

## 7b. Entregables pedidos ademas del codigo

- **Instancia local con datos** (pedido 2026-09-08): CKAN sin extensiones de
  terceros corriendo en local, con un comando reproducible de carga
  (`ckan ckanito seed-demo`) que crea usuarios, organizaciones, grupos,
  vocabulario, datasets (publicos, privados, borrador, eliminado),
  recursos por URL y subidos en varios formatos, tablas de datastore,
  vistas, relaciones, colaboradores y seguidores.
- **Explicacion tecnica de los cambios** (pedido 2026-09-08, para el
  final): documento en `doc/ckanito/` que explique como se implemento cada
  cambio y por que (contrato de backends, mapeo Solr -> PostgreSQL y sus
  limites, jobs/sesiones/kv, archivos upstream tocados, tests adaptados),
  para que expertos en CKAN evaluen el codigo con contexto.

## 8. Orden de trabajo inmediato

1. Vos: crear repo, confirmar rama base, credenciales de Postgres.
2. Yo: fase 0 completa (venv, DBs, docker solr+redis temporal, suite
   baseline, `baseline.txt`).
3. Yo: fase 1.

## 9. Referencias del relevamiento (para no volver a buscarlas)

Solr:
- `ckan/lib/search/common.py:37,48` `is_available`, `make_connection`.
- `ckan/lib/search/index.py:49,104,283-317` `clear_index`, `index_package`,
  writes. `RESERVED_FIELDS` en `:30-35`. `permission_labels` se agrega
  DESPUES del hook `before_dataset_index` (`:268-278`).
- `ckan/lib/search/query.py:30-38` `VALID_SOLR_PARAMETERS`, `QUERY_FIELDS`;
  `:337,349,371` `get_all_entity_ids`, `get_index`, `run`; `:482-503`
  traduccion de errores pysolr; `:533` `solr_literal`.
- `ckan/lib/search/__init__.py:42` `SUPPORTED_SCHEMA_VERSIONS`; `:83,107`
  `index_for/query_for`; `:117` `rebuild`; `:226,246` schema check por
  HTTP.
- Indexacion desde logic: `ckan/logic/__init__.py:979-1003`
  (`index_update_package*`, `index_insert_package_dicts`,
  `index_remove_package`). Ya no existe `SynchronousSearchPlugin`.
- `package_search`: `ckan/logic/action/get.py:1806-1900`; autocomplete
  `:1488-1493`; `package_show` usa `search.show` en `:1005`.
- Facets para conteo de grupos: `ckan/lib/dictization/model_dictize.py:34,
  337,516`.
- Excepciones -> HTTP: `ckan/views/api.py:316-334`.
- CLI: `ckan/cli/search_index.py`; `ckan/cli/db.py:84`.
- Config: `ckan/config/config_declaration.yaml:904-1058`;
  `ckan/config/environment.py:85-88,184`.
- Hooks: `ckan/plugins/interfaces.py:494,506,527` (no hay
  `after_dataset_index`); `IPermissionLabels :1961`; `IFacets :1611`.
- Extensiones in-tree: `ckanext/multilingual/plugin.py:207,257,292`;
  `ckanext/tracking/plugin.py:69,97`.
- Schema: `ckan/config/solr/schema.xml`.

Redis:
- `ckan/lib/redis.py` (58 lineas). Callers: `ckan/lib/jobs.py:85,111,181`;
  `ckan/config/middleware/common_middleware.py:56-73`;
  `ckan/config/environment.py:71-73`.
- `ckan/lib/jobs.py`: API `:37-210`, `Worker :219-322`. `rq==2.10.0`,
  `redis==8.0.1`.
- Actions: `get.py:3113-3172` (`job_list/show`), `delete.py:760-803`
  (`job_clear/cancel`). CLI `ckan/cli/jobs.py`.
- toolkit: `ckan/plugins/toolkit.py:42-46,129,136`.
- datastore RQ crudo: `ckanext/datastore/logic/action.py:855-912`;
  `IForkObserver` en `ckanext/datastore/plugin.py:278`.
- Sesiones: `ckan/config/middleware/flask_app.py:149-196,316`;
  `Flask-Session==0.8.0`, `cachelib==0.14.0`. Sin beaker.
- Config: `ckan.redis.url` en `config_declaration.yaml:1060-1066`;
  `ckan.jobs.timeout :1982`; `ckan.jobs.default_list_limit` se lee pero no
  esta declarado.

Tests/CI:
- `.github/workflows/{test,pytest,ruff,pyright,cypress,docs,towncrier}.yml`.
  Servicios en `pytest.yml:139-152`, `cypress.yml:16-44`.
- `test-infrastructure/{docker-compose.yml,init_environment.sh}`;
  `bin/postgres_init/`.
- Fixtures: `ckan/tests/pytest_ckan/fixtures.py` (`reset_index :250`,
  `reset_queues :267`, `reset_redis :276`, `clean_index :425`,
  `with_test_worker :597`); `ckan/tests/helpers.py` (`reset_db :44`,
  `RQTestBase :386`).
- Tests Solr/RQ-especificos: `ckan/tests/lib/search/test_{common,index,
  query,search}.py`, `ckan/tests/cli/test_search_index.py`,
  `ckan/tests/lib/test_jobs.py`, `ckan/tests/cli/test_jobs.py`,
  `ckan/tests/config/test_sessions.py`,
  `ckan/tests/pytest_ckan/test_fixtures.py:185-220`,
  `ckanext/datastore/tests/test_db.py:230`, `test_create.py:1488`.
- Antecedente: CKAN tuvo busqueda PG con tsvector (migraciones 011 y 043).

## 10. Entorno local de desarrollo (fase 0, 2026-09-08)

- venv: `ckan/.venv` (Python 3.10.19 via `uv`, igual que CI). Excluido en
  `.git/info/exclude`. `psycopg2` reemplazado por `psycopg2-binary` SOLO en
  el venv (no hay `libpq-dev` y no uso sudo); `requirements.txt` intacto.
- Servicios en docker: `ckanito-postgres` (postgres:18, 127.0.0.1:5433,
  user `ckan`/`ckan`), `ckanito-redis` (redis:7, 127.0.0.1:6380),
  `ckanito-solr` (ckan/ckan-solr:master, `--network host`, 8983). Solr va
  con host network porque por el bridge de docker las respuestas grandes
  de Jetty nunca llegan al host (respuestas chicas si; Postgres no lo
  sufre). Solr y Redis se apagan al terminar la fase 0.
- Config de tests: `test-core.ini` sin tocar + variables de entorno
  (`CKAN_SQLALCHEMY_URL`, `CKAN_DATASTORE_*_URL`, `CKAN_SOLR_URL`,
  `CKAN_REDIS_URL`) que CKAN ya mapea en `ckan/config/environment.py`.
  Roles/DBs creados como en `test-infrastructure/init_environment.sh`.
- Correr: `. .venv/bin/activate` y `TZ=UTC pytest
  --ckan-ini=test-core-local.ini -p no:cacheprovider --log-disable=ckan ...`.
  `test-core-local.ini` es un overlay de `test-core.ini` con las URLs de
  docker (no trackeado; en `.git/info/exclude`). NO usar variables
  `CKAN_*` de entorno: `test_config.py` y `test_environment.py` exigen que
  no esten.
- Gotchas: `ckan datastore set-permissions | psql` hay que correrlo con
  Solr ya arriba (carga el environment; si Solr no responde falla en
  silencio y despues fallan ~340 tests de datastore por
  `populate_full_text_trigger() does not exist`). `TZ=UTC` porque
  `ckanext/expire_api_token` mezcla `utcnow` con hora local y falla en
  cualquier zona distinta de UTC (bug upstream, no lo tocamos).

### Baseline (2026-09-08, upstream 0731a7a4ab intacto)

- pytest: 3555 tests, 0 fallos reales (1 deseleccionado
  `test_building_the_docs` como en CI, 2 skipped). Primera corrida completa
  15 min; los 226 fallos iniciales fueron ambiente (permisos datastore y
  variables `CKAN_*`), resueltos y re-verificados.
- ruff: limpio. pyright: ver `baseline-pyright.txt` en scratch (2 avisos
  de `pkg_resources` en el venv de uv; en CI con pip no aparecen).
