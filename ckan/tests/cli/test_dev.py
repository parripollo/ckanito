# encoding: utf-8
import os
import sys

from click.testing import CliRunner

from ckan.cli.cli import ckan
from ckan.cli import dev


def test_dev_sql_defaults_are_idempotent_sql():
    sql = dev.dev_sql(None)
    assert "IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ckan_default')" in sql
    assert "CREATE ROLE \"datastore_default\" LOGIN PASSWORD 'pass'" in sql
    assert "CREATE DATABASE \"ckan_default\" OWNER \"ckan_default\"" in sql
    assert "WHERE datname = 'datastore_default') \\gexec" in sql
    # datastore grants come from ckanext.datastore's own template
    assert '\\connect "datastore_default"' in sql
    assert 'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "datastore_default"' in sql


def test_dev_sql_reads_the_ini(tmp_path, monkeypatch):
    ini = tmp_path / "ckan.ini"
    ini.write_text(
        "[app:main]\n"
        "sqlalchemy.url = postgresql://me:s3cret@db.example/portal\n"
        "ckan.datastore.write_url = postgresql://me:s3cret@db.example/portal_ds\n"
        "ckan.datastore.read_url = postgresql://ro:s3cret@db.example/portal_ds\n"
    )
    sql = dev.dev_sql(str(ini))
    assert "rolname = 'me'" in sql and "rolname = 'ro'" in sql
    assert "CREATE DATABASE \"portal_ds\" OWNER \"me\"" in sql
    assert "ckan_default" not in sql


def test_dev_sql_command_runs_without_a_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # the "no config needed" check in ckan.cli.cli reads sys.argv
    monkeypatch.setattr(sys, "argv", ["ckan", "dev", "sql"])
    result = CliRunner().invoke(ckan, ["dev", "sql"])
    assert result.exit_code == 0, result.output
    assert "CREATE ROLE" in result.output


def test_dev_writes_an_ini_then_fails_to_connect(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CKAN_INI", "ckan.ini")
    monkeypatch.setattr(sys, "argv", ["ckan", "dev"])
    # a port nobody listens on: the ini must be written, then a clear message
    monkeypatch.setattr(
        dev, "_urls",
        lambda ini: {"main": dev.make_url("postgresql://x:x@127.0.0.1:1/x")},
    )
    result = CliRunner().invoke(ckan, ["dev"])
    assert result.exit_code == 1
    assert os.path.exists("ckan.ini")
    text = open("ckan.ini").read()
    assert "ckan.plugins = " + dev.DEV_PLUGINS in text
    assert "ckan.storage_path = %(here)s/storage" in text
    assert "debug = true" in text
    assert "ckan dev sql | sudo -u postgres psql" in result.output
    assert "Traceback" not in result.output
