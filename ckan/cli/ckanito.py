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
import csv
import io
import json
import logging
import zipfile
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
     "fields": {"sector": "environment",
                "website": "https://ambiente.gob.ar"},
     "users": [("editor-ambiente", "editor"), ("analista", "member")]},
    {"name": "municipalidad-de-cordoba", "title": "Municipalidad de Cordoba",
     "description": "Datos de la ciudad de Cordoba: transporte, obras, "
                    "presupuesto participativo y espacios verdes.",
     "image_url": "https://placehold.co/200x200/1565c0/ffffff?text=Cordoba",
     "fields": {"sector": "city", "website": "https://cordoba.gob.ar"},
     "users": [("editor-ciudad", "editor"), ("analista", "member")]},
    {"name": "instituto-de-estadistica", "title": "Instituto de Estadistica",
     "description": "Series estadisticas oficiales: poblacion, precios, "
                    "empleo y comercio exterior.",
     "image_url": "https://placehold.co/200x200/6a1b9a/ffffff?text=INDEC",
     "fields": {"sector": "statistics", "website": "https://indec.gob.ar"},
     "users": [("analista", "admin")]},
    {"name": "universidad-nacional", "title": "Universidad Nacional",
     "description": "Datos academicos y de investigacion.",
     "image_url": "https://placehold.co/200x200/ef6c00/ffffff?text=UN",
     "fields": {"sector": "academic", "website": "https://unc.edu.ar"},
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

# Fields of the demo scheming schemas (ckanito_demo_schema.yaml and
# ckanito_demo_organization.yaml). With ckanext-scheming enabled they are
# real dataset fields; without it they are stored as extras.
FREQUENCY = {"diaria": "daily", "semanal": "weekly", "mensual": "monthly",
             "anual": "yearly", "eventual": "irregular"}
CONTACT_AMBIENTE = {"contact_name": "Elena Editora",
                    "contact_email": "elena@ckanito.local"}
CONTACT_CIUDAD = {"contact_name": "Carlos Cardozo",
                  "contact_email": "carlos@ckanito.local"}
CONTACT_INDEC = {"contact_name": "Mesa de ayuda INDEC",
                 "contact_email": "datos@indec.gob.ar"}


# --- generated sample files ---------------------------------------------------
#
# Everything the demo uploads is generated here, deterministically, so the
# demo works offline and the same data appears on every instance. Tabular
# files are loaded into the datastore.

def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def _json(data: Any) -> bytes:
    return json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")


def _pdf(title: str, lines: list[str]) -> bytes:
    """A minimal but valid one page PDF with plain text."""
    def esc(text: str) -> str:
        return (text.encode("ascii", "replace").decode("ascii")
                .replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"))

    content = ["BT", "/F1 18 Tf", "72 770 Td", "(%s) Tj" % esc(title),
               "/F1 11 Tf", "0 -30 Td"]
    for line in lines:
        content.append("(%s) Tj" % esc(line))
        content.append("0 -16 Td")
    content.append("ET")
    stream = "\n".join(content).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, xref))
    return bytes(out)


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _html(title: str, body: str) -> bytes:
    return ("<!doctype html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<title>%s</title><style>body{font-family:sans-serif;max-width:40em;"
            "margin:2em auto;line-height:1.5}</style></head><body><h1>%s</h1>%s"
            "</body></html>" % (title, title, body)).encode("utf-8")


# 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAC"
    "hwGA60e6kgAAAABJRU5ErkJggg==")

ESTACIONES_AIRE = ["Centro", "Norte", "Sur", "Este", "Oeste"]

_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _wave(day: int, base: float, amplitude: float, period: float,
          phase: float = 0.0) -> float:
    import math
    return round(base + amplitude * math.sin(2 * math.pi * day / period
                                             + phase), 1)


def tabla_aire() -> list[list[Any]]:
    rows = []
    for day in range(1, 32):
        for index, estacion in enumerate(ESTACIONES_AIRE):
            fecha = "2026-01-%02d" % day
            rows.append([
                fecha, estacion,
                _wave(day, 12 + 2 * index, 4, 7, index),
                _wave(day, 20 + 3 * index, 6, 7, index + 1),
                _wave(day, 18 + index, 5, 10, index + 2),
                _wave(day, 45 - 2 * index, 8, 14, index),
            ])
    return rows


CSV_AIRE = _csv(["fecha", "estacion", "pm25", "pm10", "no2", "o3"],
                tabla_aire())

GEOJSON_ESTACIONES_AIRE = _json({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"nombre": nombre, "altura_m": 380 + 15 * index},
         "geometry": {"type": "Point",
                      "coordinates": [-64.18 + 0.03 * (index - 2),
                                      -31.42 + 0.02 * (index - 2)]}}
        for index, nombre in enumerate(ESTACIONES_AIRE)
    ],
})

AREAS = [
    ("Reserva Natural Chancani", "Reserva natural", 4920, "Pocho", 1986),
    ("Parque Nacional Quebrada del Condorito", "Parque nacional", 37344,
     "Punilla", 1996),
    ("Reserva Hidrica Pampa de Achala", "Reserva hidrica", 146000,
     "San Alberto", 1999),
    ("Reserva Natural Cerro Colorado", "Reserva natural", 3000,
     "Rio Seco", 1957),
    ("Refugio de Vida Silvestre Monte de las Barrancas",
     "Refugio de vida silvestre", 7656, "Rio Primero", 1993),
    ("Reserva Natural Laguna La Felipa", "Reserva natural", 1307,
     "Juarez Celman", 1992),
    ("Parque Provincial Ernesto Tornquist Sur", "Parque provincial", 2200,
     "Calamuchita", 2001),
    ("Reserva Forestal Natural La Calera", "Reserva forestal", 11400,
     "Colon", 1941),
    ("Reserva Natural Urbana General San Martin", "Reserva urbana", 114,
     "Capital", 2009),
    ("Reserva Natural Vaquerias", "Reserva natural", 380, "Punilla", 1969),
    ("Corredor Biogeografico del Chaco Arido", "Corredor", 1150000,
     "Cruz del Eje", 2003),
    ("Reserva Natural Mar Chiquita", "Reserva natural", 1000000,
     "San Justo", 1994),
]

CSV_AREAS = _csv(
    ["nombre", "categoria", "hectareas", "departamento", "anio_creacion"],
    [list(area) for area in AREAS])

GEOJSON_AREAS = _json({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"nombre": nombre, "categoria": categoria,
                        "hectareas": hectareas},
         "geometry": {"type": "Point",
                      "coordinates": [-64.5 - 0.12 * index,
                                      -31.0 - 0.15 * (index % 5)]}}
        for index, (nombre, categoria, hectareas, _, _) in enumerate(AREAS)
    ],
})

CSV_RESIDUOS = _csv(
    ["anio", "mes", "tipo", "toneladas"],
    [[2025, mes, tipo, round(base + 40 * ((index * 7) % 5) - 60, 1)]
     for index, mes in enumerate(_MESES)
     for tipo, base in (("humedos", 18500), ("reciclables", 2300),
                        ("voluminosos", 640))])

LINEAS = [("10", "A"), ("10", "B"), ("23", "unico"), ("31", "Norte"),
          ("31", "Sur"), ("60", "Centro"), ("71", "unico"), ("81", "Este")]

CSV_COLECTIVOS = _csv(
    ["linea", "ramal", "mes", "pasajeros", "km_recorridos", "unidades"],
    [[linea, ramal, "2026-%02d" % mes,
      120000 + 9000 * index + 4000 * (mes % 3),
      38000 + 2500 * index + 900 * (mes % 2), 10 + 2 * index]
     for index, (linea, ramal) in enumerate(LINEAS)
     for mes in range(1, 4)])

HTML_COLECTIVOS = _html(
    "Transporte urbano de pasajeros",
    "<p>Ficha del sistema de transporte urbano: %d lineas relevadas, "
    "datos mensuales de pasajeros, kilometros recorridos y unidades en "
    "servicio.</p><p>Fuente: Direccion de Transporte.</p>" % len(LINEAS))

CSV_BICIS = _csv(
    ["fecha", "viajes", "usuarios_unicos", "duracion_promedio_min",
     "km_totales"],
    [["2026-01-%02d" % day, 800 + int(_wave(day, 0, 350, 7)),
      600 + int(_wave(day, 0, 250, 7, 1)), _wave(day, 18, 4, 30),
      round((800 + _wave(day, 0, 350, 7)) * 3.2, 1)]
     for day in range(1, 32)])

GEOJSON_BICIS = _json({
    "type": "FeatureCollection",
    "features": [
        {"type": "Feature",
         "properties": {"estacion": "Estacion %02d" % n, "anclajes": 12 + n},
         "geometry": {"type": "Point",
                      "coordinates": [-64.19 + 0.008 * (n % 4),
                                      -31.41 - 0.006 * (n // 4)]}}
        for n in range(1, 13)
    ],
})

BARRIOS = ["Alberdi", "Alta Cordoba", "Nueva Cordoba", "General Paz",
           "San Vicente", "Guemes", "Villa Cabrera", "Argüello",
           "Los Boulevares", "Villa El Libertador"]

CSV_PRESUPUESTO = _csv(
    ["id", "barrio", "proyecto", "monto_pesos", "votos", "estado"],
    [[n, BARRIOS[n % len(BARRIOS)],
      ["Plaza renovada", "Luminarias LED", "Cordon cuneta",
       "Centro vecinal", "Bicisenda", "Arbolado"][n % 6] + " %d" % n,
      1500000 + 250000 * (n % 7), 120 + 37 * (n % 9),
      ["aprobado", "en ejecucion", "terminado"][n % 3]]
     for n in range(1, 21)])

TXT_REGLAMENTO = (
    "Reglamento del presupuesto participativo\n\n"
    "1. Los proyectos se presentan por barrio, entre marzo y mayo.\n"
    "2. Puede votar cualquier vecino mayor de 16 anos.\n"
    "3. Se financian los proyectos mas votados hasta agotar el cupo.\n"
    "4. El avance de cada proyecto se publica en este portal.\n").encode("utf-8")

DIVISIONES = ["alimentos", "transporte", "vivienda", "salud", "educacion"]

CSV_IPC = _csv(
    ["mes", "nivel_general"] + DIVISIONES,
    [["%d-%02d" % (2024 + (m // 12), m % 12 + 1),
      _wave(m, 3.2, 1.4, 12)]
     + [_wave(m, 3.0 + 0.4 * i, 1.6, 12, i) for i in range(len(DIVISIONES))]
     for m in range(24)])

DEPARTAMENTOS = [
    ("Capital", 1565112, 1329604, 576), ("Colon", 273034, 225151, 2588),
    ("Punilla", 208180, 178401, 2592), ("Rio Cuarto", 272393, 246393, 18394),
    ("San Justo", 220216, 206307, 13677), ("General San Martin", 141462,
     127454, 5006), ("Santa Maria", 121412, 98188, 3427),
    ("Tercero Arriba", 118097, 109554, 5187), ("Union", 116306, 105727,
     11182), ("Marcos Juarez", 110683, 104205, 9490),
    ("Rio Segundo", 113784, 103718, 4970), ("Calamuchita", 76424, 54730,
     4642), ("Juarez Celman", 68089, 61078, 8902),
    ("Rio Primero", 52180, 46675, 6753), ("San Alberto", 41765, 37004,
     3327), ("Cruz del Eje", 62486, 58759, 6653),
]

CSV_CENSO = _csv(
    ["departamento", "poblacion_2022", "poblacion_2010", "variacion_pct",
     "superficie_km2", "densidad"],
    [[nombre, p22, p10, round(100.0 * (p22 - p10) / p10, 1), km2,
      round(p22 / km2, 1)]
     for nombre, p22, p10, km2 in DEPARTAMENTOS])

CSV_VACUNACION = _csv(
    ["semana", "vacuna", "grupo_etario", "dosis"],
    [["2026-W%02d" % week, vacuna, grupo,
      int(base * (1 + 0.3 * ((week * 3 + i) % 4)))]
     for week in range(1, 27)
     for i, (vacuna, base) in enumerate((("antigripal", 4200),
                                          ("covid", 1800),
                                          ("hepatitis_b", 650)))
     for grupo in ("0-17", "18-64", "65+")])

FACULTADES = ["Ciencias Exactas", "Medicina", "Ingenieria", "Humanidades",
              "Agronomia", "Derecho", "Arquitectura", "Economia"]

CSV_PUBLICACIONES = _csv(
    ["anio", "facultad", "articulos", "con_referato", "citas"],
    [[anio, facultad, 40 + 15 * i + 5 * (anio - 2023),
      30 + 12 * i + 4 * (anio - 2023), 120 + 60 * i + 30 * (anio - 2023)]
     for anio in (2023, 2024, 2025) for i, facultad in enumerate(FACULTADES)])

CSV_SINIESTROS = _csv(
    ["anio", "mes", "siniestros", "lesionados", "fallecidos"],
    [[2025, mes, 380 + 25 * (i % 4), 210 + 18 * (i % 5), 3 + (i % 3)]
     for i, mes in enumerate(_MESES)])


def _records(csv_bytes: bytes) -> list[dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
    return [dict(row) for row in reader]


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
                 "PM10), dioxido de nitrogeno y ozono en las cinco "
                 "estaciones de monitoreo de la ciudad, enero de 2026.\n\n"
                 "* Unidad: microgramos por metro cubico\n"
                 "* Fuente: red de monitoreo ambiental",
        "author": "Direccion de Calidad Ambiental",
        "author_email": "aire@ambiente.gob.ar",
        "maintainer": "Elena Editora",
        "maintainer_email": "elena@ckanito.local",
        "version": "2026.1",
        "url": "https://ambiente.gob.ar/aire",
        "fields": {"theme": ["environment", "health"],
                   "temporal_start": "2026-01-01", "temporal_end": "2026-01-31",
                   "geographic_coverage": "city", **CONTACT_AMBIENTE},
        "resources": [
            {"name": "Mediciones enero 2026 (CSV)", "format": "CSV",
             "upload": ("calidad-aire-2026-01.csv", CSV_AIRE),
             "datastore": True, "views": ["datatables_view"],
             "description": "Una fila por estacion y dia."},
            {"name": "Mediciones enero 2026 (JSON)", "format": "JSON",
             "upload": ("calidad-aire-2026-01.json",
                        _json(_records(CSV_AIRE))),
             "views": ["text_view"]},
            {"name": "Metodologia de medicion (PDF)", "format": "PDF",
             "upload": ("metodologia.pdf", _pdf(
                 "Metodologia de medicion de calidad del aire", [
                     "Estaciones automaticas con analizadores de referencia.",
                     "PM2.5 y PM10 por atenuacion beta, promedio diario.",
                     "NO2 por quimioluminiscencia, O3 por absorcion UV.",
                     "Calibracion mensual con patrones trazables.",
                 ]))},
            {"name": "Ubicacion de estaciones (GeoJSON)", "format": "GeoJSON",
             "upload": ("estaciones-aire.geojson", GEOJSON_ESTACIONES_AIRE),
             "views": ["text_view"]},
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
        "fields": {"theme": ["environment"], "temporal_start": "2025-01-01",
                   "temporal_end": "2025-12-31", "geographic_coverage": "province",
                   "contact_name": "Direccion de Areas Protegidas",
                   "contact_email": "areas@ambiente.gob.ar"},
        "notes": "Listado y ubicacion de las areas naturales protegidas de "
                 "la provincia, con categoria, superficie y anio de creacion.",
        "resources": [
            {"name": "Listado de areas (CSV)", "format": "CSV",
             "upload": ("areas-protegidas.csv", CSV_AREAS),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Ubicacion (GeoJSON)", "format": "GeoJSON",
             "upload": ("areas-protegidas.geojson", GEOJSON_AREAS),
             "views": ["text_view"]},
            {"name": "Mapa (PNG)", "format": "PNG",
             "upload": ("mapa.png", _PNG), "views": ["image_view"]},
            {"name": "Paquete completo (ZIP)", "format": "ZIP",
             "upload": ("areas-protegidas.zip", _zip({
                 "areas-protegidas.csv": CSV_AREAS,
                 "areas-protegidas.geojson": GEOJSON_AREAS}))},
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
        "fields": {"theme": ["environment", "economy"],
                   "temporal_start": "2025-01-01", "temporal_end": "2025-12-31",
                   "geographic_coverage": "city", **CONTACT_AMBIENTE},
        "notes": "Toneladas de residuos recolectadas por mes y tipo durante "
                 "2025. Dataset privado: solo lo ven los miembros de la "
                 "organizacion.",
        "private": True,
        "resources": [
            {"name": "Residuos por mes y tipo (CSV)", "format": "CSV",
             "upload": ("residuos-2025.csv", CSV_RESIDUOS),
             "datastore": True, "views": ["datatables_view"]},
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
        "notes": "Pasajeros transportados, kilometros recorridos y unidades "
                 "en servicio por linea y ramal, primer trimestre de 2026.",
        "fields": {"theme": ["transport"],
                   "temporal_start": "2026-01-01", "temporal_end": "2026-03-31",
                   "geographic_coverage": "city", **CONTACT_CIUDAD},
        "resources": [
            {"name": "Pasajeros por linea y mes (CSV)", "format": "CSV",
             "upload": ("colectivos-2026.csv", CSV_COLECTIVOS),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Ficha del sistema (HTML)", "format": "HTML",
             "upload": ("ficha-transporte.html", HTML_COLECTIVOS),
             "views": ["webpage_view"]},
            {"name": "API de posiciones en tiempo real", "format": "API",
             "url": "https://api.example.com/colectivos/posiciones",
             "description": "Ejemplo de recurso enlazado (no subido): un "
                            "endpoint REST externo."},
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
        "fields": {"theme": ["transport", "health"],
                   "temporal_start": "2026-01-01", "temporal_end": "2026-01-31",
                   "geographic_coverage": "city", **CONTACT_CIUDAD},
        "notes": "Viajes diarios del sistema de bicicletas publicas en enero "
                 "de 2026 y ubicacion de las estaciones.",
        "resources": [
            {"name": "Viajes por dia, enero 2026 (CSV)", "format": "CSV",
             "upload": ("bicis-2026-01.csv", CSV_BICIS),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Estaciones (GeoJSON)", "format": "GeoJSON",
             "upload": ("estaciones-bici.geojson", GEOJSON_BICIS),
             "views": ["text_view"]},
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
        "fields": {"theme": ["economy", "government"],
                   "temporal_start": "2025-01-01", "temporal_end": "2025-12-31",
                   "geographic_coverage": "city", **CONTACT_CIUDAD},
        "notes": "Proyectos presentados y votados por barrio en la edicion "
                 "2025, con monto asignado y estado de ejecucion.",
        "resources": [
            {"name": "Proyectos 2025 (CSV)", "format": "CSV",
             "upload": ("presupuesto-participativo-2025.csv",
                        CSV_PRESUPUESTO),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Reglamento (TXT)", "format": "TXT",
             "upload": ("reglamento.txt", TXT_REGLAMENTO),
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
        "fields": {"theme": ["economy"], "temporal_start": "2025-01-01",
                   "temporal_end": "2025-12-31", "geographic_coverage": "country",
                   **CONTACT_INDEC},
        "notes": "Variacion mensual del IPC, nivel general y por division, "
                 "enero de 2024 a diciembre de 2025.",
        "resources": [
            {"name": "IPC mensual por division (CSV)", "format": "CSV",
             "upload": ("ipc-mensual.csv", CSV_IPC),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "IPC mensual por division (JSON)", "format": "JSON",
             "upload": ("ipc-mensual.json", _json(_records(CSV_IPC))),
             "views": ["text_view"]},
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
        "fields": {"theme": ["government"], "temporal_start": "2022-01-01",
                   "temporal_end": "2022-12-31", "geographic_coverage": "province",
                   **CONTACT_INDEC},
        "notes": "Poblacion por departamento en los censos 2010 y 2022, "
                 "variacion, superficie y densidad.",
        "resources": [
            {"name": "Poblacion por departamento (CSV)", "format": "CSV",
             "upload": ("censo-2022-departamentos.csv", CSV_CENSO),
             "datastore": True, "views": ["datatables_view"]},
            {"name": "Cuestionario censal (PDF)", "format": "PDF",
             "upload": ("cuestionario.pdf", _pdf(
                 "Cuestionario censal 2022 (extracto)", [
                     "1. Cuantas personas viven habitualmente en esta vivienda?",
                     "2. Sexo y edad de cada persona.",
                     "3. Lugar de nacimiento y residencia hace cinco anos.",
                     "4. Nivel educativo alcanzado.",
                     "5. Condicion de actividad economica.",
                 ]))},
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
        "fields": {"theme": ["health"], "temporal_start": "2025-01-01",
                   "temporal_end": "2025-12-31", "geographic_coverage": "province"},
        "notes": "Dosis aplicadas por semana, vacuna y grupo etario, primer "
                 "semestre de 2026.",
        "resources": [
            {"name": "Dosis por semana (CSV)", "format": "CSV",
             "upload": ("vacunacion-2026-s1.csv", CSV_VACUNACION),
             "datastore": True, "views": ["datatables_view"]},
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
        "fields": {"theme": ["education"], "temporal_start": "2025-01-01",
                   "temporal_end": "2025-12-31", "geographic_coverage": "country",
                   "language": "en"},
        "notes": "Articulos publicados por facultad y anio, con referato y "
                 "citas recibidas.",
        "resources": [
            {"name": "Publicaciones por facultad (CSV)", "format": "CSV",
             "upload": ("publicaciones.csv", CSV_PUBLICACIONES),
             "datastore": True, "views": ["datatables_view"]},
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
        "fields": {"theme": ["government"], "geographic_coverage": "country"},
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
        "fields": {"theme": ["transport", "health"],
                   "temporal_start": "2025-01-01", "temporal_end": "2025-12-31",
                   "geographic_coverage": "city", **CONTACT_CIUDAD},
        "notes": "Todavia en preparacion: solo lo ven los editores.",
        "state": "draft",
        "resources": [
            {"name": "Siniestros por mes 2025 (CSV)", "format": "CSV",
             "upload": ("siniestros-2025.csv", CSV_SINIESTROS),
             "datastore": True, "views": ["datatables_view"]},
        ],
    },
    {
        "name": "dataset-eliminado",
        "title": "Dataset eliminado",
        "owner_org": "universidad-nacional",
        "groups": [],
        "tags": ["prueba"],
        "license_id": "cc-by",
        "frequency": "eventual",
        "fields": {"theme": ["government"]},
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

    @staticmethod
    def custom_fields(data: dict[str, Any], plugin: str) -> dict[str, Any]:
        """Place the scheming ``fields`` of ``data``: as real fields when
        the scheming plugin is loaded, as extras otherwise."""
        fields = data.pop("fields", {})
        if plugins.plugin_loaded(plugin):
            data.update(fields)
        elif fields:
            data["extras"] = data.get("extras", []) + [
                {"key": key, "value": value if isinstance(value, str)
                 else ", ".join(value)} for key, value in fields.items()]
        return fields

    def patch_fields(self, action: str, existing: dict[str, Any],
                     fields: dict[str, Any], plugin: str) -> None:
        """Bring the scheming fields of an existing object up to date."""
        if not plugins.plugin_loaded(plugin) or not fields:
            return
        if all(existing.get(key) == value for key, value in fields.items()) \
                and not (action == "package_patch" and existing.get("extras")):
            return
        if action == "package_patch":
            # package_update cannot be handed back the relationships that
            # package_show returns (it deletes or breaks them), so the
            # dataset is updated without them; free extras of older demo
            # versions are dropped, the schema fields replace them.
            data = dict(existing, extras=[], **fields)
            data.pop("relationships_as_subject", None)
            data.pop("relationships_as_object", None)
            self.action("package_update", **data)
        else:
            self.action(action, id=existing["id"], **fields)
        self.count("updated")

    def organizations(self) -> None:
        for data in ORGANIZATIONS:
            data = dict(data)
            members = data.pop("users")
            fields = self.custom_fields(data, "scheming_organizations")
            existing = self.exists("organization_show", data["name"])
            if existing is None:
                self.action("organization_create", **data)
                self.count("organizations")
            else:
                self.patch_fields("organization_patch", existing, fields,
                                  "scheming_organizations")
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
            data.setdefault("fields", {})
            data["fields"]["update_frequency"] = FREQUENCY[frequency]
            data["fields"].setdefault("language", "es")
            fields = self.custom_fields(data, "scheming_datasets")
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
            else:
                self.patch_fields("package_patch", dataset, fields,
                                  "scheming_datasets")
            # Resources are reconciled one by one: a run interrupted half
            # way completes on the next one, and a changed definition
            # (e.g. a link that became an upload) updates the instance.
            existing = {r["name"]: r for r in dataset.get("resources", [])}
            wanted = {resource["name"] for resource in resources}
            for resource in resources:
                self.resource(dataset["id"], resource,
                              existing.get(resource["name"]))
            for name, resource in existing.items():
                if name not in wanted:
                    self.action("resource_delete", id=resource["id"])
                    self.count("resources_removed")
            if delete:
                self.action("package_delete", id=dataset["id"])

    def resource(self, package_id: str, data: dict[str, Any],
                 existing: Optional[dict[str, Any]] = None) -> None:
        data = dict(data)
        views = data.pop("views", [])
        datastore = data.pop("datastore", False)
        upload = data.pop("upload", None)
        if upload:
            filename, content = upload
            data["upload"] = FileStorage(io.BytesIO(content), filename)
            data["url"] = filename
        if data.get("format") in ("CSV", "JSON", "GeoJSON", "TXT", "HTML"):
            data.setdefault("encoding", "UTF-8")

        changed = False
        if existing is None:
            resource = self.action("resource_create",
                                   package_id=package_id, **data)
            self.count("resources")
            changed = True
        elif self.resource_differs(existing, data, bool(upload)):
            resource = self.action("resource_patch", id=existing["id"],
                                   **data)
            self.count("resources_updated")
            changed = True
        else:
            resource = existing

        if datastore and upload and plugins.plugin_loaded("datastore") \
                and (changed or not resource.get("datastore_active")):
            self.datastore(resource["id"], upload[1], replace=changed)

        present = {v["view_type"] for v in self.action(
            "resource_view_list", id=resource["id"])}
        for view_type in views:
            if not plugins.plugin_loaded(view_type) or view_type in present:
                continue
            self.action("resource_view_create", resource_id=resource["id"],
                        title=view_type.replace("_", " ").title(),
                        view_type=view_type)
            self.count("resource_views")

    @staticmethod
    def resource_differs(existing: dict[str, Any], data: dict[str, Any],
                         upload: bool) -> bool:
        if upload:
            # a link that should be a file, or a file with another name
            return existing.get("url_type") != "upload" or \
                not (existing.get("url") or "").endswith("/" + data["url"])
        return existing.get("url") != data.get("url") or \
            existing.get("format") != data.get("format") or \
            bool(data.get("encoding") and
                 existing.get("encoding") != data.get("encoding"))

    def datastore(self, resource_id: str, csv_bytes: bytes,
                  replace: bool = False) -> None:
        reader = csv.reader(io.StringIO(csv_bytes.decode("utf-8")))
        header = next(reader)
        records = []
        for line in reader:
            values: list[Any] = []
            for value in line:
                try:
                    values.append(float(value) if "." in value
                                  else int(value))
                except ValueError:
                    values.append(value)
            records.append(dict(zip(header, values)))
        if replace:
            try:
                self.action("datastore_delete", resource_id=resource_id,
                            force=True)
            except logic.NotFound:
                pass
        self.action("datastore_create", resource_id=resource_id,
                    records=records, force=True)
        self.count("datastore_tables")

    def relationships(self) -> None:
        for subject, obj, rel_type, comment in RELATIONSHIPS:
            # checked on the model: package_relationships_list returns
            # detached instances from a CLI process
            subject_pkg = model.Package.get(subject)
            object_pkg = model.Package.get(obj)
            if not subject_pkg or not object_pkg:
                continue
            rel = model.Session.query(model.PackageRelationship).filter_by(
                subject_package_id=subject_pkg.id,
                object_package_id=object_pkg.id, type=rel_type).first()
            if rel is not None and rel.state != "deleted":
                continue
            self.action("package_relationship_create", subject=subject,
                        object=obj, type=rel_type, comment=comment)
            self.count("relationships")

    def collaborators(self) -> None:
        for dataset, user, capacity in COLLABORATORS:
            present = {c["user_id"]: c["capacity"] for c in self.action(
                "package_collaborator_list", id=dataset)}
            user_obj = model.User.get(user)
            if user_obj and present.get(user_obj.id) == capacity:
                continue
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
