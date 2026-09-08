# encoding: utf-8
"""CKANito commands.

``ckan ckanito seed-demo`` fills an instance with demo data touching every
kind of object CKAN can hold: users, organizations, groups, a tag
vocabulary, datasets (public, private, draft, deleted), resources linked
by URL and uploaded, datastore tables, resource views, dataset
relationships, collaborators and followers.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any, Optional

import click
from werkzeug.datastructures import FileStorage

import ckan.logic as logic
import ckan.model as model
import ckan.plugins as plugins
from ckan.types import Context

from . import error_shout

log = logging.getLogger(__name__)

ADMIN = {"name": "admin", "email": "admin@ckanito.local", "fullname": "Ana Admin"}

USERS = [
    {"name": "editor-ambiente", "fullname": "Elena Editora",
     "email": "elena@ckanito.local", "about": "Edita datos ambientales."},
    {"name": "editor-ciudad", "fullname": "Carlos Cardozo",
     "email": "carlos@ckanito.local", "about": "Datos de la ciudad."},
    {"name": "analista", "fullname": "Ana Analista",
     "email": "ana@ckanito.local", "about": "Analiza datos abiertos."},
    {"name": "visitante", "fullname": "Victor Visitante",
     "email": "victor@ckanito.local"},
]

ORGANIZATIONS = [
    {"name": "ministerio-de-ambiente", "title": "Ministerio de Ambiente",
     "description": "Datos ambientales: calidad de aire, agua, residuos y "
                    "areas protegidas.",
     "image_url": "https://placehold.co/200x200/2e7d32/ffffff?text=Ambiente",
     "extras": [{"key": "sector", "value": "ambiente"}],
     "users": [("editor-ambiente", "editor"), ("analista", "member")]},
    {"name": "municipalidad-de-cordoba", "title": "Municipalidad de Cordoba",
     "description": "Datos de la ciudad de Cordoba: transporte, obras, "
                    "presupuesto participativo y espacios verdes.",
     "image_url": "https://placehold.co/200x200/1565c0/ffffff?text=Cordoba",
     "users": [("editor-ciudad", "editor"), ("analista", "member")]},
    {"name": "instituto-de-estadistica", "title": "Instituto de Estadistica",
     "description": "Series estadisticas oficiales: poblacion, precios, "
                    "empleo y comercio exterior.",
     "image_url": "https://placehold.co/200x200/6a1b9a/ffffff?text=INDEC",
     "users": [("analista", "admin")]},
    {"name": "universidad-nacional", "title": "Universidad Nacional",
     "description": "Datos academicos y de investigacion.",
     "image_url": "https://placehold.co/200x200/ef6c00/ffffff?text=UN",
     "users": [("editor-ciudad", "member")]},
]

GROUPS = [
    {"name": "ambiente", "title": "Ambiente y Clima",
     "description": "Aire, agua, clima y biodiversidad.",
     "image_url": "https://placehold.co/200x200/43a047/ffffff?text=Clima"},
    {"name": "transporte", "title": "Transporte y Movilidad",
     "description": "Colectivos, bicicletas, transito y siniestros viales."},
    {"name": "salud", "title": "Salud",
     "description": "Hospitales, vacunacion y epidemiologia."},
    {"name": "economia", "title": "Economia y Presupuesto",
     "description": "Presupuesto, compras publicas y precios."},
]

VOCABULARY = {
    "name": "frecuencia",
    "tags": ["diaria", "semanal", "mensual", "anual", "eventual"],
}

LICENSES = ["cc-by", "cc-by-sa", "odc-odbl", "cc-zero", "other-open",
            "notspecified"]

# 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC"
    "hwGA60e6kgAAAABJRU5ErkJggg==")

CSV_AIRE = (
    "fecha,estacion,pm25,pm10,no2,o3\n"
    "2026-01-01,Centro,12.5,20.1,18.0,45.2\n"
    "2026-01-02,Centro,14.1,22.8,19.4,43.9\n"
    "2026-01-03,Centro,9.8,17.5,15.1,50.3\n"
    "2026-01-01,Norte,8.2,15.0,12.3,48.0\n"
    "2026-01-02,Norte,7.9,14.2,11.8,49.5\n"
    "2026-01-03,Norte,10.4,18.9,14.7,44.1\n"
    "2026-01-01,Sur,15.7,25.3,21.2,40.8\n"
    "2026-01-02,Sur,16.3,26.0,22.5,39.7\n"
    "2026-01-03,Sur,13.9,23.1,19.9,42.6\n"
)

CSV_COLECTIVOS = (
    "linea,ramal,pasajeros_mes,km_recorridos,unidades\n"
    "10,A,183200,45210,18\n"
    "10,B,121900,38120,12\n"
    "23,unico,240100,61230,24\n"
    "31,Norte,98400,29540,10\n"
    "31,Sur,102300,30110,11\n"
    "60,Centro,310500,72880,30\n"
)

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"nombre": "Parque Sarmiento", "hectareas": 118},
         "geometry": {"type": "Point", "coordinates": [-64.18, -31.43]}},
        {"type": "Feature",
         "properties": {"nombre": "Parque de las Tejas", "hectareas": 9},
         "geometry": {"type": "Point", "coordinates": [-64.19, -31.43]}},
    ],
}

# name, title, org, groups, tags, license, frequency, extras, resources
DATASETS: list[dict[str, Any]] = [
    {
        "name": "calidad-del-aire",
        "title": "Calidad del aire - mediciones diarias",
        "owner_org": "ministerio-de-ambiente",
        "groups": ["ambiente", "salud"],
        "tags": ["aire", "contaminacion", "pm25", "salud"],
        "license_id": "cc-by",
        "frequency": "diaria",
        "notes": "Mediciones **diarias** de material particulado (PM2.5 y "
                 "PM10), dioxido de nitrogeno y ozono en las estaciones de "
                 "monitoreo de la ciudad.\n\n* Unidad: microgramos por "
                 "metro cubico\n* Fuente: red de monitoreo ambiental",
        "author": "Direccion de Calidad Ambiental",
        "author_email": "aire@ambiente.gob.ar",
        "maintainer": "Elena Editora",
        "maintainer_email": "elena@ckanito.local",
        "version": "2026.1",
        "url": "https://ambiente.gob.ar/aire",
        "extras": [{"key": "cobertura_geografica", "value": "Ciudad"},
                   {"key": "periodo", "value": "2026"}],
        "resources": [
            {"name": "Mediciones 2026 (CSV)", "format": "CSV",
             "upload": ("calidad-aire-2026.csv", CSV_AIRE.encode()),
             "datastore": True, "views": ["datatables_view"],
             "description": "Tabla con una fila por estacion y dia."},
            {"name": "Mediciones 2026 (JSON)", "format": "JSON",
             "upload": ("calidad-aire-2026.json",
                        json.dumps([
                            dict(zip(CSV_AIRE.splitlines()[0].split(","),
                                     row.split(",")))
                            for row in CSV_AIRE.splitlines()[1:]],
                            indent=2).encode())},
            {"name": "Metodologia (PDF)", "format": "PDF",
             "url": "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/"
                    "resources/pdf/dummy.pdf",
             "description": "Metodologia de medicion y calibracion."},
            {"name": "Ubicacion de estaciones (GeoJSON)", "format": "GeoJSON",
             "upload": ("estaciones.geojson",
                        json.dumps(GEOJSON).encode())},
        ],
    },
    {
        "name": "areas-protegidas",
        "title": "Areas naturales protegidas",
        "owner_org": "ministerio-de-ambiente",
        "groups": ["ambiente"],
        "tags": ["biodiversidad", "parques", "conservacion"],
        "license_id": "odc-odbl",
        "frequency": "anual",
        "notes": "Listado y limites de las areas naturales protegidas.",
        "resources": [
            {"name": "Limites (Shapefile ZIP)", "format": "ZIP",
             "url": "https://example.com/areas-protegidas.zip"},
            {"name": "Ficha (HTML)", "format": "HTML",
             "url": "https://www.argentina.gob.ar/parquesnacionales",
             "views": ["webpage_view"]},
            {"name": "Mapa (PNG)", "format": "PNG",
             "upload": ("mapa.png", _PNG), "views": ["image_view"]},
        ],
    },
    {
        "name": "residuos-urbanos",
        "title": "Residuos solidos urbanos recolectados",
        "owner_org": "ministerio-de-ambiente",
        "groups": ["ambiente", "economia"],
        "tags": ["residuos", "reciclaje"],
        "license_id": "cc-zero",
        "frequency": "mensual",
        "notes": "Toneladas de residuos recolectadas por mes y tipo.",
        "private": True,
        "resources": [
            {"name": "Residuos por mes (XLSX)", "format": "XLSX",
             "url": "https://example.com/residuos.xlsx"},
        ],
    },
    {
        "name": "recorridos-de-colectivos",
        "title": "Recorridos y pasajeros del transporte urbano",
        "owner_org": "municipalidad-de-cordoba",
        "groups": ["transporte"],
        "tags": ["colectivos", "transporte", "movilidad"],
        "license_id": "cc-by",
        "frequency": "mensual",
        "notes": "Pasajeros transportados y kilometros recorridos por "
                 "linea y ramal.",
        "extras": [{"key": "cobertura_geografica", "value": "Cordoba"}],
        "resources": [
            {"name": "Pasajeros por linea (CSV)", "format": "CSV",
             "upload": ("colectivos.csv", CSV_COLECTIVOS.encode()),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Recorridos (KML)", "format": "KML",
             "url": "https://example.com/recorridos.kml"},
            {"name": "API de posiciones en tiempo real", "format": "API",
             "url": "https://api.example.com/colectivos/posiciones",
             "description": "Endpoint REST con posicion de cada unidad."},
        ],
    },
    {
        "name": "bicicletas-publicas",
        "title": "Sistema de bicicletas publicas - viajes",
        "owner_org": "municipalidad-de-cordoba",
        "groups": ["transporte", "salud"],
        "tags": ["bicicletas", "movilidad"],
        "license_id": "cc-by-sa",
        "frequency": "diaria",
        "notes": "Viajes realizados en el sistema de bicicletas publicas.",
        "resources": [
            {"name": "Viajes 2026 (CSV)", "format": "CSV",
             "url": "https://example.com/bicis-2026.csv"},
            {"name": "Estaciones (GeoJSON)", "format": "GeoJSON",
             "upload": ("estaciones-bici.geojson",
                        json.dumps(GEOJSON).encode())},
        ],
    },
    {
        "name": "presupuesto-participativo",
        "title": "Presupuesto participativo - proyectos votados",
        "owner_org": "municipalidad-de-cordoba",
        "groups": ["economia"],
        "tags": ["presupuesto", "participacion"],
        "license_id": "other-open",
        "frequency": "anual",
        "notes": "Proyectos presentados y votados por barrio.",
        "resources": [
            {"name": "Proyectos 2025 (CSV)", "format": "CSV",
             "url": "https://example.com/pp-2025.csv"},
            {"name": "Reglamento (TXT)", "format": "TXT",
             "upload": ("reglamento.txt",
                        b"Reglamento del presupuesto participativo.\n"
                        b"1. Los proyectos se presentan por barrio.\n"
                        b"2. Vota cualquier vecino mayor de 16.\n"),
             "views": ["text_view"]},
        ],
    },
    {
        "name": "indice-de-precios",
        "title": "Indice de precios al consumidor",
        "owner_org": "instituto-de-estadistica",
        "groups": ["economia"],
        "tags": ["precios", "inflacion", "ipc"],
        "license_id": "cc-by",
        "frequency": "mensual",
        "notes": "Variacion mensual e interanual del IPC por division.",
        "resources": [
            {"name": "IPC mensual (CSV)", "format": "CSV",
             "url": "https://example.com/ipc.csv"},
            {"name": "IPC mensual (XLS)", "format": "XLS",
             "url": "https://example.com/ipc.xls"},
            {"name": "Serie historica (JSON)", "format": "JSON",
             "url": "https://example.com/ipc.json"},
        ],
    },
    {
        "name": "censo-poblacion",
        "title": "Censo nacional de poblacion 2022",
        "owner_org": "instituto-de-estadistica",
        "groups": ["salud", "economia"],
        "tags": ["censo", "poblacion", "demografia"],
        "license_id": "cc-by",
        "frequency": "eventual",
        "notes": "Resultados definitivos del censo 2022 por departamento.",
        "resources": [
            {"name": "Poblacion por departamento (CSV)", "format": "CSV",
             "url": "https://example.com/censo-2022.csv"},
            {"name": "Cuestionario (PDF)", "format": "PDF",
             "url": "https://example.com/censo-cuestionario.pdf"},
        ],
    },
    {
        "name": "vacunacion",
        "title": "Campana de vacunacion - dosis aplicadas",
        "owner_org": "instituto-de-estadistica",
        "groups": ["salud"],
        "tags": ["vacunas", "salud"],
        "license_id": "cc-zero",
        "frequency": "semanal",
        "notes": "Dosis aplicadas por semana, vacuna y grupo etario.",
        "resources": [
            {"name": "Dosis por semana (CSV)", "format": "CSV",
             "url": "https://example.com/vacunas.csv"},
        ],
    },
    {
        "name": "publicaciones-cientificas",
        "title": "Publicaciones cientificas de la universidad",
        "owner_org": "universidad-nacional",
        "groups": [],
        "tags": ["investigacion", "ciencia"],
        "license_id": "notspecified",
        "frequency": "anual",
        "notes": "Articulos publicados por facultad y ano.",
        "resources": [
            {"name": "Publicaciones (CSV)", "format": "CSV",
             "url": "https://example.com/publicaciones.csv"},
            {"name": "Video institucional", "format": "MP4",
             "url": "https://example.com/video.mp4",
             "views": ["video_view"]},
            {"name": "Podcast", "format": "MP3",
             "url": "https://example.com/podcast.mp3",
             "views": ["audio_view"]},
        ],
    },
    {
        "name": "sin-organizacion",
        "title": "Dataset sin organizacion",
        "owner_org": None,
        "groups": ["ambiente"],
        "tags": ["prueba"],
        "license_id": "cc-by",
        "frequency": "eventual",
        "notes": "Dataset creado por un usuario sin organizacion.",
        "resources": [],
    },
    {
        "name": "borrador-transito",
        "title": "Siniestros viales (borrador)",
        "owner_org": "municipalidad-de-cordoba",
        "groups": ["transporte"],
        "tags": ["transito"],
        "license_id": "cc-by",
        "frequency": "mensual",
        "notes": "Todavia en preparacion.",
        "state": "draft",
        "resources": [],
    },
    {
        "name": "dataset-eliminado",
        "title": "Dataset eliminado",
        "owner_org": "universidad-nacional",
        "groups": [],
        "tags": ["prueba"],
        "license_id": "cc-by",
        "frequency": "eventual",
        "notes": "Este dataset fue eliminado y solo lo ve un sysadmin.",
        "delete": True,
        "resources": [],
    },
]

RELATIONSHIPS = [
    ("vacunacion", "censo-poblacion", "depends_on",
     "Las tasas se calculan con la poblacion del censo"),
    ("bicicletas-publicas", "recorridos-de-colectivos", "links_to", None),
    ("areas-protegidas", "calidad-del-aire", "child_of", None),
]

COLLABORATORS = [
    ("calidad-del-aire", "analista", "editor"),
    ("indice-de-precios", "editor-ciudad", "member"),
]

FOLLOWS = [
    ("dataset", "visitante", "calidad-del-aire"),
    ("dataset", "analista", "indice-de-precios"),
    ("group", "visitante", "ambiente"),
    ("user", "visitante", "analista"),
]


class Seeder:
    def __init__(self, password: str):
        self.password = password
        self.created: dict[str, int] = {}

    def context(self, user: str = ADMIN["name"]) -> Context:
        return {"user": user, "ignore_auth": True}

    def action(self, action_name: str, as_user: str = ADMIN["name"],
               **data: Any) -> Any:
        return logic.get_action(action_name)(self.context(as_user), data)

    def count(self, kind: str) -> None:
        self.created[kind] = self.created.get(kind, 0) + 1

    def exists(self, action: str, id_: str) -> Optional[dict[str, Any]]:
        try:
            return self.action(action, id=id_)
        except logic.NotFound:
            return None

    # -- objects -----------------------------------------------------------

    def users(self) -> None:
        for data in [ADMIN] + USERS:
            if self.exists("user_show", data["name"]):
                continue
            self.action("user_create", password=self.password, **data)
            self.count("users")
        admin = model.User.get(ADMIN["name"])
        assert admin
        if not admin.sysadmin:
            admin.sysadmin = True
            model.Session.commit()

    def organizations(self) -> None:
        for data in ORGANIZATIONS:
            data = dict(data)
            members = data.pop("users")
            if not self.exists("organization_show", data["name"]):
                self.action("organization_create", **data)
                self.count("organizations")
            for username, role in members:
                self.action("organization_member_create", id=data["name"],
                            username=username, role=role)

    def groups(self) -> None:
        for data in GROUPS:
            if self.exists("group_show", data["name"]):
                continue
            self.action("group_create", **data)
            self.count("groups")
        self.action("group_member_create", id="ambiente",
                    username="editor-ambiente", role="editor")
        self.action("group_member_create", id="transporte",
                    username="editor-ciudad", role="admin")

    def vocabulary(self) -> dict[str, Any]:
        try:
            return self.action("vocabulary_show", id=VOCABULARY["name"])
        except logic.NotFound:
            vocab = self.action(
                "vocabulary_create", name=VOCABULARY["name"],
                tags=[{"name": tag} for tag in VOCABULARY["tags"]])
            self.count("vocabularies")
            return vocab

    def datasets(self, vocab: dict[str, Any]) -> None:
        for data in DATASETS:
            data = dict(data)
            resources = data.pop("resources")
            frequency = data.pop("frequency")
            delete = data.pop("delete", False)
            dataset = self.exists("package_show", data["name"])
            if dataset is None:
                tags = [{"name": tag} for tag in data.pop("tags")]
                tags.append({"name": frequency,
                             "vocabulary_id": vocab["id"]})
                data["tags"] = tags
                data["groups"] = [{"name": name} for name in data["groups"]]
                creator = "editor-ambiente" if data.get("owner_org") == \
                    "ministerio-de-ambiente" else ADMIN["name"]
                dataset = self.action("package_create", as_user=creator,
                                      **data)
                self.count("datasets")
            elif dataset.get("state") == "deleted":
                continue
            # resources one by one, so that a run interrupted half way
            # (e.g. datastore permissions missing) completes on the next run
            existing = {r["name"]: r for r in dataset.get("resources", [])}
            for resource in resources:
                self.resource(dataset["id"], resource,
                              existing.get(resource["name"]))
            if delete:
                self.action("package_delete", id=dataset["id"])

    def resource(self, package_id: str, data: dict[str, Any],
                 existing: Optional[dict[str, Any]] = None) -> None:
        data = dict(data)
        views = data.pop("views", [])
        datastore = data.pop("datastore", False)
        upload = data.pop("upload", None)
        if existing is None:
            if upload:
                filename, content = upload
                data["upload"] = FileStorage(io.BytesIO(content), filename)
                data["url"] = filename
            resource = self.action("resource_create",
                                   package_id=package_id, **data)
            self.count("resources")
        else:
            resource = existing
        if datastore and upload and plugins.plugin_loaded("datastore") \
                and not resource.get("datastore_active"):
            self.datastore(resource["id"], upload[1].decode())
        present = {v["view_type"] for v in self.action(
            "resource_view_list", id=resource["id"])}
        for view_type in views:
            if not plugins.plugin_loaded(view_type) or view_type in present:
                continue
            self.action("resource_view_create", resource_id=resource["id"],
                        title=view_type.replace("_", " ").title(),
                        view_type=view_type)
            self.count("resource_views")

    def datastore(self, resource_id: str, csv_text: str) -> None:
        lines = csv_text.strip().splitlines()
        header = lines[0].split(",")
        records = []
        for line in lines[1:]:
            values: list[Any] = []
            for value in line.split(","):
                try:
                    values.append(float(value) if "." in value
                                  else int(value))
                except ValueError:
                    values.append(value)
            records.append(dict(zip(header, values)))
        self.action("datastore_create", resource_id=resource_id,
                    records=records, force=True)
        self.count("datastore_tables")

    def relationships(self) -> None:
        for subject, obj, rel_type, comment in RELATIONSHIPS:
            try:
                present = self.action("package_relationships_list",
                                      id=subject, id2=obj, rel=rel_type)
            except logic.NotFound:
                present = []
            if present:
                continue
            self.action("package_relationship_create", subject=subject,
                        object=obj, type=rel_type, comment=comment)
            self.count("relationships")

    def collaborators(self) -> None:
        for dataset, user, capacity in COLLABORATORS:
            try:
                self.action("package_collaborator_create", id=dataset,
                            user_id=user, capacity=capacity)
                self.count("collaborators")
            except logic.ValidationError as e:
                log.warning("Collaborator %s on %s skipped: %s",
                            user, dataset, e)

    def follows(self) -> None:
        for kind, follower, target in FOLLOWS:
            if self.action("am_following_%s" % kind, as_user=follower,
                           id=target):
                continue
            self.action("follow_%s" % kind, as_user=follower, id=target)
            self.count("follows")

    def run(self) -> dict[str, int]:
        self.users()
        self.organizations()
        self.groups()
        vocab = self.vocabulary()
        self.datasets(vocab)
        self.relationships()
        self.collaborators()
        self.follows()
        return self.created


@click.group(short_help="CKANito commands")
@click.help_option("-h", "--help")
def ckanito():
    pass


@ckanito.command(name="seed-demo",
                 short_help="Fill the instance with demo data")
@click.option("-p", "--password", default="ckanito-demo-2026",
              show_default=True, help="Password for every demo user")
def seed_demo(password: str):
    """Create demo users, organizations, groups, datasets, resources,
    views, relationships, collaborators and followers. Safe to run more
    than once: objects that already exist are left alone.

    The sysadmin is ``admin``; every user gets the same password.
    """
    try:
        created = Seeder(password).run()
    except (logic.ValidationError, logic.NotFound) as e:
        error_shout(e)
        raise click.Abort()
    for kind, number in sorted(created.items()):
        click.secho("%-18s %d" % (kind, number), fg="green")
    if not created:
        click.secho("Nothing to do, demo data already present", fg="yellow")
    click.echo("Users: admin (sysadmin), %s. Password: %s" % (
        ", ".join(user["name"] for user in USERS), password))
