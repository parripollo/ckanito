# -*- coding: utf-8 -*-
import uuid
import pytest


from ckan.lib.search.common import config as ckan_config
import ckan.tests.factories as factories
from ckan.tests import helpers
import ckan.model as model
import ckan.lib.search as search
from ckan.lib.search.backends import get_backend

def get_data():
    return {
        "name": "council-owned-litter-bins",
        "extras": [
            {"key": "spatial-reference-system", "value": "test-spatial"},
        ],
    }


@pytest.mark.usefixtures("clean_db", "clean_index")
def test_02_add_package_from_dict():
    factories.Dataset()
    factories.Dataset(**get_data())
    query = search.query_for(model.Package)
    assert query.run({"q": ""})["count"] == 2
    assert query.run({"q": "spatial"})["count"] == 1


@pytest.mark.usefixtures("clean_db", "clean_index")
def test_03_update_package_from_dict():
    factories.Dataset()
    package = factories.Dataset(**get_data())
    query = search.query_for(model.Package)

    # update package
    package['name'] = "new_name"
    package['extras'].append({"key": "published_by", "value": "barrow"})
    helpers.call_action("package_update", context={}, **package)

    assert query.run({"q": ""})["count"] == 2
    assert query.run({"q": "barrow"})["count"] == 1
    assert query.run({"q": "barrow"})["results"][0] == "new_name"

    # update package again
    package['name'] = "council-owned-litter-bins"
    helpers.call_action("package_update", context={}, **package)

    assert query.run({"q": ""})["count"] == 2
    assert query.run({"q": "spatial"})["count"] == 1
    assert (
        query.run({"q": "spatial"})["results"][0]
        == "council-owned-litter-bins"
    )


@pytest.mark.usefixtures("clean_db", "clean_index")
def test_04_delete_package_from_dict():
    factories.Dataset()
    package = factories.Dataset(**get_data())
    query = search.query_for(model.Package)

    helpers.call_action("package_delete", context={}, id=package["id"])

    assert query.run({"q": ""})["count"] == 1


def test_local_params_not_allowed_by_default():

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": "{!bool must=test}"})

    assert str(e.value) == "Local parameters are not supported in param 'q'."


def test_local_params_not_allowed_by_default_different_field():

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"fq": "{!bool must=test} +site_id:test.ckan.net"})

    assert str(e.value) == "Local parameters are not supported in param 'fq'."


def test_local_params_not_allowed_by_default_different_field_list():

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"fq_list": ["+site_id:default", "{!bool must=test}"]})

    assert str(e.value) == "Local parameters are not supported in param 'fq_list'."


def test_local_params_with_whitespace_not_allowed_by_default():

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": " {!bool must=test}"})

    assert str(e.value) == "Local parameters are not supported in param 'q'."


@pytest.mark.parametrize(
    "q",
    [
        "_query_:{!bool must=test}",
        "_query_: {!bool must=test}",
        "_query_ :{!bool must=test}",
        "_query_:{!bool must=test} OR name:test",
        "name:test OR _query_:{!bool must=test}",
        "name:test OR _query_  :{!bool must=test}",
        "name:test OR   _query_  :{!bool must=test}",
        "_val_:{!bool must=test}",
        "_val_:{!bool must=test} OR name:test",
        "name:test OR _val_:{!bool must=test}",
        "{!bool must=test} name:test _query_:{!bool must=test}",
        r"\_query_:{!bool must=test}",
        r"_query\_:{!bool must=test}",
        r"_\q\u\e\r\y_:{!bool must=test}",
        r"\_\q\u\e\r\y\_:{!bool must=test}",
        r"+\_query_:{!bool must=test}",
        r"(\_query_:{!bool must=test})",
        r"(\  \_query_:{!bool must=test})",
        r"\_v\a\l\_ : sum(a,b)",

    ]

)
def test_magic_fields_not_allowed(q):

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": q})

    assert str(e.value) == "Magic fields are not supported in param 'q'."


@pytest.mark.parametrize(
    "q",
    [
        "dataset_query_:test",
        "dataset_query_store",
        "_query_store",
        "_query_store:true",
        "_query_store: true",
        "_query_store : true",
        "new_val_store name:test",
        "+new_val_inc name:test",
    ]

)
def test_magic_fields_dont_interfere(q):

    query = search.query_for(model.Package)
    assert query.run({"q": q})


@pytest.mark.parametrize(
    "q",
    [
        "{!bool must=test} OR name:test {!bool must=test}",
        "{!bool must=test} {!bool must=test}",
    ]

)
def test_query_parsers_not_allowed(q):

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": q})

    assert str(e.value) == "Query parsers are not supported in param 'q'."


@pytest.mark.parametrize(
    "q",
    [
        "\\\\{!bool must=test}",
        "xx{!bool must=test}",
        "name:test OR {!bool must=test}",
    ]

)
def test_local_params_at_the_start(q):

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": q})

    assert str(e.value) == "Local parameters must be defined at the beginning of param 'q'."


@pytest.mark.usefixtures("clean_index")
def test_local_params_never_allowed():

    query = search.query_for(model.Package)
    with pytest.raises(search.common.SearchError) as e:
        query.run({"q": "{!something_else a=test}"})

    assert str(e.value) == "Local parameters are not supported in param 'q'."


@pytest.mark.usefixtures("clean_index")
def test_get_index_uses_site_id():

    dataset_id = str(uuid.uuid4())

    backend = get_backend()

    datasets = [
        {
            "id": dataset_id,
            "site_id": "site1",
            "entity_type": "package",
            "type": "dataset",
            "state": "active",
            "private": False,
            "index_id": "2",
        },
        {
            "id": dataset_id,
            "site_id": ckan_config["ckan.site_id"],
            "entity_type": "package",
            "type": "dataset",
            "state": "active",
            "private": False,
            "index_id": "1",
        },
    ]
    for dataset in datasets:
        backend.index(dataset)

    # Check both records were indexed
    assert backend.get_by_reference(dataset_id, "site1")["site_id"] == "site1"
    assert backend.get_by_reference(
        dataset_id, ckan_config["ckan.site_id"])["index_id"] == "1"

    # Check that our own get_index method returns the dataset for the current site only
    query = search.query_for(model.Package)
    result = query.get_index(dataset_id)

    assert result["site_id"] == ckan_config["ckan.site_id"]
