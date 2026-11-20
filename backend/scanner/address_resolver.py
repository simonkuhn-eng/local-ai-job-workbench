"""Deterministic Address & Recipient Resolver for Cover Letters.

Extracts company legal names, street addresses, postal codes, contact persons,
and job reference IDs from job descriptions, URLs, and a Bavarian/German employer registry.
Always returns source attribution and verification links so users can audit where data came from.
"""
from __future__ import annotations

import re
import sqlite3
import urllib.parse
from typing import Any

# Registry of verified employer headquarters / main Bavarian & German locations.
# Source: Official company register / Impressum.
COMPANY_REGISTRY: dict[str, dict[str, Any]] = {
    "agile robots": {
        "legal_name": "Agile Robots AG",
        "street": "Staffelseestraße 8",
        "zip_city": "81477 München",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Agile Robots AG",
        "source_url": "https://www.agile-robots.de/impressum/",
    },
    "mbda": {
        "legal_name": "MBDA Deutschland GmbH",
        "street": "Hagenauer Forst 27",
        "zip_city": "86529 Schrobenhausen",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum MBDA Deutschland",
        "source_url": "https://mbda-careers.de/impressum",
    },
    "mbda deutschland": {
        "legal_name": "MBDA Deutschland GmbH",
        "street": "Hagenauer Forst 27",
        "zip_city": "86529 Schrobenhausen",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum MBDA Deutschland",
        "source_url": "https://mbda-careers.de/impressum",
    },
    "quantum systems": {
        "legal_name": "Quantum-Systems GmbH",
        "street": "Dornierstraße 11",
        "zip_city": "82205 Gilching",
        "contact_default": "People & Culture Team",
        "source_label": "Offizielles Impressum Quantum-Systems",
        "source_url": "https://quantum-systems.com/de/impressum/",
    },
    "hensoldt": {
        "legal_name": "HENSOLDT AG",
        "street": "Willy-Messerschmitt-Straße 3",
        "zip_city": "82024 Taufkirchen",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum HENSOLDT AG",
        "source_url": "https://www.hensoldt.net/impressum",
    },
    "airbus": {
        "legal_name": "Airbus Defence and Space GmbH",
        "street": "Willy-Messerschmitt-Straße 1",
        "zip_city": "85521 Ottobrunn",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Airbus Defence and Space",
        "source_url": "https://www.airbus.com/en/legal-notice",
    },
    "airbus defence and space": {
        "legal_name": "Airbus Defence and Space GmbH",
        "street": "Willy-Messerschmitt-Straße 1",
        "zip_city": "85521 Ottobrunn",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Airbus Defence and Space",
        "source_url": "https://www.airbus.com/en/legal-notice",
    },
    "audi": {
        "legal_name": "AUDI AG",
        "street": "Auto-Union-Straße 1",
        "zip_city": "85057 Ingolstadt",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum AUDI AG",
        "source_url": "https://www.audi.de/de/brand/de/tools/navigation/layer/impressum.html",
    },
    "audi ag": {
        "legal_name": "AUDI AG",
        "street": "Auto-Union-Straße 1",
        "zip_city": "85057 Ingolstadt",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum AUDI AG",
        "source_url": "https://www.audi.de/de/brand/de/tools/navigation/layer/impressum.html",
    },
    "bmw": {
        "legal_name": "BMW AG",
        "street": "Petuelring 130",
        "zip_city": "80788 München",
        "contact_default": "BMW Group Recruiting Team",
        "source_label": "Offizielles Impressum BMW AG",
        "source_url": "https://www.bmwgroup.jobs/de/de/impressum.html",
    },
    "bmw group": {
        "legal_name": "BMW AG",
        "street": "Petuelring 130",
        "zip_city": "80788 München",
        "contact_default": "BMW Group Recruiting Team",
        "source_label": "Offizielles Impressum BMW AG",
        "source_url": "https://www.bmwgroup.jobs/de/de/impressum.html",
    },
    "kuka": {
        "legal_name": "KUKA AG",
        "street": "Zugspitzstraße 140",
        "zip_city": "86165 Augsburg",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum KUKA AG",
        "source_url": "https://www.kuka.com/de-de/impressum",
    },
    "rohde & schwarz": {
        "legal_name": "Rohde & Schwarz GmbH & Co. KG",
        "street": "Mühldorfstraße 15",
        "zip_city": "81671 München",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum Rohde & Schwarz",
        "source_url": "https://www.rohde-schwarz.com/de/impressum_63447.html",
    },
    "siemens": {
        "legal_name": "Siemens AG",
        "street": "Werner-von-Siemens-Straße 1",
        "zip_city": "80333 München",
        "contact_default": "Siemens Recruiting Services",
        "source_label": "Offizielles Impressum Siemens AG",
        "source_url": "https://www.siemens.com/global/de/general/impressum.html",
    },
    "siemens energy": {
        "legal_name": "Siemens Energy AG",
        "street": "Otto-Hahn-Ring 6",
        "zip_city": "81739 München",
        "contact_default": "Talent Acquisition Team",
        "source_label": "Offizielles Impressum Siemens Energy",
        "source_url": "https://www.siemens-energy.com/global/en/general/imprint.html",
    },
    "siemens healthineers": {
        "legal_name": "Siemens Healthineers AG",
        "street": "Siemensstr. 3",
        "zip_city": "91301 Forchheim",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Siemens Healthineers AG",
        "source_url": "https://www.siemens-healthineers.com/de/imprint",
    },
    "everllence": {
        "legal_name": "Everllence SE",
        "street": "Stadtbachstraße 1",
        "zip_city": "86153 Augsburg",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum Everllence SE",
        "source_url": "https://www.everllence.com/imprint",
    },
    "man energy solutions": {
        "legal_name": "Everllence SE",
        "street": "Stadtbachstraße 1",
        "zip_city": "86153 Augsburg",
        "contact_default": "Personalabteilung / Recruiting",
        "source_label": "Offizielles Impressum Everllence SE",
        "source_url": "https://www.everllence.com/imprint",
    },
    "infineon": {
        "legal_name": "Infineon Technologies AG",
        "street": "Am Campeon 1-15",
        "zip_city": "85579 Neubiberg",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Infineon Technologies AG",
        "source_url": "https://www.infineon.com/cms/de/about-infineon/imprint/",
    },
    "infineon technologies": {
        "legal_name": "Infineon Technologies AG",
        "street": "Am Campeon 1-15",
        "zip_city": "85579 Neubiberg",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Infineon Technologies AG",
        "source_url": "https://www.infineon.com/cms/de/about-infineon/imprint/",
    },
    "krones": {
        "legal_name": "Krones AG",
        "street": "Böhmerwaldstraße 5",
        "zip_city": "93073 Neutraubling",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Krones AG",
        "source_url": "https://www.krones.com/de/impressum.php",
    },
    "maschinenfabrik reinhausen": {
        "legal_name": "Maschinenfabrik Reinhausen GmbH",
        "street": "Falkensteinstraße 8",
        "zip_city": "93059 Regensburg",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Maschinenfabrik Reinhausen",
        "source_url": "https://www.reinhausen.com/de/impressum",
    },
    "dallmeier": {
        "legal_name": "Dallmeier electronic GmbH & Co.KG",
        "street": "Bahnhofstraße 16",
        "zip_city": "93047 Regensburg",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Dallmeier electronic",
        "source_url": "https://www.dallmeier.com/de/impressum",
    },
    "dallmeier electronic": {
        "legal_name": "Dallmeier electronic GmbH & Co.KG",
        "street": "Bahnhofstraße 16",
        "zip_city": "93047 Regensburg",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Dallmeier electronic",
        "source_url": "https://www.dallmeier.com/de/impressum",
    },
    "avl software and functions": {
        "legal_name": "AVL Software and Functions GmbH",
        "street": "Im Gewerbepark B 29",
        "zip_city": "93059 Regensburg",
        "contact_default": "HR / Recruiting",
        "source_label": "Offizielles Impressum AVL Software and Functions",
        "source_url": "https://www.avl.com/de-de/impressum",
    },
    "innok robotics": {
        "legal_name": "Innok Robotics GmbH",
        "street": "Diesenbach 78",
        "zip_city": "93128 Regenstauf",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Innok Robotics",
        "source_url": "https://www.innok-robotics.de/impressum/",
    },
    "mvtec": {
        "legal_name": "MVTec Software GmbH",
        "street": "Arnulfstraße 205",
        "zip_city": "80634 München",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum MVTec Software",
        "source_url": "https://www.mvtec.com/de/impressum",
    },
    "mvtec software": {
        "legal_name": "MVTec Software GmbH",
        "street": "Arnulfstraße 205",
        "zip_city": "80634 München",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum MVTec Software",
        "source_url": "https://www.mvtec.com/de/impressum",
    },
    "fraunhofer iis": {
        "legal_name": "Fraunhofer-Institut für Integrierte Schaltungen IIS",
        "street": "Am Wolfsmantel 33",
        "zip_city": "91058 Erlangen",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Fraunhofer IIS",
        "source_url": "https://www.iis.fraunhofer.de/de/impressum.html",
    },
    "kinexon": {
        "legal_name": "KINEXON GmbH",
        "street": "Schellingstraße 35",
        "zip_city": "80799 München",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum KINEXON",
        "source_url": "https://kinexon.com/imprint/",
    },
    "se3 labs": {
        "legal_name": "SE3 Labs GmbH",
        "street": "Gollierstraße 70",
        "zip_city": "80339 München",
        "contact_default": "Talent Team",
        "source_label": "Offizielles Impressum SE3 Labs",
        "source_url": "https://www.se3.ai/imprint",
    },
    "proglove": {
        "legal_name": "Workaround GmbH (ProGlove)",
        "street": "Rupert-Mayer-Straße 44",
        "zip_city": "81379 München",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum ProGlove",
        "source_url": "https://www.proglove.com/de/impressum/",
    },
    "twaice": {
        "legal_name": "TWAICE Technologies GmbH",
        "street": "Joseph-Dollinger-Bogen 26",
        "zip_city": "80807 München",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum TWAICE",
        "source_url": "https://www.twaice.com/imprint",
    },
    "deepdrive": {
        "legal_name": "DeepDrive GmbH",
        "street": "Landsberger Straße 234",
        "zip_city": "80687 München",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum DeepDrive",
        "source_url": "https://deepdrive.tech/imprint/",
    },
    "framos": {
        "legal_name": "FRAMOS GmbH",
        "street": "Mehlbeerenstraße 2",
        "zip_city": "82024 Taufkirchen",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum FRAMOS",
        "source_url": "https://www.framos.com/de/impressum",
    },
    "swm": {
        "legal_name": "Stadtwerke München GmbH",
        "street": "Emmy-Noether-Straße 2",
        "zip_city": "80992 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum SWM",
        "source_url": "https://www.swm.de/impressum",
    },
    "tq group": {
        "legal_name": "TQ-Systems GmbH",
        "street": "Mühlstraße 2",
        "zip_city": "82229 Seefeld",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum TQ-Group",
        "source_url": "https://www.tq-group.com/de/impressum/",
    },
    "iabg": {
        "legal_name": "Industrieanlagen-Betriebsgesellschaft mbH (IABG)",
        "street": "Einsteinstraße 20",
        "zip_city": "85521 Ottobrunn",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum IABG",
        "source_url": "https://www.iabg.de/impressum",
    },
    "alpine eagle": {
        "legal_name": "Alpine Eagle GmbH",
        "street": "Dachauer Straße 15",
        "zip_city": "80335 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Alpine Eagle",
        "source_url": "https://www.alpineeagle.com/imprint",
    },
    "navvis": {
        "legal_name": "NavVis GmbH",
        "street": "Blutenburgstraße 18",
        "zip_city": "80636 München",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum NavVis",
        "source_url": "https://www.navvis.com/imprint",
    },
    "tng technology consulting": {
        "legal_name": "TNG Technology Consulting GmbH",
        "street": "Beta-Straße 13a",
        "zip_city": "85774 Unterföhring",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum TNG",
        "source_url": "https://www.tngtech.com/impressum.html",
    },
    "maibornwolff": {
        "legal_name": "MaibornWolff GmbH",
        "street": "Theresienhöhe 13",
        "zip_city": "80339 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum MaibornWolff",
        "source_url": "https://www.maibornwolff.de/impressum/",
    },
    "qaware": {
        "legal_name": "QAware GmbH",
        "street": "Aschauer Straße 32",
        "zip_city": "81549 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum QAware",
        "source_url": "https://www.qaware.de/impressum/",
    },
    "osapiens": {
        "legal_name": "osapiens Services GmbH",
        "street": "Julius-Hatry-Straße 1",
        "zip_city": "68163 Mannheim",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum osapiens",
        "source_url": "https://osapiens.com/imprint/",
    },
    "b plus": {
        "legal_name": "b-plus technologies GmbH",
        "street": "Ulrichsberger Straße 17",
        "zip_city": "94469 Deggendorf",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum b-plus",
        "source_url": "https://www.b-plus.com/de/impressum",
    },
    "adorsys": {
        "legal_name": "adorsys GmbH & Co. KG",
        "street": "Fürther Straße 246a",
        "zip_city": "90429 Nürnberg",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum adorsys",
        "source_url": "https://adorsys.com/impressum/",
    },
    "cariad": {
        "legal_name": "CARIAD SE",
        "street": "Moosacher Str. 86",
        "zip_city": "80809 München",
        "contact_default": "People & Organization",
        "source_label": "Offizielles Impressum CARIAD SE",
        "source_url": "https://cariad.technology/de/de/legal-notice.html",
    },
    "celonis": {
        "legal_name": "Celonis SE",
        "street": "Theresienstraße 6",
        "zip_city": "80333 München",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Celonis SE",
        "source_url": "https://www.celonis.com/de/impressum/",
    },
    "motius": {
        "legal_name": "Motius GmbH",
        "street": "Lothstraße 5",
        "zip_city": "80335 München",
        "contact_default": "Talent Team",
        "source_label": "Offizielles Impressum Motius GmbH",
        "source_url": "https://www.motius.com/imprint",
    },
    "arx robotics": {
        "legal_name": "ARX Robotics GmbH",
        "street": "Landsberger Str. 318a",
        "zip_city": "80687 München",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum ARX Robotics",
        "source_url": "https://arx-robotics.com/impressum",
    },
    "isar aerospace": {
        "legal_name": "Isar Aerospace Technologies GmbH",
        "street": "Schornstraße 6",
        "zip_city": "85640 Putzbrunn",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Isar Aerospace",
        "source_url": "https://www.isaraerospace.com/imprint",
    },
    "rocket factory augsburg": {
        "legal_name": "Rocket Factory Augsburg AG",
        "street": "Berliner Allee 65",
        "zip_city": "86153 Augsburg",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum Rocket Factory Augsburg",
        "source_url": "https://www.rfa.space/imprint/",
    },
    "neura robotics": {
        "legal_name": "NEURA Robotics GmbH",
        "street": "Gottlieb-Daimler-Straße 20",
        "zip_city": "72555 Metzingen",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum NEURA Robotics",
        "source_url": "https://neura-robotics.com/de/impressum/",
    },
    "proxima fusion": {
        "legal_name": "Proxima Fusion GmbH",
        "street": "Gollierstraße 70",
        "zip_city": "80339 München",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum Proxima Fusion",
        "source_url": "https://www.proximafusion.com/imprint",
    },
    "brainlab": {
        "legal_name": "Brainlab AG",
        "street": "Olof-Palme-Straße 9",
        "zip_city": "81829 München",
        "contact_default": "Human Resources",
        "source_label": "Offizielles Impressum Brainlab AG",
        "source_url": "https://www.brainlab.com/de/impressum/",
    },
    "diehl defence": {
        "legal_name": "Diehl Defence GmbH & Co. KG",
        "street": "Alte Parkstraße 1",
        "zip_city": "88662 Überlingen",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum Diehl Defence",
        "source_url": "https://www.diehl.com/defence/de/impressum/",
    },
    "zeiss": {
        "legal_name": "Carl Zeiss AG",
        "street": "Carl-Zeiss-Straße 22",
        "zip_city": "73447 Oberkochen",
        "contact_default": "Recruiting",
        "source_label": "Offizielles Impressum Carl Zeiss AG",
        "source_url": "https://www.zeiss.de/corporate/impressum.html",
    },
    "applied intuition": {
        "legal_name": "Applied Intuition Germany GmbH",
        "street": "Karlstraße 68",
        "zip_city": "80335 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Applied Intuition",
        "source_url": "https://www.appliedintuition.com",
    },
    "mobileye": {
        "legal_name": "Mobileye Germany GmbH",
        "street": "Erika-Mann-Straße 69",
        "zip_city": "80636 München",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Mobileye",
        "source_url": "https://www.mobileye.com",
    },
    "helsing": {
        "legal_name": "Helsing GmbH",
        "street": "Caroline-Michaelis-Straße 8",
        "zip_city": "10115 Berlin",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum Helsing GmbH",
        "source_url": "https://helsing.ai/imprint",
    },
    "bertrandt": {
        "legal_name": "Bertrandt AG",
        "street": "Lilienthalstraße 34",
        "zip_city": "85080 Gaimersheim",
        "contact_default": "Recruiting Center",
        "source_label": "Offizielles Impressum Bertrandt",
        "source_url": "https://www.bertrandt.com/impressum",
    },
    "edag": {
        "legal_name": "EDAG Engineering GmbH",
        "street": "Robert-Bosch-Straße 7",
        "zip_city": "85053 Ingolstadt",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum EDAG",
        "source_url": "https://www.edag.com/de/impressum",
    },
    "scalable capital": {
        "legal_name": "Scalable Capital GmbH",
        "street": "Seitzstraße 8e",
        "zip_city": "80538 München",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Scalable Capital",
        "source_url": "https://de.scalable.capital/impressum",
    },
    "robco": {
        "legal_name": "RobCo GmbH",
        "street": "Feringastraße 6",
        "zip_city": "85774 Unterföhring",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum RobCo",
        "source_url": "https://www.robco.de/impressum",
    },
    "luminovo": {
        "legal_name": "Luminovo GmbH",
        "street": "Brienner Straße 53",
        "zip_city": "80333 München",
        "contact_default": "People Team",
        "source_label": "Offizielles Impressum Luminovo",
        "source_url": "https://luminovo.com/imprint",
    },
    "egym": {
        "legal_name": "EGYM GmbH",
        "street": "Einsteinstraße 172",
        "zip_city": "81677 München",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum EGYM",
        "source_url": "https://egym.com/de/impressum",
    },
    "entrix": {
        "legal_name": "Entrix GmbH",
        "street": "Landsberger Straße 155",
        "zip_city": "80687 München",
        "contact_default": "People & Culture",
        "source_label": "Offizielles Impressum Entrix",
        "source_url": "https://www.entrixenergy.com/imprint",
    },
    "marvel fusion": {
        "legal_name": "Marvel Fusion GmbH",
        "street": "Theresienhöhe 12",
        "zip_city": "80339 München",
        "contact_default": "Talent Acquisition",
        "source_label": "Offizielles Impressum Marvel Fusion",
        "source_url": "https://marvelfusion.com/imprint/",
    },
    "hypersonica": {
        "legal_name": "Hypersonica GmbH",
        "street": "Lise-Meitner-Straße 9",
        "zip_city": "85521 Ottobrunn",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Hypersonica",
        "source_url": "https://www.hypersonica.com",
    },
    "tytan technologies": {
        "legal_name": "TYTAN Technologies GmbH",
        "street": "Karlstraße 68",
        "zip_city": "80335 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum TYTAN Technologies",
        "source_url": "https://www.tytan-technologies.com",
    },
    "machine learning reply": {
        "legal_name": "Machine Learning Reply GmbH",
        "street": "Barthstraße 4",
        "zip_city": "80339 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Reply",
        "source_url": "https://www.reply.com/de/imprint",
    },
    "apple": {
        "legal_name": "Apple GmbH",
        "street": "Karlstraße 19",
        "zip_city": "80333 München",
        "contact_default": "Recruiting Team",
        "source_label": "Offizielles Impressum Apple GmbH",
        "source_url": "https://www.apple.com/de/legal/imprint/",
    },
    "octopus energy": {
        "legal_name": "Octopus Energy Germany GmbH",
        "street": "Augustenstraße 10",
        "zip_city": "80333 München",
        "contact_default": "Talent Team",
        "source_label": "Offizielles Impressum Octopus Energy",
        "source_url": "https://octopusenergy.de/impressum",
    },
    "rewag": {
        "legal_name": "REWAG Regensburger Energie- und Wasserversorgung AG & Co KG",
        "street": "Greflingerstraße 22",
        "zip_city": "93055 Regensburg",
        "contact_default": "Personalabteilung",
        "source_label": "Offizielles Impressum REWAG",
        "source_url": "https://www.rewag.de/impressum",
    },
    "dexory": {
        "legal_name": "Dexory Germany GmbH",
        "street": "",
        "zip_city": "München",
        "contact_default": "Talent Acquisition",
        "source_label": "Karriereseite Dexory",
        "source_url": "https://www.dexory.com",
    },
}

# Regex patterns for German address parsing from free text
STREET_RE = re.compile(
    r'(?:^|[\n,;:|]\s*)([A-ZÄÖÜ][a-zäöüß]+(?:[\s\-]+[A-ZÄÖÜa-zäöüß]+){0,3}?(?:straße|strasse|str\.|weg|platz|allee|ring|gasse|ufer|damm|chaussee|park|campus|forst)\s+\d+[a-zA-Z]?(?:\s*[-/]\s*\d+[a-zA-Z]?)?)\b',
    re.UNICODE | re.MULTILINE,
)
PLZ_CITY_RE = re.compile(
    r'\b(\d{5})\s+([A-ZÄÖÜ][a-zäöüß\.\s\-]+?)(?=[,\n<]|\s{2,}|(?<!\ba)(?<!\bd)(?<!\bi)(?<!\bv)\.(?:\s|$)|$)',
    re.UNICODE,
)
CONTACT_PERSON_RE = re.compile(
    r'(?:Ansprechpartner(?:in)?|Kontakt(?:person)?|Bewerbung(?:en)? an|Ihr(?:e)? Ansprechpartner(?:in)?|Recruiter(?:in)?|Fragen beantwortet(?:\s+Ihnen)?)\s*[:\-–]\s*(?:(?:Herr|Frau|Dr\.|Prof\.)\s+)?([A-ZÄÖÜ][a-zäöüß\-]+(?:[ \t]+[A-ZÄÖÜ][a-zäöüß\-]+){1,2})(?=\s*(?:[,;\n\(]|Tel|Telefon|Phone|E-Mail|Mail|$))',
    re.UNICODE,
)
# Require word boundary, explicit separator (':', '-', '#', or whitespace), AND at least one digit in ref IDs
JOB_REF_RE = re.compile(
    r'\b(?:Referenz(?:\s*nummer|\s*code|\s*nr\.?)?|Ref[\.\-]?\s*(?:Nr\.?|Code|ID)?|Job[\.\-\s]+ID|Req(?:uisition)?[\.\-\s]+(?:Nr\.?|ID)|Stellen[\.\-\s]?ID|Kennziffer|Ausschreibungsnummer)\s*(?:[:\-–#]\s*|\s+)(?=[A-Za-z0-9\-_/]*\d)([A-Za-z0-9][A-Za-z0-9\-_/]{2,24})\b',
    re.IGNORECASE,
)
URL_JOB_REF_RE = re.compile(
    r'(?:job[s]?/|id=|req_id=|reference=|job=)(?=[A-Za-z0-9\-_]*\d)([A-Za-z0-9][A-Za-z0-9\-_]{2,24})(?=[/?&#]|$)',
    re.IGNORECASE,
)

_CITY_SYNONYMS: dict[str, set[str]] = {
    "münchen": {"münchen", "muenchen", "munich"},
    "munich": {"münchen", "muenchen", "munich"},
    "nürnberg": {"nürnberg", "nuernberg", "nuremberg"},
    "nuremberg": {"nürnberg", "nuernberg", "nuremberg"},
    "köln": {"köln", "koeln", "cologne"},
    "cologne": {"köln", "koeln", "cologne"},
    "frankfurt": {"frankfurt", "frankfurt am main", "frankfurt/main"},
    "frankfurt am main": {"frankfurt", "frankfurt am main", "frankfurt/main"},
    "hannover": {"hannover", "hanover"},
    "hanover": {"hannover", "hanover"},
    "regensburg": {"regensburg"},
    "ingolstadt": {"ingolstadt"},
    "augsburg": {"augsburg"},
    "erlangen": {"erlangen"},
    "forchheim": {"forchheim"},
    "garching": {"garching", "garching bei münchen"},
    "ottobrunn": {"ottobrunn", "ottobrunn bei münchen"},
    "taufkirchen": {"taufkirchen", "taufkirchen bei münchen"},
    "unterschleißheim": {"unterschleißheim", "unterschleissheim"},
    "gilching": {"gilching"},
    "oberpfaffenhofen": {"oberpfaffenhofen", "weßling"},
    "neubiberg": {"neubiberg", "münchen", "munich"},
}


def _strip_title_gender_tags(title: str) -> str:
    """Remove gender/diversity markers like (m/w/d), (f/m/d), (all genders) from cover-letter subjects."""
    t = re.sub(
        r"\s*[\(\[\–\-]?\s*\((?:m/w/d|w/m/d|m/f/d|f/m/d|d/m/f|d/f/m|m/w/x|m/f/x|gn|all\s+genders|human|any\s+gender)\)\s*[\)\]]?",
        " ",
        title or "",
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s*[–\-]\s*(?:Teilzeit|Vollzeit)\s*$", "", t, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", t).strip(" -–/,")


def _cities_equivalent(reg_city: str, clean_loc: str) -> bool:
    rc = reg_city.strip().lower()
    cl = clean_loc.strip().lower()
    if not rc or not cl:
        return True
    if rc in cl or cl in rc:
        return True
    syns = _CITY_SYNONYMS.get(rc, {rc})
    return any(re.search(rf"\b{re.escape(syn)}\b", cl) for syn in syns)


def _localize_contact(contact: str, lang: str) -> str:
    if not contact:
        return ""
    if lang == "en":
        c_low = contact.strip().lower()
        if c_low == "personalabteilung":
            return "Recruiting Team"
        if c_low in ("personalabteilung / recruiting", "personalabteilung / bewerbung"):
            return "Recruiting Team"
    return contact


def clean_city_location(raw_loc: str) -> str:
    """Normalize raw location strings into clean city or PLZ/city format."""
    if not raw_loc:
        return ""
    cleaned = raw_loc.split(";")[0].split("|")[0].strip()
    if re.match(r"^Standort prüfen\s*:", cleaned, re.I):
        return ""
    if cleaned.lower() in ("multiple locations", "standort prüfen"):
        return ""
    skip_parts = {"germany", "deutschland", "de", "bavaria", "bayern"}
    parts = [
        p.strip()
        for p in re.split(r'\s*,\s*', cleaned)
        if p.strip() and p.strip() != "-" and p.strip().lower() not in skip_parts
    ]
    if len(parts) >= 2 and parts[-1].lower() == parts[-2].lower():
        parts.pop()
    return ", ".join(parts).strip(" ,-/")


def build_maps_url(company: str, street: str, zip_city: str) -> str:
    """Construct a clean, direct Google Maps search link for verification."""
    query_parts = [p for p in (company, street, zip_city) if p]
    if not query_parts:
        return ""
    encoded = urllib.parse.quote_plus(" ".join(query_parts))
    return f"https://www.google.com/maps/search/?api=1&query={encoded}"


def resolve_letter_address(
    con: sqlite3.Connection | None = None,
    job_id: int | None = None,
    company: str = "",
    location: str = "",
    description: str = "",
    url: str = "",
    source_id: str = "",
    title: str = "",
    lang: str = "de",
) -> dict[str, Any]:
    """Auto-detect recipient address, contact person, and job reference.
    
    Priority order:
    1. Direct match in Job Posting Description (Text extract).
    2. Bavarian / German Employers Registry (Official Impressum).
    3. Job metadata location / URL fallback.
    
    Returns structured recipient fields, source attribution, and verification links.
    Fields without evidence are returned as empty strings ("").
    """
    if job_id and con:
        row = con.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row:
            company = row["company"] or company
            location = row["location"] or location
            description = row["description"] or description
            url = row["url"] or url
            source_id = str(row["source_id"] or "")
            title = row["title"] or title

    res_company = company.strip()
    res_contact = ""
    res_street = ""
    res_zip_city = ""
    res_ref = ""

    source_kind = "fallback"
    source_label = "Keine Anschrift hinterlegt"
    source_url = url or ""
    source_snippet = ""
    confidence = "low"

    # Step 1: Look for explicit Reference ID in description, source_id or URL
    if (
        source_id
        and source_id.isalnum()
        and 3 <= len(source_id) <= 20
        and any(ch.isdigit() for ch in source_id)
        and not source_id.isdigit()
    ):
        res_ref = source_id
    elif description:
        ref_match = JOB_REF_RE.search(description)
        if ref_match:
            candidate = ref_match.group(1).strip()
            if candidate.lower() not in ("referral", "employee", "link", "bonus") and any(ch.isdigit() for ch in candidate):
                res_ref = candidate
    if not res_ref and url:
        url_match = URL_JOB_REF_RE.search(url)
        if url_match:
            res_ref = url_match.group(1).strip()

    # Step 2: Search for address & contact in Job Description
    found_street = ""
    found_zip_city = ""
    found_contact = ""
    found_snippet = ""

    if description:
        street_match = STREET_RE.search(description)
        if street_match:
            found_street = street_match.group(1).strip()
            start = max(0, street_match.start() - 40)
            end = min(len(description), street_match.end() + 60)
            found_snippet = description[start:end].replace("\n", " ").strip()

        plz_match = PLZ_CITY_RE.search(description)
        if plz_match:
            candidate_plz = plz_match.group(1).strip()
            candidate_city = plz_match.group(2).strip(" .,-")
            if 2 <= len(candidate_city) <= 35 and not any(
                w in candidate_city.lower() for w in ("tel", "fax", "mail", "http", "employment", "gmbh", "ag")
            ):
                found_zip_city = f"{candidate_plz} {candidate_city}"

        contact_match = CONTACT_PERSON_RE.search(description)
        if contact_match:
            c_cand = contact_match.group(1).strip()
            c_low = c_cand.lower()
            if (
                len(c_cand) <= 40
                and not any(
                    w in c_low
                    for w in (
                        "uns", "wir", "team", "erfahrung", "people connect", "contact center",
                        "recruiting", "talent acquisition", "human resources", "personalabteilung",
                        "bewerber", "career", "careers", "support",
                    )
                )
            ):
                found_contact = c_cand

    # Step 3: Check Bavarian / German Employers Registry (avoid cross-subsidiary substring mixups)
    norm_comp = re.sub(r'[\s\-_\.,]+', ' ', company.strip().lower()).strip()
    norm_comp_clean = re.sub(r'\b(?:gmbh|ag|se|inc|ltd|corp|co|kg)\b', '', norm_comp).strip()
    norm_comp_clean = re.sub(r'\s{2,}', ' ', norm_comp_clean)
    registry_entry = None
    if norm_comp:
        for cand_key in (norm_comp, norm_comp_clean):
            if cand_key in COMPANY_REGISTRY:
                registry_entry = COMPANY_REGISTRY[cand_key]
                break
        if not registry_entry:
            for k, v in sorted(COMPANY_REGISTRY.items(), key=lambda kv: len(kv[0]), reverse=True):
                if k in ("siemens", "airbus") and norm_comp_clean != k:
                    continue
                if re.search(rf'\b{re.escape(k)}\b', norm_comp_clean or norm_comp):
                    registry_entry = v
                    break

    # Reconcile findings according to priority
    if found_street and found_zip_city:
        res_street = found_street
        res_zip_city = found_zip_city
        default_contact = _localize_contact(registry_entry.get("contact_default", ""), lang) if registry_entry else ""
        res_contact = found_contact or default_contact
        source_kind = "job_posting"
        source_label = "Volltext der Ausschreibung"
        source_url = url or ""
        source_snippet = found_snippet or f"In Ausschreibung gefunden: {res_street}, {res_zip_city}"
        confidence = "high"
    elif registry_entry:
        res_company = registry_entry.get("legal_name", res_company)
        reg_city = re.sub(r'^\d{5}\s*', '', registry_entry.get("zip_city", "")).strip().lower()
        clean_loc = clean_city_location(location)

        differs = bool(clean_loc and reg_city and not _cities_equivalent(reg_city, clean_loc))
        default_contact = _localize_contact(registry_entry.get("contact_default", ""), lang)

        if differs:
            res_street = ""
            res_zip_city = clean_loc
            res_contact = found_contact or default_contact
            source_kind = "job_location"
            source_label = f"Firmensitz: {registry_entry.get('legal_name', res_company)} · Standort laut Ausschreibung"
            source_url = registry_entry.get("source_url", url or "")
            source_snippet = f"{res_company} (Standort: {res_zip_city})"
            confidence = "medium"
        else:
            res_street = registry_entry.get("street", "")
            res_zip_city = registry_entry.get("zip_city", "")
            res_contact = found_contact or default_contact
            source_kind = "company_registry"
            source_label = registry_entry.get("source_label", "Offizielles Unternehmens-Impressum")
            source_url = registry_entry.get("source_url", url or "")
            source_snippet = f"Offizielle Firmenanschrift für {res_company}: {res_street}, {res_zip_city}"
            confidence = "high"
    elif found_street:
        res_street = found_street
        res_zip_city = clean_city_location(location)
        res_contact = found_contact
        source_kind = "job_posting"
        source_label = "Volltext der Ausschreibung (Straße) & Standort"
        source_url = url or ""
        source_snippet = found_snippet
        confidence = "medium"
    else:
        res_street = ""
        res_zip_city = clean_city_location(location)
        res_contact = found_contact
        source_kind = "job_location" if res_zip_city else "fallback"
        source_label = "Standortangabe aus Ausschreibung" if res_zip_city else "Nicht verfügbar"
        source_url = url or ""
        source_snippet = f"Standort aus Anzeige: {location}" if location else "Keine Anschrift in Anzeige gefunden"
        confidence = "fallback"

    # Clean subject line
    subject = ""
    if title:
        clean_title = _strip_title_gender_tags(title)
        subject = f"Application for {clean_title}" if lang == "en" else f"Bewerbung als {clean_title}"
        if res_ref:
            subject += f" (Ref: {res_ref})"

    maps_url = build_maps_url(res_company, res_street, res_zip_city)

    return {
        "company": res_company,
        "contact_person": res_contact,
        "street": res_street,
        "zip_city": res_zip_city,
        "job_ref": res_ref,
        "subject": subject,
        "source": {
            "kind": source_kind,
            "label": source_label,
            "url": source_url,
            "snippet": source_snippet,
            "verification_url": maps_url,
            "confidence": confidence,
        },
    }
