import csv
import json
import mimetypes
import os
import re
import smtplib
from base64 import b64encode
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error as urllib_error
from urllib import request as urllib_request
from uuid import uuid4

from flask import Flask, g, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


def _env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        return int(raw_value)
    except ValueError:
        return default


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "").strip() or "svh-management-local-secret-key"
app.config["MAX_CONTENT_LENGTH"] = max(1, _env_int("MAX_CV_FILE_SIZE_MB", 10)) * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = _env_flag("SESSION_COOKIE_SECURE", False)

if _env_flag("TRUST_PROXY", True):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

if app.secret_key == "svh-management-local-secret-key":
    app.logger.warning(
        "FLASK_SECRET_KEY non défini: secret de développement utilisé. "
        "Définissez FLASK_SECRET_KEY en production."
    )

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
HEROES_DIR = STATIC_DIR / "img" / "heroes"
GALLERY_DIR = STATIC_DIR / "img" / "gallery"
STYLE_CSS_FILE = STATIC_DIR / "style.css"
DEFAULT_DATA_DIR = BASE_DIR / "data"
_raw_data_dir = Path(os.environ.get("SVH_DATA_DIR", str(DEFAULT_DATA_DIR))).expanduser()
DATA_DIR = (_raw_data_dir if _raw_data_dir.is_absolute() else (BASE_DIR / _raw_data_dir)).resolve()

DEFAULT_DRIVE_RESOURCES_FILE = DEFAULT_DATA_DIR / "drive_resources.json"
_raw_drive_resources_file = Path(
    os.environ.get("SVH_DRIVE_RESOURCES_FILE", str(DEFAULT_DRIVE_RESOURCES_FILE))
).expanduser()
DRIVE_RESOURCES_FILE = (
    _raw_drive_resources_file
    if _raw_drive_resources_file.is_absolute()
    else (BASE_DIR / _raw_drive_resources_file)
).resolve()

PREMIUM_LEADS_FILE = DATA_DIR / "premium_leads.csv"
FREELANCE_APPLICATIONS_FILE = DATA_DIR / "freelance_applications.csv"
FREELANCE_CV_DIR = DATA_DIR / "freelance_cvs"
CONTACT_REQUESTS_FILE = DATA_DIR / "contact_requests.csv"
REPLACEMENT_REQUESTS_FILE = DATA_DIR / "replacement_requests.csv"
SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
ALLOWED_CV_EXTENSIONS = {".pdf", ".doc", ".docx"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^[0-9+().\s-]{8,25}$")
ISSUE_NUMBER_PATTERN = re.compile(r"N[°ºo]\s*(\d+)", re.IGNORECASE)
DRIVE_FILE_ID_PATH_PATTERN = re.compile(r"/d/([A-Za-z0-9_-]{10,})")
DRIVE_FILE_ID_QUERY_PATTERN = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
SMTP_HOST = _env_first("SMTP_HOST")
SMTP_PORT = max(1, _env_int("SMTP_PORT", 587))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = _env_flag("SMTP_USE_TLS", True)
SMTP_USE_SSL = _env_flag("SMTP_USE_SSL", False)
SMTP_TIMEOUT_SEC = max(5, _env_int("SMTP_TIMEOUT_SEC", 20))
EMAIL_NOTIFICATIONS_REQUIRED = _env_flag("EMAIL_NOTIFICATIONS_REQUIRED", False)
CONTACT_EMAIL_TO = os.environ.get("CONTACT_EMAIL_TO", "contact@svhmanagement.fr").strip() or "contact@svhmanagement.fr"
CONTACT_EMAIL_FROM = (
    os.environ.get("CONTACT_EMAIL_FROM", "").strip()
    or SMTP_USERNAME
    or CONTACT_EMAIL_TO
)
RESEND_API_KEY = _env_first("RESEND_API_KEY", "RESEND_APIKEY", "RESEND_KEY")
RESEND_API_URL = os.environ.get("RESEND_API_URL", "https://api.resend.com/emails").strip()
RESEND_EMAIL_FROM = os.environ.get("RESEND_EMAIL_FROM", "").strip() or CONTACT_EMAIL_FROM
RESEND_EMAIL_TO = os.environ.get("RESEND_EMAIL_TO", "").strip() or CONTACT_EMAIL_TO
try:
    STYLE_VERSION = str(int(STYLE_CSS_FILE.stat().st_mtime))
except OSError:
    STYLE_VERSION = datetime.utcnow().strftime("%Y%m%d%H%M%S")

HEROES_DIR.mkdir(parents=True, exist_ok=True)
GALLERY_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
FREELANCE_CV_DIR.mkdir(parents=True, exist_ok=True)

SHARED_LOGO = "/static/img/logo-svh.png"
DEFAULT_LANGUAGE = "fr"
LANGUAGE_SESSION_KEY = "site_language"

LANGUAGES = {
    "fr": {"name": "Français", "flag": "🇫🇷"},
    "en": {"name": "English", "flag": "🇬🇧"},
    "es": {"name": "Español", "flag": "🇪🇸"},
    "de": {"name": "Deutsch", "flag": "🇩🇪"},
}

I18N: Dict[str, Dict[str, Any]] = {
    "fr": {
        "brand": {"alt": "S.V.H Management"},
        "menu": {"main_aria": "Menu principal"},
        "language": {
            "title": "Choisir la langue",
            "chooser_aria": "Choix de la langue du site",
        },
        "nav": {
            "home": "Accueil",
            "service": "À votre service",
            "about": "Qui sommes-nous",
            "formations": "Formations",
            "programs": "Notre catalogue de formations",
            "replacements": "Remplacements",
            "consulting": "Conseil & Assistance",
            "resources": "Ressources",
            "contact": "Contact & infos",
        },
        "pages": {
            "service": "À votre service",
            "about": "Qui sommes-nous",
            "formations": "Formations",
            "programs": "Notre catalogue de formations",
            "replacements": "Remplacements",
            "consulting": "Conseil & Assistance",
            "resources": "Ressources",
            "contact": "Contact & infos",
        },
        "home": {
            "title": "L’expertise terrain au service des hôteliers",
            "lines": [
                "S.V.H Management met son expertise terrain au service des hôteliers et des professionnels de la restauration.",
                "Forte de plus de 30 ans d’expérience opérationnelle en hôtellerie-restauration, la société accompagne les exploitants et les investisseurs face aux enjeux actuels du secteur : management des équipes, performance économique, continuité opérationnelle et montée en compétences.",
                "S.V.H Management intervient aujourd’hui dans toute la France pour proposer :",
                "::bell::le remplacement de direction",
                "::bell::le renfort en réception",
                "::bell::la formation aux métiers de l’hôtellerie",
                "::bell::le conseil et l’accompagnement des exploitants",
                "Notre approche repose sur trois piliers essentiels : Services, Valeurs et Humain.",
            ],
            "vision_title": "S.V.H — Une vision engagée du management hôtelier",
            "vision_intro": [
                "Chez SVH Management, nous croyons qu’un hôtel performant ne repose ni sur le hasard, ni uniquement sur les chiffres.",
                "Il repose sur une structure solide, des principes clairs et des femmes et des hommes engagés.",
            ],
            "triptych": [
                {
                    "before": "Les",
                    "letter": "S",
                    "after": "ervices",
                    "body": "Nous intervenons avec méthode, précision et exigence. Structurer, piloter, optimiser : chaque mission est menée avec une approche stratégique et opérationnelle, orientée résultats. Nous sécurisons la continuité d’exploitation et transformons la performance en avantage compétitif durable.",
                },
                {
                    "before": "Les",
                    "letter": "V",
                    "after": "aleurs",
                    "body": "Notre engagement repose sur l’intégrité, la transparence et la culture du résultat. Nous défendons un management responsable, exigeant et loyal, au service d’une performance saine et pérenne. La confiance est le socle de chaque collaboration.",
                },
                {
                    "before": "Les",
                    "letter": "H",
                    "after": "umains",
                    "body": "Parce qu’aucune stratégie ne réussit sans les équipes, nous plaçons l’humain au centre. Fédérer, accompagner, transmettre : nous développons les compétences et révélons les talents pour créer une dynamique collective forte et durable.",
                },
            ],
            "vision_closing": [
                "S.V.H, c’est l’alliance de la rigueur, du sens et de l’engagement.",
                "Une signature. Une méthode. Une exigence.",
            ],
        },
        "service": {
            "title": "S.V.H Management",
            "subtitle": "L’expertise opérationnelle au service des professionnels de l’hôtellerie.",
            "highlights": "Direction d’hôtel • Renfort opérationnel • Formation • Conseil hôtelier",
            "mission_title": "Notre mission",
            "mission": [
                "Accompagner les hôteliers, exploitants et investisseurs dans la performance de leurs établissements grâce à une expertise terrain acquise au cœur de l’exploitation hôtelière.",
            ],
            "experience_title": "Notre expérience",
            "experience_intro": [
                "Fondée par Franck Bougaut, professionnel avec plus de 30 ans d’expérience dans l’hôtellerie-restauration, S.V.H Management s’appuie sur un parcours complet allant :",
            ],
            "experience_steps": [
                "de la cuisine",
                "à la direction d’hôtel",
                "jusqu’à la direction multi-sites au sein d’un groupe hôtelier international.",
            ],
            "experience_outro": "Cette expérience permet aujourd’hui d’apporter une vision globale et pragmatique de l’exploitation hôtelière.",
            "domains_title": "Nos domaines d’intervention",
            "domains_intro": "S.V.H Management intervient dans plusieurs domaines clés :",
            "domains": [
                {
                    "title": "Remplacement de direction",
                    "body": "Nous assurons le management transitoire de votre établissement lors d’une absence, d’un départ ou d’une transition. Prise de poste rapide, pilotage des équipes, suivi des indicateurs et maintien de vos standards de qualité.",
                },
                {
                    "title": "Renfort en réception",
                    "body": "Nous mettons à disposition des profils opérationnels pour sécuriser le front office de jour comme de nuit, fluidifier le parcours client, limiter la surcharge des équipes et préserver la satisfaction de vos clients.",
                },
                {
                    "title": "Formation hôtelière",
                    "body": "Nous concevons des formations adaptées à votre établissement : posture de service, techniques métiers, organisation opérationnelle et montée en compétences de vos équipes terrain et encadrantes.",
                },
                {
                    "title": "Conseil aux exploitants",
                    "body": "Nous accompagnons les exploitants dans l’analyse de l’activité, l’optimisation des processus, l’amélioration de la rentabilité et la structuration durable de leur organisation.",
                },
            ],
            "values_title": "Nos valeurs",
            "values_intro": "S.V.H Management repose sur trois piliers fondamentaux :",
            "values": [
                {
                    "title": "Services",
                    "body": "La qualité de service comme moteur de la satisfaction client.",
                },
                {
                    "title": "Valeurs",
                    "body": "L’engagement, l’éthique et le professionnalisme.",
                },
                {
                    "title": "Humain",
                    "body": "Parce que la réussite d’un hôtel repose avant tout sur les femmes et les hommes qui le font vivre.",
                },
            ],
        },
        "about": {
            "heading": "Expertise en management hôtelier",
            "intro": [
                "S.V.H Management est une société spécialisée dans le management hôtelier, le conseil aux exploitants et la formation aux métiers de l’hôtellerie et de la restauration.",
                "Fondée par Franck Bougaut, professionnel du secteur avec plus de 30 ans d’expérience en exploitation hôtelière, l’entreprise accompagne les hôtels dans l’optimisation de leur organisation, de leur performance et de leurs équipes.",
                "Après un parcours débuté en cuisine, Franck Bougaut évolue rapidement vers des postes de direction d’hôtel puis de direction multi-sites dans un groupe hôtelier international.",
            ],
            "quote": [],
            "list_intro": "Cette expérience lui permet aujourd’hui d’intervenir auprès des professionnels de l’hôtellerie dans plusieurs domaines :",
            "bullets": [
                "remplacement de direction d’hôtel",
                "renfort opérationnel en réception",
                "formation aux métiers de l’hôtellerie",
                "conseil en exploitation hôtelière",
                "accompagnement des investisseurs et exploitants",
            ],
            "values": "S.V.H Management intervient avec une approche basée sur l’expérience terrain, la performance économique et la valorisation des équipes.<br><br>Dans un contexte où le recrutement et la formation deviennent des enjeux majeurs pour les hôtels, S.V.H Management apporte des solutions concrètes pour sécuriser l’exploitation et accompagner la croissance des établissements.",
            "signature_quote": "Passionné par nos métiers et par l’humain, je crois profondément que la réussite passe par les équipes des établissements, avant même le produit lui-même. J’ai malheureusement constaté trop souvent le manque de personnel et le manque de formation pour de soi-disant questions de coûts. Mais a-t-on idée du coût d’une personne non formée ou d’une équipe amputée ? S.V.H Management est née de ces constats.",
            "signature_role": "Gérant S.V.H Management",
        },
        "formations": {
            "heading": "S.V.H Management, organisme de formation en hôtellerie-restauration",
            "catalog_button": "Notre catalogue de formations",
            "paragraphs": [
                "<strong>S.V.H Management</strong> est un organisme de formation spécialisé en hôtellerie-restauration. Forte de plus de 30 ans d’expérience opérationnelle en cuisine, salle, réception, direction d’hôtels et gestion de restaurants, notre structure apporte une expertise terrain immédiatement applicable.",
                "Notre mission est d’accompagner les hôteliers, restaurateurs, exploitants et investisseurs dans la montée en compétences de leurs équipes, l’amélioration de la qualité de service client et la performance durable de leurs établissements.",
                "Chaque formation hôtelière est conçue en amont selon vos objectifs, votre niveau d’exigence et la réalité de votre exploitation, afin d’aligner les savoir-faire, les postures professionnelles et les standards de votre marque.",
                "Nos programmes couvrent les métiers clés de l’hôtellerie-restauration : accueil, réception, management d’équipe, organisation opérationnelle, relation client et pilotage de la performance.",
                "Avec une charte qualité exigeante, nous déployons des formations professionnelles structurées et orientées résultats, pour développer des compétences immédiatement mobilisables sur le terrain.",
                "Parce que chaque établissement est unique, nous construisons des parcours de formation sur mesure en intégrant vos valeurs d’entreprise, vos procédures internes et votre promesse de service.",
                "Nous pouvons vous accompagner dans vos démarches de prise en charge auprès de votre OPCO AKTO, afin de répondre à vos obligations légales de formation et de sécuriser vos investissements.",
                "Toutes nos formations sont disponibles en présentiel et peuvent être digitalisées pour le distanciel, avec un objectif clair : renforcer durablement la performance humaine et opérationnelle de votre établissement.",
            ],
        },
        "programs": {
            "image_alt": "Programme de formation",
            "catalog_image_alt": "Catalogue de formations",
            "catalog_intro": "Retrouvez ici tous les programmes de formations disponibles en aperçu et en téléchargement.",
            "catalog_empty": "Aucun programme n'est disponible pour le moment.",
            "filter_label": "Filtrer par thème",
            "filter_all": "Tous les programmes",
            "filter_empty": "Aucun programme ne correspond à ce filtre.",
            "filters": {
                "gestion": "Gestion & Revenue",
                "pms": "PMS & Outils",
                "hygiene": "Hygiène & Qualité",
                "relation": "Relation client",
                "marketing": "Marketing visuel",
            },
        },
        "replacements": {
            "intro": "<strong>S.V.H Management</strong> propose des solutions de <strong>remplacement d’effectifs en hôtellerie</strong> pour garantir la continuité d’exploitation de votre établissement :",
            "offers": [
                "<strong>Remplacement de direction et management de transition hôtelier</strong> : prise en main rapide des opérations pour sécuriser le pilotage, les équipes et les indicateurs de performance pendant toute période d’absence.",
                "<strong>Renfort en réception (Adjoint de direction, Réceptionniste, Réceptionniste de nuit)</strong> : intervenants immédiatement opérationnels, formés au PMS, pour maintenir la qualité d’accueil et la fluidité du front office.",
            ],
            "paragraphs": [
                "Qu’il s’agisse d’un départ, d’un imprévu ou d’un pic d’activité, l’absence d’un Directeur, d’un General Manager ou d’un collaborateur clé fragilise immédiatement l’organisation d’un hôtel.",
                "En confiant votre remplacement à <strong>S.V.H Management</strong>, vous conservez un haut niveau de service tout en vous donnant le temps d’identifier une solution durable.",
                "Cette continuité opérationnelle limite les risques majeurs : désorganisation des équipes, perte de motivation, baisse du chiffre d’affaires et dégradation de l’expérience client.",
                "Un établissement sans leadership ou sans effectif suffisant, c’est une exécution qui se dégrade et une performance qui recule.",
                "<strong>S.V.H Management</strong> mobilise des profils expérimentés, sélectionnés pour leur sens des responsabilités, leur intégrité et leur maîtrise des exigences du secteur hôtelier.",
                "Nos missions de remplacement sont conçues pour réduire la pression managériale, sécuriser vos opérations et préserver la dynamique de vos équipes.",
                "Chaque intervenant arrive avec une expérience terrain solide et devient opérationnel dès son arrivée sur site.",
            ],
            "form_title": "J'ai un besoin de remplacement.",
            "request_success": "Votre demande de remplacement a bien été envoyée. Merci !",
            "request_errors": {
                "required": "Tous les champs sont obligatoires.",
                "invalid_email": "Adresse email invalide.",
                "invalid_phone": "Numéro de téléphone invalide.",
                "save_failed": "L'envoi a échoué. Veuillez réessayer.",
                "email_failed": "Votre demande est enregistrée, mais l'email de notification a échoué. Merci de nous appeler au 07 67 31 47 55.",
            },
            "freelance_title": "Vous aussi, vous voulez rejoindre l'aventure S.V.H Management ?",
            "freelance_button": "Je suis Freelance",
            "freelance_intro": "Déposez votre CV pour nous proposer votre candidature et rejoindre de futures missions au sein de S.V.H Management.",
            "freelance_success": "Votre candidature freelance a bien été envoyée. Merci !",
            "freelance_errors": {
                "required": "Tous les champs et le CV sont obligatoires.",
                "invalid_email": "Adresse email invalide.",
                "invalid_phone": "Numéro de téléphone invalide.",
                "cv_required": "Merci de joindre votre CV.",
                "cv_extension": "Format CV non accepté. Utilisez PDF, DOC ou DOCX.",
                "upload_failed": "Le dépôt du CV a échoué. Veuillez réessayer.",
                "email_failed": "Votre candidature est enregistrée, mais l'email de notification a échoué. Merci de nous appeler au 07 67 31 47 55.",
            },
            "script": {
                "subject_prefix": "Demande de remplacement - ",
                "subject_fallback": "Non précisé",
                "line_last_name": "Nom : ",
                "line_first_name": "Prénom : ",
                "line_email": "Mail : ",
                "line_phone": "Téléphone : ",
                "line_position": "Poste recherché : ",
                "line_message_title": "Espace texte :",
            },
        },
        "consulting": {
            "paragraphs": [
                "<strong>S.V.H Management</strong> propose un service de <strong>conseil hôtelier</strong> pour accompagner la restructuration de votre établissement, l’évolution de votre organisation et l’optimisation de vos process opérationnels.",
                "De la gestion des OTA (Online Travel Agencies) aux dossiers d’exploitation, en passant par les obligations légales et l’amélioration des flux <strong>back office</strong> / <strong>front office</strong>, nous définissons des actions concrètes pour renforcer votre performance.",
                "Notre <strong>audit 360</strong> identifie précisément vos points forts, vos axes de progrès et vos priorités stratégiques afin de bâtir un plan d’action clair, mesurable et adapté à la réalité de votre exploitation hôtelière.",
                "<strong>L’assistance aux hôteliers</strong> complète ce dispositif avec un appui administratif et commercial sur mesure, ponctuel ou régulier, mené avec discrétion, intégrité, rigueur et confidentialité pour sécuriser votre quotidien.",
            ]
        },
        "resources": {
            "intro_title": "Le Petit Hôtelier",
            "intro_text": "Le Petit Hôtelier, le guide pratique créé par un hôtelier, pour les hôteliers et leurs équipes. Comprendre ses KPI, calculer ses indicateurs, découvrir notre jargon, nos méthodes de fonctionnement et bien plus encore. Inscris-toi et télécharge gratuitement les Petits Hôteliers ☺️.",
            "ticker_aria": "Bandeau défilant des ressources déverrouillées",
            "cover_alt": "Aperçu de couverture : {title}",
            "scroll_prev": "Défiler vers la gauche",
            "scroll_next": "Défiler vers la droite",
            "select_label": "Sélectionner un titre",
            "select_button": "Aller",
            "premium_title": "Accès Premium aux Ressources",
            "premium_desc": "Renseignez vos informations pour débloquer l’accès aux fichiers téléchargeables.",
            "unlock_button": "Débloquer l’accès",
            "access_validated": "Accès premium validé",
            "for": "pour",
            "files_available": "{count} fichier(s) disponible(s) au téléchargement.",
            "download": "Télécharger",
            "preview": "Aperçu",
            "no_files": "Aucun fichier n’a pu être chargé depuis le dossier Drive.",
            "errors": {
                "required": "Tous les champs sont obligatoires pour obtenir l’accès premium.",
                "invalid_email": "Adresse email invalide.",
                "invalid_phone": "Numéro de téléphone invalide.",
            },
        },
        "contact": {
            "intro_title": "Un projet ? Un besoin ? Parlons-en !",
            "map_aria": "Intervention partout en France",
            "map_alt": "Carte de France",
            "map_note": "Intervention partout en France",
            "coords_aria": "Coordonnées",
            "linkedin_label": "Restons en contact sur les réseaux !",
            "linkedin_aria": "Profil LinkedIn de Franck Bougaut",
            "google_reviews_label": "Laissez-nous un avis.",
            "google_reviews_cta": "Laisser un avis Google",
            "google_reviews_aria": "Donner un avis Google à S.V.H Management",
            "form_title": "Formulaire de contact",
            "form_success": "Votre message a bien été envoyé. Merci !",
            "errors": {
                "required": "Tous les champs du formulaire sont obligatoires.",
                "invalid_email": "Adresse email invalide.",
                "save_failed": "L'envoi a échoué. Veuillez réessayer.",
                "email_failed": "Votre message est enregistré, mais l'email de notification a échoué. Merci de nous appeler au 07 67 31 47 55.",
            },
            "script": {
                "default_subject": "Demande de contact - SVH Management",
                "line_name": "Nom : ",
                "line_email": "Email : ",
                "line_message_title": "Message :",
            },
        },
        "forms": {
            "name": "Nom",
            "first_name": "Prénom",
            "last_name": "Nom",
            "email": "Email",
            "mail": "Mail",
            "phone": "Téléphone",
            "geo_area": "Zone géographique d'intervention",
            "available_job": "Métier disponible",
            "cv_file": "Déposer votre CV",
            "cv_help": "Formats acceptés : PDF, DOC, DOCX.",
            "subject": "Objet",
            "message": "Message",
            "text_area": "Espace texte",
            "send": "Envoyer",
            "send_request": "Envoyer la demande",
            "send_application": "Envoyer ma candidature",
            "help_mailto": "En cliquant sur Envoyer, votre application email s’ouvre avec le message pré-rempli vers contact@svhmanagement.fr.",
            "position_label": "Poste recherché",
            "position_placeholder": "Sélectionnez un poste",
            "position_direction": "Direction",
            "position_receptionist": "Réceptionniste",
            "position_reception": "Réception",
            "position_kitchen": "Cuisine",
            "position_service": "Salle",
            "position_housekeeping": "Étages",
            "position_training": "Formations",
            "position_other": "Autre",
        },
    },
    "en": {
        "brand": {"alt": "S.V.H Management"},
        "menu": {"main_aria": "Main menu"},
        "language": {
            "title": "Choose language",
            "chooser_aria": "Website language selector",
        },
        "nav": {
            "home": "Home",
            "service": "At your service",
            "about": "Who are we?",
            "formations": "Training",
            "programs": "Our training catalogue",
            "replacements": "Replacements",
            "consulting": "Consulting & Support",
            "resources": "Resources",
            "contact": "Contact & info",
        },
        "pages": {
            "service": "At your service",
            "about": "Who are we?",
            "formations": "Training",
            "programs": "Our training catalogue",
            "replacements": "Replacements",
            "consulting": "Consulting & Support",
            "resources": "Resources",
            "contact": "Contact & info",
        },
        "home": {
            "title": "Serving Hospitality Professionals!",
            "lines": [
                "S.V.H Management puts its field expertise at the service of hoteliers and hospitality professionals.",
                "Backed by over 30 years of operational experience in hospitality and food service, the company supports operators and investors with today’s sector challenges: team management, economic performance, operational continuity, and skills development.",
                "S.V.H Management now operates across France to provide:",
                "::bell::management replacement",
                "::bell::front desk reinforcement",
                "::bell::hospitality training",
                "::bell::advisory and support for operators",
                "Our approach is based on three essential pillars: Service, Values, and Human.",
            ],
            "vision_title": "S.V.H — A committed vision of hotel management",
            "vision_intro": [
                "At SVH Management, we believe high-performing hotels are not built by chance or by numbers alone.",
                "They are built on strong structure, clear principles, and committed people.",
            ],
            "triptych": [
                {
                    "before": "",
                    "letter": "S",
                    "after": "for Service",
                    "body": "We act with method, precision and high standards. Structure, lead, optimize: each mission combines strategic and operational execution focused on measurable results and business continuity.",
                },
                {
                    "before": "",
                    "letter": "V",
                    "after": "for Values",
                    "body": "Our commitment is grounded in integrity, transparency and results. We stand for responsible, demanding and loyal management, built on trust and long-term performance.",
                },
                {
                    "before": "",
                    "letter": "H",
                    "after": "for Human",
                    "body": "No strategy succeeds without teams. We place people at the center: align, support and transfer know-how to develop skills and reveal talent for lasting collective momentum.",
                },
            ],
            "vision_closing": [
                "S.V.H is the alliance of rigor, meaning and commitment.",
                "A signature. A method. A high standard.",
            ],
        },
        "service": {
            "title": "S.V.H Management",
            "subtitle": "Operational expertise serving hospitality professionals.",
            "highlights": "Hotel management • Operational support • Training • Hotel consulting",
            "mission_title": "Our mission",
            "mission": [
                "Support hoteliers, operators, and investors in improving property performance through field expertise built in day-to-day hotel operations.",
            ],
            "experience_title": "Our experience",
            "experience_intro": [
                "Founded by Franck Bougaut, a professional with more than 30 years of hospitality experience, S.V.H Management is built on a complete career path going:",
            ],
            "experience_steps": [
                "from kitchen operations",
                "to hotel management",
                "up to multi-site leadership within an international hotel group.",
            ],
            "experience_outro": "This experience now provides a global and pragmatic vision of hotel operations.",
            "domains_title": "Our areas of intervention",
            "domains_intro": "S.V.H Management operates in several key areas:",
            "domains": [
                {
                    "title": "Management replacement",
                    "body": "Ensure business continuity and property performance.",
                },
                {
                    "title": "Front desk support",
                    "body": "Strengthen your reception teams by day and by night.",
                },
                {
                    "title": "Hospitality training",
                    "body": "Train teams to meet sector standards and expectations.",
                },
                {
                    "title": "Operator consulting",
                    "body": "Support hoteliers in managing and optimizing their property.",
                },
            ],
            "values_title": "Our values",
            "values_intro": "S.V.H Management is built on three core pillars:",
            "values": [
                {
                    "title": "Service",
                    "body": "Service quality as a driver of customer satisfaction.",
                },
                {
                    "title": "Values",
                    "body": "Commitment, ethics, and professionalism.",
                },
                {
                    "title": "People",
                    "body": "Because hotel success is first and foremost built by the people behind it.",
                },
            ],
        },
        "about": {
            "heading": "Expertise in hotel management",
            "intro": [
                "S.V.H Management is a company specialized in hotel management, operator consulting, and training for hospitality and food service professions.",
                "Founded by Franck Bougaut, a sector professional with more than 30 years of experience in hotel operations, the company supports hotels in optimizing their organization, performance, and teams.",
                "After starting his career in the kitchen, Franck Bougaut quickly moved into hotel management roles and then multi-site leadership within an international hotel group.",
            ],
            "quote": [],
            "list_intro": "This experience now allows him to support hospitality professionals in several areas:",
            "bullets": [
                "hotel management replacement",
                "operational front desk support",
                "training for hospitality professions",
                "hotel operations consulting",
                "support for investors and operators",
            ],
            "values": "S.V.H Management works with an approach based on field experience, economic performance, and team development.<br><br>In a context where recruitment and training are becoming major challenges for hotels, S.V.H Management provides concrete solutions to secure operations and support property growth.",
            "signature_quote": "",
            "signature_role": "Managing Director S.V.H Management",
        },
        "formations": {
            "heading": "S.V.H Management training organization",
            "catalog_button": "Our training catalogue",
            "paragraphs": [
                "Our founder's 30+ years of expertise cover kitchen, service, front desk, hotel and restaurant management, sales and team leadership.",
                "To support and upskill your teams, and reinforce <strong>Service</strong>, company <strong>Values</strong> and <strong>People</strong>, <strong>S.V.H Management</strong> designs training programs for your hospitality operations.",
                "Our programs are designed upstream according to your level of expectations so that your team's posture and communication reflect <strong>your venue identity.</strong>",
                "As hospitality experts, we deliver programs with a quality charter and optimal conditions to build targeted job skills.",
                "<strong>S.V.H Management</strong> puts special focus on integrating your business and human values into each training plan.",
                "The quality of our programs meets your establishment's quality standards and expectations.",
                "Funding requests can be prepared with your OPCO AKTO to meet legal training obligations.",
                "All our training programs are tailor-made and available on-site or digital for remote formats.",
            ],
        },
        "programs": {
            "image_alt": "Training program",
            "catalog_image_alt": "Training catalogue",
            "catalog_intro": "Find all training programs here, available for preview and download.",
            "catalog_empty": "No training program is currently available.",
            "filter_label": "Filter by topic",
            "filter_all": "All programs",
            "filter_empty": "No program matches this filter.",
            "filters": {
                "gestion": "Management & Revenue",
                "pms": "PMS & Tools",
                "hygiene": "Hygiene & Quality",
                "relation": "Customer relation",
                "marketing": "Visual marketing",
            },
        },
        "replacements": {
            "intro": "<strong>S.V.H Management</strong> offers replacement services:",
            "offers": [
                "<strong>Temporary Executive Management</strong> offers are built to cover management absences.",
                "<strong>Assistant Manager, Front Desk Agent and Night Front Desk Agent</strong> offers are designed to cover reception staff absences. All our consultants are PMS-trained and immediately operational.",
            ],
            "paragraphs": [
                "Whether caused by departure or emergency, the absence of a Director, General Manager or key team member is a major handicap for a hotel.",
                "This impact can be reduced by relying on a professional who gives you time to stabilize and secure a long-term solution.",
                "Team destabilization, motivation loss and revenue decline are real risks when management or staff positions are vacant.",
                "Imagine an orchestra without a conductor, or a sports team without its captain.",
                "Responsibility, integrity and professional skills are the strengths <strong>S.V.H Management</strong> brings to replacement assignments.",
                "<strong>S.V.H Management</strong> designed its replacement offers to reduce stress linked to missing key roles.",
                "Our consultant arrives with strong operational experience and is effective from day one.",
            ],
            "form_title": "I have a replacement need.",
            "freelance_title": "Would you also like to join the S.V.H Management adventure?",
            "freelance_button": "I am Freelance",
            "freelance_intro": "Submit your CV to apply and join future assignments with S.V.H Management.",
            "freelance_success": "Your freelance application has been sent successfully. Thank you!",
            "freelance_errors": {
                "required": "All fields and the CV are required.",
                "invalid_email": "Invalid email address.",
                "invalid_phone": "Invalid phone number.",
                "cv_required": "Please attach your CV.",
                "cv_extension": "Unsupported CV format. Use PDF, DOC, or DOCX.",
                "upload_failed": "CV upload failed. Please try again.",
            },
            "script": {
                "subject_prefix": "Replacement request - ",
                "subject_fallback": "Not specified",
                "line_last_name": "Last name: ",
                "line_first_name": "First name: ",
                "line_email": "Email: ",
                "line_phone": "Phone: ",
                "line_position": "Target role: ",
                "line_message_title": "Message:",
            },
        },
        "consulting": {
            "paragraphs": [
                "<strong>S.V.H Management</strong> also provides consulting services for restructuring, process redesign and operational evolution.",
                "A 360 audit of your property helps identify what works and what does not, then define concrete actions.",
                "We also provide dedicated support for <strong>administrative tasks</strong> with discretion, rigor and confidentiality.",
                "<strong>Support for hoteliers</strong> covers occasional or recurring commercial and administrative tasks specific to hospitality.",
            ]
        },
        "resources": {
            "intro_title": "The Little Hotelier",
            "intro_text": "The Little Hotelier is a practical guide created by a hotelier for hoteliers and their teams. Understand KPIs, calculate indicators, learn our industry language and much more. Sign up and download it for free ☺️.",
            "ticker_aria": "Scrolling strip of unlocked resources",
            "cover_alt": "Cover preview: {title}",
            "scroll_prev": "Scroll left",
            "scroll_next": "Scroll right",
            "select_label": "Select a title",
            "select_button": "Go",
            "premium_title": "Premium Access to Resources",
            "premium_desc": "Fill in your information to unlock downloadable files.",
            "unlock_button": "Unlock access",
            "access_validated": "Premium access granted",
            "for": "for",
            "files_available": "{count} file(s) available for download.",
            "download": "Download",
            "preview": "Preview",
            "no_files": "No file could be loaded from the Drive folder.",
            "errors": {
                "required": "All fields are required to get premium access.",
                "invalid_email": "Invalid email address.",
                "invalid_phone": "Invalid phone number.",
            },
        },
        "contact": {
            "intro_title": "A project? A need? Let's talk!",
            "map_aria": "Interventions across France",
            "map_alt": "Map of France",
            "map_note": "Interventions across France",
            "coords_aria": "Contact details",
            "linkedin_label": "Let's stay connected on social networks!",
            "linkedin_aria": "Franck Bougaut LinkedIn profile",
            "google_reviews_label": "Your feedback matters",
            "google_reviews_cta": "Leave a Google review",
            "google_reviews_aria": "Leave a Google review for S.V.H Management",
            "form_title": "Contact form",
            "script": {
                "default_subject": "Contact request - SVH Management",
                "line_name": "Name: ",
                "line_email": "Email: ",
                "line_message_title": "Message:",
            },
        },
        "forms": {
            "name": "Name",
            "first_name": "First name",
            "last_name": "Last name",
            "email": "Email",
            "mail": "Email",
            "phone": "Phone",
            "geo_area": "Geographic area of intervention",
            "available_job": "Available role",
            "cv_file": "Upload your CV",
            "cv_help": "Accepted formats: PDF, DOC, DOCX.",
            "subject": "Subject",
            "message": "Message",
            "text_area": "Message area",
            "send": "Send",
            "send_request": "Send request",
            "send_application": "Send my application",
            "help_mailto": "By clicking Send, your email app opens with a pre-filled message to contact@svhmanagement.fr.",
            "position_label": "Target role",
            "position_placeholder": "Select a role",
            "position_direction": "Management",
            "position_receptionist": "Receptionist",
            "position_reception": "Front desk",
            "position_kitchen": "Kitchen",
            "position_service": "Service",
            "position_housekeeping": "Housekeeping",
            "position_training": "Training",
            "position_other": "Other",
        },
    },
    "es": {
        "brand": {"alt": "S.V.H Management"},
        "menu": {"main_aria": "Menú principal"},
        "language": {
            "title": "Elegir idioma",
            "chooser_aria": "Selector de idioma del sitio",
        },
        "nav": {
            "home": "Inicio",
            "service": "A su servicio",
            "about": "¿Quiénes somos?",
            "formations": "Formación",
            "programs": "Nuestro catálogo de formación",
            "replacements": "Sustituciones",
            "consulting": "Consejo y asistencia",
            "resources": "Recursos",
            "contact": "Contacto e info",
        },
        "pages": {
            "service": "A su servicio",
            "about": "¿Quiénes somos?",
            "formations": "Formación",
            "programs": "Nuestro catálogo de formación",
            "replacements": "Sustituciones",
            "consulting": "Consejo y asistencia",
            "resources": "Recursos",
            "contact": "Contacto e info",
        },
        "home": {
            "title": "¡Al servicio de los hoteleros!",
            "lines": [
                "S.V.H Management pone su experiencia de terreno al servicio de los hoteleros y profesionales de la hostelería.",
                "Con más de 30 años de experiencia operativa en hostelería y restauración, la empresa acompaña a explotadores e inversores ante los retos actuales del sector: gestión de equipos, rendimiento económico, continuidad operativa y desarrollo de competencias.",
                "S.V.H Management interviene hoy en toda Francia para ofrecer:",
                "::bell::sustitución de dirección",
                "::bell::refuerzo en recepción",
                "::bell::formación en oficios de la hostelería",
                "::bell::asesoría y acompañamiento a explotadores",
                "Nuestra metodología se basa en tres pilares esenciales: Servicios, Valores y Humano.",
            ],
            "vision_title": "S.V.H — Una visión comprometida del management hotelero",
            "vision_intro": [
                "En SVH Management creemos que un hotel eficiente no se basa en el azar ni únicamente en los números.",
                "Se basa en una estructura sólida, principios claros y equipos comprometidos.",
            ],
            "triptych": [
                {
                    "before": "",
                    "letter": "S",
                    "after": "de Servicio",
                    "body": "Intervenimos con método, precisión y exigencia. Estructurar, dirigir y optimizar: cada misión combina enfoque estratégico y operativo orientado a resultados y continuidad.",
                },
                {
                    "before": "",
                    "letter": "V",
                    "after": "de Valores",
                    "body": "Nuestro compromiso se apoya en la integridad, la transparencia y la cultura del resultado. Defendemos una gestión responsable y leal, con la confianza como base de cada colaboración.",
                },
                {
                    "before": "",
                    "letter": "H",
                    "after": "de Humano",
                    "body": "Ninguna estrategia funciona sin las personas. Ponemos al equipo en el centro para federar, acompañar y transmitir, desarrollando competencias y talento de forma duradera.",
                },
            ],
            "vision_closing": [
                "S.V.H es la alianza entre rigor, sentido y compromiso.",
                "Una firma. Un método. Una exigencia.",
            ],
        },
        "service": {
            "title": "S.V.H Management",
            "subtitle": "La experiencia operativa al servicio de los profesionales de la hostelería.",
            "highlights": "Dirección hotelera • Refuerzo operativo • Formación • Consultoría hotelera",
            "mission_title": "Nuestra misión",
            "mission": [
                "Acompañar a hoteleros, explotadores e inversores en el rendimiento de sus establecimientos gracias a una experiencia de terreno adquirida en el corazón de la explotación hotelera.",
            ],
            "experience_title": "Nuestra experiencia",
            "experience_intro": [
                "Fundada por Franck Bougaut, profesional con más de 30 años de experiencia en hostelería-restauración, S.V.H Management se apoya en una trayectoria completa que va:",
            ],
            "experience_steps": [
                "desde la cocina",
                "hasta la dirección hotelera",
                "y la dirección multi-sede en un grupo hotelero internacional.",
            ],
            "experience_outro": "Esta experiencia permite aportar hoy una visión global y pragmática de la explotación hotelera.",
            "domains_title": "Nuestras áreas de intervención",
            "domains_intro": "S.V.H Management interviene en varios ámbitos clave:",
            "domains": [
                {
                    "title": "Sustitución de dirección",
                    "body": "Asegurar la continuidad y el rendimiento de su establecimiento.",
                },
                {
                    "title": "Refuerzo en recepción",
                    "body": "Reforzar sus equipos de recepción de día y de noche.",
                },
                {
                    "title": "Formación hotelera",
                    "body": "Formar a los equipos según los estándares y exigencias del sector.",
                },
                {
                    "title": "Consultoría para explotadores",
                    "body": "Acompañar a los hoteleros en la gestión y optimización de su establecimiento.",
                },
            ],
            "values_title": "Nuestros valores",
            "values_intro": "S.V.H Management se basa en tres pilares fundamentales:",
            "values": [
                {
                    "title": "Servicios",
                    "body": "La calidad del servicio como motor de la satisfacción del cliente.",
                },
                {
                    "title": "Valores",
                    "body": "Compromiso, ética y profesionalidad.",
                },
                {
                    "title": "Humano",
                    "body": "Porque el éxito de un hotel depende ante todo de las mujeres y los hombres que le dan vida.",
                },
            ],
        },
        "about": {
            "heading": "Experiencia en gestión hotelera",
            "intro": [
                "S.V.H Management es una empresa especializada en gestión hotelera, asesoría a explotadores y formación en los oficios de la hostelería y la restauración.",
                "Fundada por Franck Bougaut, profesional del sector con más de 30 años de experiencia en explotación hotelera, la empresa acompaña a los hoteles en la optimización de su organización, su rendimiento y sus equipos.",
                "Tras una trayectoria iniciada en cocina, Franck Bougaut evolucionó rápidamente hacia puestos de dirección hotelera y luego de dirección multi-sitio dentro de un grupo hotelero internacional.",
            ],
            "quote": [],
            "list_intro": "Esta experiencia le permite hoy intervenir junto a profesionales de la hostelería en varios ámbitos:",
            "bullets": [
                "sustitución de dirección hotelera",
                "refuerzo operativo en recepción",
                "formación en los oficios de la hostelería",
                "asesoría en explotación hotelera",
                "acompañamiento a inversores y explotadores",
            ],
            "values": "S.V.H Management interviene con un enfoque basado en la experiencia de terreno, el rendimiento económico y la valorización de los equipos.<br><br>En un contexto donde la contratación y la formación se vuelven retos clave para los hoteles, S.V.H Management aporta soluciones concretas para asegurar la explotación y acompañar el crecimiento de los establecimientos.",
            "signature_quote": "",
            "signature_role": "Gerente S.V.H Management",
        },
        "formations": {
            "heading": "Organismo de formación S.V.H Management",
            "catalog_button": "Nuestro catálogo de formación",
            "paragraphs": [
                "La experiencia del fundador abarca más de 30 años en cocina, sala, recepción, gestión hotelera y restauración, comercialización y dirección de equipos.",
                "Para acompañar y desarrollar las competencias de tus equipos, y reforzar <strong>Servicio</strong>, <strong>Valores</strong> y <strong>Humano</strong>, <strong>S.V.H Management</strong> diseña formaciones para tus operaciones hoteleras.",
                "Nuestras formaciones se diseñan según tu nivel de exigencia para que el discurso y los gestos del personal reflejen <strong>la identidad de tu establecimiento.</strong>",
                "Como expertos en hostelería, implementamos formaciones con carta de calidad y condiciones óptimas para aportar competencias concretas.",
                "<strong>S.V.H Management</strong> integra tus valores empresariales y humanos en cada programa.",
                "La calidad de nuestras formaciones responde a los criterios de tu establecimiento.",
                "Podemos ayudarte con solicitudes de financiación ante tu OPCO AKTO para cumplir obligaciones legales de formación.",
                "Todas nuestras formaciones son a medida y disponibles en presencial y formato digital.",
            ],
        },
        "programs": {
            "image_alt": "Programa de formación",
            "catalog_image_alt": "Catálogo de formación",
            "catalog_intro": "Encuentra aquí todos los programas de formación disponibles para vista previa y descarga.",
            "catalog_empty": "No hay programas disponibles por el momento.",
            "filter_label": "Filtrar por temática",
            "filter_all": "Todos los programas",
            "filter_empty": "Ningún programa coincide con este filtro.",
            "filters": {
                "gestion": "Gestión y Revenue",
                "pms": "PMS y Herramientas",
                "hygiene": "Higiene y Calidad",
                "relation": "Relación cliente",
                "marketing": "Marketing visual",
            },
        },
        "replacements": {
            "intro": "<strong>S.V.H Management</strong> ofrece servicios de sustitución:",
            "offers": [
                "<strong>Dirección temporal</strong> para responder a ausencias de dirección.",
                "<strong>Adjunto de dirección, recepcionista y recepcionista nocturno</strong> para cubrir ausencias en recepción. Todos nuestros perfiles están formados en PMS y operativos de inmediato.",
            ],
            "paragraphs": [
                "Ya sea por salida o imprevisto, la falta de un Director, General Manager o miembro clave afecta seriamente a un establecimiento hotelero.",
                "Este impacto puede reducirse con un profesional que te permita ganar tiempo y estabilizar la operación.",
                "La desestabilización de equipos, la pérdida de motivación y de facturación son riesgos reales cuando faltan puestos clave.",
                "Imagina una orquesta sin director o un equipo deportivo sin capitán.",
                "El sentido de responsabilidad, la integridad y las competencias son los activos que aporta <strong>S.V.H Management</strong>.",
                "<strong>S.V.H Management</strong> ha diseñado sus ofertas para reducir la ansiedad vinculada a la falta de personal clave.",
                "Nuestro consultor llega con experiencia y es operativo desde su llegada.",
            ],
            "form_title": "Tengo una necesidad de sustitución.",
            "freelance_title": "¿Usted también quiere unirse a la aventura S.V.H Management?",
            "freelance_button": "Soy Freelance",
            "freelance_intro": "Suba su CV para presentarnos su candidatura y unirse a futuras misiones en S.V.H Management.",
            "freelance_success": "Su candidatura freelance se ha enviado correctamente. ¡Gracias!",
            "freelance_errors": {
                "required": "Todos los campos y el CV son obligatorios.",
                "invalid_email": "Correo electrónico no válido.",
                "invalid_phone": "Número de teléfono no válido.",
                "cv_required": "Por favor, adjunte su CV.",
                "cv_extension": "Formato de CV no admitido. Use PDF, DOC o DOCX.",
                "upload_failed": "La subida del CV ha fallado. Inténtelo de nuevo.",
            },
            "script": {
                "subject_prefix": "Solicitud de sustitución - ",
                "subject_fallback": "No especificado",
                "line_last_name": "Apellido: ",
                "line_first_name": "Nombre: ",
                "line_email": "Correo: ",
                "line_phone": "Teléfono: ",
                "line_position": "Puesto buscado: ",
                "line_message_title": "Mensaje:",
            },
        },
        "consulting": {
            "paragraphs": [
                "<strong>S.V.H Management</strong> también ofrece servicios de consultoría para reestructuración, organización y evolución de procesos.",
                "Una auditoría 360 de tu establecimiento permite identificar lo que funciona y lo que no para definir acciones concretas.",
                "También ofrecemos apoyo específico para <strong>tareas administrativas</strong> con discreción, rigor y confidencialidad.",
                "<strong>La asistencia a hoteleros</strong> cubre tareas comerciales y administrativas de forma puntual o regular.",
            ]
        },
        "resources": {
            "intro_title": "El Pequeño Hotelero",
            "intro_text": "El Pequeño Hotelero es una guía práctica creada por un hotelero para hoteleros y sus equipos. Comprende KPI, calcula indicadores y descubre nuestro lenguaje profesional. Regístrate y descárgalo gratis ☺️.",
            "ticker_aria": "Banda deslizante de recursos desbloqueados",
            "cover_alt": "Vista de portada: {title}",
            "scroll_prev": "Desplazar a la izquierda",
            "scroll_next": "Desplazar a la derecha",
            "select_label": "Seleccionar un título",
            "select_button": "Ir",
            "premium_title": "Acceso Premium a Recursos",
            "premium_desc": "Rellena tus datos para desbloquear los archivos descargables.",
            "unlock_button": "Desbloquear acceso",
            "access_validated": "Acceso premium validado",
            "for": "para",
            "files_available": "{count} archivo(s) disponible(s) para descarga.",
            "download": "Descargar",
            "preview": "Vista previa",
            "no_files": "No se pudo cargar ningún archivo desde la carpeta Drive.",
            "errors": {
                "required": "Todos los campos son obligatorios para obtener acceso premium.",
                "invalid_email": "Correo electrónico no válido.",
                "invalid_phone": "Número de teléfono no válido.",
            },
        },
        "contact": {
            "intro_title": "¿Un proyecto? ¿Una necesidad? ¡Hablemos!",
            "map_aria": "Intervenciones en toda Francia",
            "map_alt": "Mapa de Francia",
            "map_note": "Intervenciones en toda Francia",
            "coords_aria": "Datos de contacto",
            "linkedin_label": "¡Sigamos en contacto en redes!",
            "linkedin_aria": "Perfil de LinkedIn de Franck Bougaut",
            "google_reviews_label": "Tu opinión es importante",
            "google_reviews_cta": "Dejar una reseña en Google",
            "google_reviews_aria": "Dejar una reseña de Google para S.V.H Management",
            "form_title": "Formulario de contacto",
            "script": {
                "default_subject": "Solicitud de contacto - SVH Management",
                "line_name": "Nombre: ",
                "line_email": "Correo: ",
                "line_message_title": "Mensaje:",
            },
        },
        "forms": {
            "name": "Nombre",
            "first_name": "Nombre",
            "last_name": "Apellido",
            "email": "Correo",
            "mail": "Correo",
            "phone": "Teléfono",
            "geo_area": "Zona geográfica de intervención",
            "available_job": "Puesto disponible",
            "cv_file": "Subir su CV",
            "cv_help": "Formatos aceptados: PDF, DOC, DOCX.",
            "subject": "Asunto",
            "message": "Mensaje",
            "text_area": "Espacio de texto",
            "send": "Enviar",
            "send_request": "Enviar solicitud",
            "send_application": "Enviar mi candidatura",
            "help_mailto": "Al hacer clic en Enviar, tu aplicación de correo se abre con un mensaje pre-rellenado a contact@svhmanagement.fr.",
            "position_label": "Puesto buscado",
            "position_placeholder": "Selecciona un puesto",
            "position_direction": "Dirección",
            "position_receptionist": "Recepcionista",
            "position_reception": "Recepción",
            "position_kitchen": "Cocina",
            "position_service": "Sala",
            "position_housekeeping": "Pisos",
            "position_training": "Formaciones",
            "position_other": "Otro",
        },
    },
    "de": {
        "brand": {"alt": "S.V.H Management"},
        "menu": {"main_aria": "Hauptmenü"},
        "language": {
            "title": "Sprache wählen",
            "chooser_aria": "Sprachauswahl der Website",
        },
        "nav": {
            "home": "Start",
            "service": "Zu Ihren Diensten",
            "about": "Wer sind wir?",
            "formations": "Schulungen",
            "programs": "Unser Schulungskatalog",
            "replacements": "Vertretungen",
            "consulting": "Beratung & Unterstützung",
            "resources": "Ressourcen",
            "contact": "Kontakt & Infos",
        },
        "pages": {
            "service": "Zu Ihren Diensten",
            "about": "Wer sind wir?",
            "formations": "Schulungen",
            "programs": "Unser Schulungskatalog",
            "replacements": "Vertretungen",
            "consulting": "Beratung & Unterstützung",
            "resources": "Ressourcen",
            "contact": "Kontakt & Infos",
        },
        "home": {
            "title": "Im Dienst der Hotellerie!",
            "lines": [
                "S.V.H Management stellt seine Praxiserfahrung in den Dienst von Hoteliers und Fachkräften der Hotellerie.",
                "Mit mehr als 30 Jahren operativer Erfahrung in Hotellerie und Gastronomie begleitet das Unternehmen Betreiber und Investoren bei den aktuellen Branchenherausforderungen: Teamführung, wirtschaftliche Performance, operative Kontinuität und Kompetenzaufbau.",
                "S.V.H Management ist heute in ganz Frankreich im Einsatz und bietet:",
                "::bell::Direktionsvertretung",
                "::bell::Verstärkung an der Rezeption",
                "::bell::Schulungen für Hotelberufe",
                "::bell::Beratung und Begleitung von Betreibern",
                "Unser Ansatz basiert auf drei zentralen Säulen: Service, Werte und Mensch.",
            ],
            "vision_title": "S.V.H — Eine engagierte Vision des Hotelmanagements",
            "vision_intro": [
                "Bei SVH Management glauben wir, dass ein leistungsstarkes Hotel weder vom Zufall noch nur von Kennzahlen lebt.",
                "Es basiert auf klarer Struktur, klaren Prinzipien und engagierten Menschen.",
            ],
            "triptych": [
                {
                    "before": "",
                    "letter": "S",
                    "after": "für Service",
                    "body": "Wir arbeiten mit Methode, Präzision und Anspruch. Strukturieren, steuern, optimieren: Jede Mission vereint strategischen und operativen Ansatz mit Fokus auf messbare Ergebnisse.",
                },
                {
                    "before": "",
                    "letter": "V",
                    "after": "für Werte",
                    "body": "Unser Engagement beruht auf Integrität, Transparenz und Ergebnisorientierung. Wir vertreten verantwortungsvolles, konsequentes und loyales Management auf Basis von Vertrauen.",
                },
                {
                    "before": "",
                    "letter": "H",
                    "after": "für Human",
                    "body": "Keine Strategie gelingt ohne Teams. Deshalb steht der Mensch im Zentrum: Wir fördern, begleiten und vermitteln, um Kompetenzen und Talente nachhaltig zu entwickeln.",
                },
            ],
            "vision_closing": [
                "S.V.H ist die Verbindung von Rigorosität, Sinn und Engagement.",
                "Eine Signatur. Eine Methode. Ein Anspruch.",
            ],
        },
        "service": {
            "title": "S.V.H Management",
            "subtitle": "Operative Expertise im Dienst von Fachkräften der Hotellerie.",
            "highlights": "Hoteldirektion • Operative Verstärkung • Schulung • Hotelberatung",
            "mission_title": "Unsere Mission",
            "mission": [
                "Wir begleiten Hoteliers, Betreiber und Investoren bei der Leistungssteigerung ihrer Häuser durch Praxiserfahrung aus dem operativen Hotelbetrieb.",
            ],
            "experience_title": "Unsere Erfahrung",
            "experience_intro": [
                "S.V.H Management wurde von Franck Bougaut gegründet, einem Profi mit mehr als 30 Jahren Erfahrung in Hotellerie und Gastronomie, und basiert auf einem vollständigen Werdegang:",
            ],
            "experience_steps": [
                "von der Küche",
                "über die Hoteldirektion",
                "bis zur Multi-Site-Direktion innerhalb einer internationalen Hotelgruppe.",
            ],
            "experience_outro": "Diese Erfahrung ermöglicht heute eine globale und pragmatische Sicht auf den Hotelbetrieb.",
            "domains_title": "Unsere Einsatzbereiche",
            "domains_intro": "S.V.H Management ist in mehreren Schlüsselfeldern tätig:",
            "domains": [
                {
                    "title": "Direktionsvertretung",
                    "body": "Sicherung von Kontinuität und Performance Ihres Betriebs.",
                },
                {
                    "title": "Verstärkung an der Rezeption",
                    "body": "Stärkung Ihrer Rezeptions-Teams am Tag und in der Nacht.",
                },
                {
                    "title": "Hotel-Schulungen",
                    "body": "Schulung von Teams nach Branchenstandards und Anforderungen.",
                },
                {
                    "title": "Beratung für Betreiber",
                    "body": "Begleitung von Hoteliers bei Steuerung und Optimierung ihres Betriebs.",
                },
            ],
            "values_title": "Unsere Werte",
            "values_intro": "S.V.H Management basiert auf drei grundlegenden Säulen:",
            "values": [
                {
                    "title": "Service",
                    "body": "Servicequalität als Motor der Kundenzufriedenheit.",
                },
                {
                    "title": "Werte",
                    "body": "Engagement, Ethik und Professionalität.",
                },
                {
                    "title": "Mensch",
                    "body": "Denn der Erfolg eines Hotels beruht vor allem auf den Frauen und Männern, die es tragen.",
                },
            ],
        },
        "about": {
            "heading": "Expertise im Hotelmanagement",
            "intro": [
                "S.V.H Management ist ein Unternehmen, das auf Hotelmanagement, Beratung von Betreibern sowie Schulungen für Berufe in Hotellerie und Gastronomie spezialisiert ist.",
                "Das Unternehmen wurde von Franck Bougaut gegründet, einem Branchenprofi mit mehr als 30 Jahren Erfahrung im Hotelbetrieb. Es unterstützt Hotels bei der Optimierung von Organisation, Leistung und Teams.",
                "Nach einem beruflichen Einstieg in der Küche entwickelte sich Franck Bougaut schnell in leitende Hotelpositionen und anschließend in die Multi-Site-Direktion innerhalb einer internationalen Hotelgruppe.",
            ],
            "quote": [],
            "list_intro": "Dank dieser Erfahrung begleitet er heute Hotellerie-Profis in mehreren Bereichen:",
            "bullets": [
                "Vertretung der Hoteldirektion",
                "operative Verstärkung an der Rezeption",
                "Schulungen für Hotelberufe",
                "Beratung im Hotelbetrieb",
                "Begleitung von Investoren und Betreibern",
            ],
            "values": "S.V.H Management arbeitet mit einem Ansatz, der auf Praxiserfahrung, wirtschaftlicher Leistung und der Aufwertung von Teams basiert.<br><br>In einem Umfeld, in dem Recruiting und Weiterbildung zu zentralen Herausforderungen für Hotels werden, bietet S.V.H Management konkrete Lösungen, um den Betrieb zu sichern und das Wachstum der Häuser zu begleiten.",
            "signature_quote": "",
            "signature_role": "Geschäftsführer S.V.H Management",
        },
        "formations": {
            "heading": "Schulungsorganisation S.V.H Management",
            "catalog_button": "Unser Schulungskatalog",
            "paragraphs": [
                "Die Expertise des Gründers umfasst mehr als 30 Jahre in Küche, Service, Rezeption, Hotel- und Restaurantleitung, Vertrieb und Teammanagement.",
                "Um Teams zu stärken und Kompetenzen auszubauen, entwickelt <strong>S.V.H Management</strong> Schulungen mit Fokus auf <strong>Service</strong>, <strong>Werte</strong> und <strong>Mensch</strong>.",
                "Unsere Schulungen werden auf Ihr Anspruchsniveau abgestimmt, damit Verhalten und Kommunikation Ihres Personals Ihre Identität widerspiegeln.",
                "Als Branchenexperten setzen wir Programme mit Qualitätscharta und optimalen Rahmenbedingungen um.",
                "<strong>S.V.H Management</strong> integriert Unternehmens- und Humanwerte in jedes Programm.",
                "Die Qualität unserer Schulungen erfüllt die qualitativen Anforderungen Ihres Betriebs.",
                "Wir können Finanzierungsanträge bei Ihrem OPCO AKTO begleiten, um gesetzliche Schulungspflichten zu erfüllen.",
                "Alle Programme sind maßgeschneidert und als Präsenz- oder digitale Formate verfügbar.",
            ],
        },
        "programs": {
            "image_alt": "Schulungsprogramm",
            "catalog_image_alt": "Schulungskatalog",
            "catalog_intro": "Hier finden Sie alle Schulungsprogramme mit Vorschau und Download.",
            "catalog_empty": "Derzeit sind keine Programme verfügbar.",
            "filter_label": "Nach Thema filtern",
            "filter_all": "Alle Programme",
            "filter_empty": "Kein Programm passt zu diesem Filter.",
            "filters": {
                "gestion": "Management & Revenue",
                "pms": "PMS & Tools",
                "hygiene": "Hygiene & Qualität",
                "relation": "Kundenbeziehung",
                "marketing": "Visuelles Marketing",
            },
        },
        "replacements": {
            "intro": "<strong>S.V.H Management</strong> bietet Vertretungsleistungen:",
            "offers": [
                "<strong>Temporäre Direktion</strong> für Management-Abwesenheiten.",
                "<strong>Stellvertretende Leitung, Rezeptionist und Nachtrezeptionist</strong> zur Abdeckung von Rezeption-Abwesenheiten. Alle Profile sind PMS-erfahren und sofort einsetzbar.",
            ],
            "paragraphs": [
                "Egal ob Abgang oder Notfall: Der Ausfall von Direktion oder Schlüsselpositionen ist ein großes Handicap für Hotels.",
                "Dieser Impact kann durch erfahrene Unterstützung abgefedert werden, um Zeit für eine nachhaltige Lösung zu gewinnen.",
                "Teaminstabilität, Motivationsverlust und Umsatzeinbußen sind reale Risiken bei vakanten Schlüsselrollen.",
                "Stellen Sie sich ein Orchester ohne Dirigent oder ein Team ohne Kapitän vor.",
                "Verantwortung, Integrität und Kompetenz sind die Stärken, die <strong>S.V.H Management</strong> in Vertretungsmissionen einbringt.",
                "<strong>S.V.H Management</strong> hat Angebote entwickelt, um Stress bei Personalausfällen zu reduzieren.",
                "Unsere Berater sind vom ersten Tag an einsatzfähig.",
            ],
            "form_title": "Ich habe einen Vertretungsbedarf.",
            "freelance_title": "Möchten auch Sie Teil des Abenteuers S.V.H Management werden?",
            "freelance_button": "Ich bin Freelancer",
            "freelance_intro": "Laden Sie Ihren Lebenslauf hoch, um sich zu bewerben und zukünftige Einsätze bei S.V.H Management zu übernehmen.",
            "freelance_success": "Ihre Freelancer-Bewerbung wurde erfolgreich gesendet. Vielen Dank!",
            "freelance_errors": {
                "required": "Alle Felder und der Lebenslauf sind erforderlich.",
                "invalid_email": "Ungültige E-Mail-Adresse.",
                "invalid_phone": "Ungültige Telefonnummer.",
                "cv_required": "Bitte laden Sie Ihren Lebenslauf hoch.",
                "cv_extension": "Nicht unterstütztes CV-Format. Nutzen Sie PDF, DOC oder DOCX.",
                "upload_failed": "Der Upload des Lebenslaufs ist fehlgeschlagen. Bitte versuchen Sie es erneut.",
            },
            "script": {
                "subject_prefix": "Vertretungsanfrage - ",
                "subject_fallback": "Nicht angegeben",
                "line_last_name": "Nachname: ",
                "line_first_name": "Vorname: ",
                "line_email": "E-Mail: ",
                "line_phone": "Telefon: ",
                "line_position": "Gesuchte Position: ",
                "line_message_title": "Nachricht:",
            },
        },
        "consulting": {
            "paragraphs": [
                "<strong>S.V.H Management</strong> bietet außerdem Beratungsleistungen für Restrukturierung, Organisation und operative Weiterentwicklung.",
                "Ein 360°-Audit Ihres Betriebs zeigt, was funktioniert und wo Verbesserungen nötig sind.",
                "Zusätzlich bieten wir gezielte Unterstützung bei <strong>administrativen Aufgaben</strong> mit Diskretion und Verlässlichkeit.",
                "<strong>Unterstützung für Hoteliers</strong> umfasst punktuelle oder regelmäßige kaufmännische und administrative Aufgaben.",
            ]
        },
        "resources": {
            "intro_title": "Der kleine Hotelier",
            "intro_text": "Der kleine Hotelier ist ein praxisnaher Leitfaden von einem Hotelier für Hoteliers und Teams. KPI verstehen, Kennzahlen berechnen und Fachsprache kennenlernen. Registrieren und kostenlos herunterladen ☺️.",
            "ticker_aria": "Laufband mit freigeschalteten Ressourcen",
            "cover_alt": "Titelvorschau: {title}",
            "scroll_prev": "Nach links scrollen",
            "scroll_next": "Nach rechts scrollen",
            "select_label": "Titel auswählen",
            "select_button": "Los",
            "premium_title": "Premium-Zugang zu Ressourcen",
            "premium_desc": "Bitte füllen Sie Ihre Daten aus, um die Download-Dateien freizuschalten.",
            "unlock_button": "Zugang freischalten",
            "access_validated": "Premium-Zugang bestätigt",
            "for": "für",
            "files_available": "{count} Datei(en) zum Download verfügbar.",
            "download": "Herunterladen",
            "preview": "Vorschau",
            "no_files": "Es konnte keine Datei aus dem Drive-Ordner geladen werden.",
            "errors": {
                "required": "Alle Felder sind erforderlich, um Premium-Zugang zu erhalten.",
                "invalid_email": "Ungültige E-Mail-Adresse.",
                "invalid_phone": "Ungültige Telefonnummer.",
            },
        },
        "contact": {
            "intro_title": "Ein Projekt? Ein Bedarf? Sprechen wir darüber!",
            "map_aria": "Einsätze in ganz Frankreich",
            "map_alt": "Frankreichkarte",
            "map_note": "Einsätze in ganz Frankreich",
            "coords_aria": "Kontaktdaten",
            "linkedin_label": "Bleiben wir in den Netzwerken in Kontakt!",
            "linkedin_aria": "LinkedIn-Profil von Franck Bougaut",
            "google_reviews_label": "Ihr Feedback zählt",
            "google_reviews_cta": "Google-Bewertung abgeben",
            "google_reviews_aria": "Google-Bewertung für S.V.H Management abgeben",
            "form_title": "Kontaktformular",
            "script": {
                "default_subject": "Kontaktanfrage - SVH Management",
                "line_name": "Name: ",
                "line_email": "E-Mail: ",
                "line_message_title": "Nachricht:",
            },
        },
        "forms": {
            "name": "Name",
            "first_name": "Vorname",
            "last_name": "Nachname",
            "email": "E-Mail",
            "mail": "E-Mail",
            "phone": "Telefon",
            "geo_area": "Einsatzgebiet",
            "available_job": "Verfügbarer Beruf",
            "cv_file": "Lebenslauf hochladen",
            "cv_help": "Akzeptierte Formate: PDF, DOC, DOCX.",
            "subject": "Betreff",
            "message": "Nachricht",
            "text_area": "Textfeld",
            "send": "Senden",
            "send_request": "Anfrage senden",
            "send_application": "Meine Bewerbung senden",
            "help_mailto": "Beim Klick auf Senden öffnet sich Ihre E-Mail-App mit einer vorausgefüllten Nachricht an contact@svhmanagement.fr.",
            "position_label": "Gesuchte Position",
            "position_placeholder": "Position auswählen",
            "position_direction": "Direktion",
            "position_receptionist": "Rezeptionist",
            "position_reception": "Rezeption",
            "position_kitchen": "Küche",
            "position_service": "Service",
            "position_housekeeping": "Etage",
            "position_training": "Schulungen",
            "position_other": "Andere",
        },
    },
}

HERO_IMAGES = {
    "accueil": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SQrs52qMY-fZRY9yXwVL_YsciaTyWVhocjYr1TkKunUSKNnNytkb_dT9EPfiOXt9lBRwR4w"
        "CRFFwg34gJuQL-g8V58JkmIUngCT9j2-cA_mI0xf8VlltQByknoeoKoOHyrZ2FgIt3e5bWStL58Zy"
        "L34a0bM_3QCoacbth6DZuYFQZVJ9uwUGScCW64=w16383"
    ),
    "qui": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SRF3ay-Nz6DnXdb3aA0hHeJ5V-14yavdo45QdGnaK2mmE9witKgOVBhfln2J9MV67UQUxby"
        "VTGoASWmeBIh6Oz7b_x8V-Mxauv8FnyVFAE0fZQ0MsSZGgtT8X2erwuxzXZLc9R4SZbXTVJcxdH-"
        "1lb0HLlmDJ5tYjrpUngjhaUYV1_aLi7DAG42PKo=w16383"
    ),
    "formations": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0STidqon1-jUOQYl9ikgFqdq0cFGnI50sSHc7uB0NqlTsksk4oLUHkeUBkJnTE2fty7m0KuT"
        "q_H3bA33Bp8iEJ_KW5ulRvnnd7aNV9p2iRSCLqM9y1bMyfetM3uGcfYgOLLUjlivLT1iHkurUGn8"
        "YYNdw9BCCMqV-sHjlcC2a7MAsOrxjj1WaLQR=w16383"
    ),
    "programmes": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SQ8Aqpf2ub5OjEffrxpZCuvqyQydQ097c3NHVz6W4BA0xOJ_Cbyo4lp4g3wWS65ibMqMIcY"
        "tCCDAbL48JWL5I6pK1aH9qyloIVWKQdSifdcCdUCAh_fS5U3jb6JrLTlxkHZtC0fH4QlaVd-eD80"
        "cf22OZve6w34cG4Tf13NKlTkWmM3Bst9-9Jo9WE=w16383"
    ),
    "remplacements": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SQSxY0QUJGo8apJuz-Had13fPmn1Y_Pm-15vmWJG-Oo8GslUxPdIXddLUJmmMRvXjQXFpB_"
        "iax3YDns36af7_3D_p0VE37nHISNVRyKhYULt1vrPJq3e6VkIM9BS2BALnUzQYgYXTstlFn1Mhlz"
        "TyQvx_eh_ySzT4o4J-xDHQIZcXB1BQlaM30j4g4=w16383"
    ),
    "conseil": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0STCGE_m1DK0WtvUAXf8eIaDPiVK9Yy-xhWHJYv0amNXpiL2hDhYEAtXlBwhK708QBPWpTMe"
        "tNnmlQV3A2DYBcLpcYa6R_h5Q7bYGi_MXwv1IwFUXGFUd98uKxrnn1DhNlwBJEzB5JFcKFLp29Fy"
        "4-hxtUSDL5Gau15T1Ds3trhrIjbcQQTJifBM-Eo=w16383"
    ),
    "ressources": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SSuMjUFlwTj3wz51gkgP3S4v6cMGln58xEyyoqws_viMy_-1Ddm09YCeq-Y9ArmHlalh86B"
        "awoTKKyZfJ8fX3lgmOo2MR0XGaXBLRi9IQyAGRLjKZcMtEKgFDpY2ssG3YXCaa58E58YL2w6Sj9K"
        "MrRkUeW1K6P95hwiCh2hTPPdoMneQHh_ZmALWIc=w16383"
    ),
    "contact": (
        "https://lh3.googleusercontent.com/sitesv/"
        "APaQ0SS9M77ZM5oRqOpJq0_zoTSi1BgLwaNctI0Byw2DQaifYFvA3p34QGNZWnPoNBbUYtQJ67Wr"
        "0riYC9ItgDJ75gtaMWiF-C9i6Wtj5bbO-VySLgz3kfpNumBPEqBaZm1XJPavK7Q6WKfvxBC_XfDC"
        "qCofvXliBQ3TcVl4hdSXutzs3820W_vT02SpJQ8=w16383"
    ),
}

NAV_ITEMS = [
    {"endpoint": "home", "label_key": "nav.home", "key": "accueil", "children": []},
    {
        "endpoint": "a_votre_service",
        "label_key": "nav.service",
        "key": "service",
        "children": [],
    },
    {
        "endpoint": "qui_sommes_nous",
        "label_key": "nav.about",
        "key": "qui",
        "children": [],
    },
    {
        "endpoint": "formations",
        "label_key": "nav.formations",
        "key": "formations",
        "children": [
            {
                "endpoint": "formations_programmes",
                "label_key": "nav.programs",
            }
        ],
    },
    {
        "endpoint": "remplacements",
        "label_key": "nav.replacements",
        "key": "remplacements",
        "children": [],
    },
    {
        "endpoint": "conseil_assistance",
        "label_key": "nav.consulting",
        "key": "conseil",
        "children": [],
    },
    {
        "endpoint": "ressources",
        "label_key": "nav.resources",
        "key": "ressources",
        "children": [],
    },
    {
        "endpoint": "contact_infos",
        "label_key": "nav.contact",
        "key": "contact",
        "children": [],
    },
]

TRAINING_CATALOG = [
    {
        "title": "Analyser un compte de résultat",
        "filename": "programme-analyser-un-compte-de-resultat.pdf",
        "category": "gestion",
    },
    {
        "title": "Revenue management",
        "filename": "programme-revenue-management-3.pdf",
        "category": "gestion",
    },
    {
        "title": "Référent hygiène",
        "filename": "programme-referent-hygiene-2.pdf",
        "category": "hygiene",
    },
    {
        "title": "Hygiène alimentaire adaptée à l'activité des établissements de restauration",
        "filename": "programme-hygiene-alimentaire-adaptee-a-l-activite-des-etablissements-de-restauration.pdf",
        "category": "hygiene",
    },
    {
        "title": "Développement durable, RSE",
        "filename": "programme-developpement-durable-rse.pdf",
        "category": "hygiene",
    },
    {
        "title": "Accueillir sa clientèle en situation de Handicap",
        "filename": "programme-accueillir-sa-clientele-en-situation-de-handicap.pdf",
        "category": "relation",
    },
    {
        "title": "Le Yield management 1 - Débutant",
        "filename": "programme-le-yield-management-1-debutant.pdf",
        "category": "gestion",
    },
    {
        "title": "LEAN PMS",
        "filename": "programme-lean-pms.pdf",
        "category": "pms",
    },
    {
        "title": "OPÉRA PMS (1 jour)",
        "filename": "programme-opera-pms-1-jour.pdf",
        "category": "pms",
    },
    {
        "title": "Maîtriser l'Art de la Photographie Culinaire",
        "filename": "programme-maitriser-l-art-de-la-photographie-culinaire.pdf",
        "category": "marketing",
    },
    {
        "title": "OPÉRA PMS",
        "filename": "programme-opera-pms.pdf",
        "category": "pms",
    },
]

TRAINING_FILTER_ORDER = ("gestion", "pms", "hygiene", "relation", "marketing")


def _find_local_image(relative_folder: str, stem: str) -> Optional[str]:
    for extension in SUPPORTED_IMAGE_EXTENSIONS:
        relative_path = Path(relative_folder) / f"{stem}{extension}"
        absolute_path = STATIC_DIR / relative_path
        if absolute_path.exists():
            return f"/static/{relative_path.as_posix()}"
    return None


def _hero_image(key: str) -> str:
    return _find_local_image("img/heroes", key) or HERO_IMAGES[key]


def _load_training_catalog() -> List[Dict[str, str]]:
    catalog_items: List[Dict[str, str]] = []
    for item in TRAINING_CATALOG:
        title = str(item.get("title", "")).strip()
        filename = str(item.get("filename", "")).strip()
        category = str(item.get("category", "")).strip()
        if not title or not filename or not category:
            continue
        relative_static_path = f"docs/formations/{filename}"
        absolute_path = STATIC_DIR / relative_static_path
        if not absolute_path.exists():
            continue
        file_url = url_for("static", filename=relative_static_path)
        catalog_items.append(
            {
                "title": title,
                "file_url": file_url,
                "category": category,
            }
        )
    return catalog_items


def _extract_drive_file_id(raw_id: str, view_url: str, download_url: str) -> str:
    if raw_id:
        return raw_id

    for raw_url in (view_url, download_url):
        if not raw_url:
            continue
        path_match = DRIVE_FILE_ID_PATH_PATTERN.search(raw_url)
        if path_match:
            return path_match.group(1)
        query_match = DRIVE_FILE_ID_QUERY_PATTERN.search(raw_url)
        if query_match:
            return query_match.group(1)

    return ""


def _load_drive_resources() -> List[Dict[str, str]]:
    resources_file = DRIVE_RESOURCES_FILE
    if (
        not resources_file.exists()
        and resources_file != DEFAULT_DRIVE_RESOURCES_FILE
        and DEFAULT_DRIVE_RESOURCES_FILE.exists()
    ):
        resources_file = DEFAULT_DRIVE_RESOURCES_FILE

    if not resources_file.exists():
        return []

    try:
        data = json.loads(resources_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    resources: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        view_url = str(item.get("view_url", "")).strip()
        download_url = str(item.get("download_url", "")).strip()
        modified = str(item.get("modified", "")).strip()
        if not title or not view_url or not download_url:
            continue
        file_id = _extract_drive_file_id(raw_id, view_url, download_url)
        thumbnail_url = (
            f"https://drive.google.com/thumbnail?id={file_id}&sz=w420-h560"
            if file_id
            else ""
        )
        resources.append(
            {
                "id": file_id,
                "title": title,
                "view_url": view_url,
                "download_url": download_url,
                "modified": modified,
                "thumbnail_url": thumbnail_url,
            }
        )

    def sort_key(item: Dict[str, str]) -> tuple:
        title = item.get("title", "")
        match = ISSUE_NUMBER_PATTERN.search(title)
        if match:
            return (0, int(match.group(1)), title.casefold())
        return (1, 9999, title.casefold())

    resources.sort(key=sort_key)
    return resources


def _email_notifications_enabled() -> bool:
    if RESEND_API_KEY and RESEND_EMAIL_FROM and RESEND_EMAIL_TO:
        return True
    return bool(SMTP_HOST and CONTACT_EMAIL_TO and CONTACT_EMAIL_FROM)


def _email_transport_mode() -> str:
    if RESEND_API_KEY and RESEND_EMAIL_FROM and RESEND_EMAIL_TO:
        return "resend"
    if SMTP_HOST and CONTACT_EMAIL_TO and CONTACT_EMAIL_FROM:
        return "smtp"
    return "none"


def _send_email_notification(
    subject: str,
    body: str,
    *,
    reply_to: str = "",
    attachment_path: Optional[Path] = None,
    attachment_name: str = "",
    to_email: str = "",
    from_email: str = "",
) -> bool:
    normalized_subject = subject.strip() or "Nouveau message site SVH Management"
    resend_to = to_email.strip() or RESEND_EMAIL_TO
    smtp_to = to_email.strip() or CONTACT_EMAIL_TO
    effective_from = from_email.strip() or CONTACT_EMAIL_FROM

    if RESEND_API_KEY and RESEND_EMAIL_FROM and resend_to:
        payload: Dict[str, Any] = {
            "from": from_email.strip() or RESEND_EMAIL_FROM,
            "to": [resend_to],
            "subject": normalized_subject,
            "text": body,
        }
        if reply_to and EMAIL_PATTERN.match(reply_to):
            payload["reply_to"] = reply_to
        if attachment_path:
            try:
                encoded_attachment = b64encode(attachment_path.read_bytes()).decode("ascii")
                payload["attachments"] = [
                    {
                        "filename": attachment_name.strip() or attachment_path.name,
                        "content": encoded_attachment,
                    }
                ]
            except OSError:
                app.logger.exception(
                    "Echec lecture pièce jointe pour email Resend. fichier=%s",
                    attachment_path,
                )
                return False

        request_data = json.dumps(payload).encode("utf-8")
        request_headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        http_request = urllib_request.Request(
            RESEND_API_URL,
            data=request_data,
            headers=request_headers,
            method="POST",
        )

        try:
            with urllib_request.urlopen(http_request, timeout=SMTP_TIMEOUT_SEC) as response:
                return 200 <= int(getattr(response, "status", 0)) < 300
        except urllib_error.HTTPError as exc:
            try:
                response_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                response_body = ""
            app.logger.error(
                "Echec envoi email Resend formulaire. to=%s from=%s subject=%s status=%s body=%s",
                resend_to,
                payload.get("from", ""),
                normalized_subject,
                exc.code,
                response_body[:1200],
            )
            return False
        except Exception:
            app.logger.exception(
                "Echec envoi email Resend formulaire. to=%s from=%s subject=%s",
                resend_to,
                payload.get("from", ""),
                normalized_subject,
            )
            return False

    if not (SMTP_HOST and smtp_to and CONTACT_EMAIL_FROM):
        return False

    message = EmailMessage()
    message["Subject"] = normalized_subject
    message["From"] = effective_from
    message["To"] = smtp_to
    if reply_to and EMAIL_PATTERN.match(reply_to):
        message["Reply-To"] = reply_to
    message.set_content(body)
    if attachment_path:
        try:
            attachment_data = attachment_path.read_bytes()
            attachment_mime, _ = mimetypes.guess_type(attachment_name or attachment_path.name)
            if attachment_mime and "/" in attachment_mime:
                main_type, sub_type = attachment_mime.split("/", 1)
            else:
                main_type, sub_type = "application", "octet-stream"
            message.add_attachment(
                attachment_data,
                maintype=main_type,
                subtype=sub_type,
                filename=attachment_name.strip() or attachment_path.name,
            )
        except OSError:
            app.logger.exception(
                "Echec lecture pièce jointe pour email SMTP. fichier=%s",
                attachment_path,
            )
            return False

    try:
        if SMTP_USE_SSL:
            smtp_client = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SEC)
        else:
            smtp_client = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT_SEC)

        with smtp_client as server:
            if not SMTP_USE_SSL and SMTP_USE_TLS:
                server.ehlo()
                server.starttls()
                server.ehlo()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
        return True
    except Exception:
        app.logger.exception(
            "Echec envoi email SMTP formulaire. to=%s from=%s subject=%s",
            smtp_to,
            effective_from,
            normalized_subject,
        )
        return False


def _save_premium_lead(first_name: str, last_name: str, email: str, phone: str) -> None:
    is_new_file = not PREMIUM_LEADS_FILE.exists()
    with PREMIUM_LEADS_FILE.open("a", encoding="utf-8", newline="") as csv_file:
        writer = csv.writer(csv_file)
        if is_new_file:
            writer.writerow(["timestamp", "first_name", "last_name", "email", "phone"])
        writer.writerow(
            [
                datetime.utcnow().isoformat(timespec="seconds") + "Z",
                first_name,
                last_name,
                email,
                phone,
            ]
        )


def _save_contact_request(name: str, email: str, subject: str, message: str) -> bool:
    is_new_file = not CONTACT_REQUESTS_FILE.exists()
    try:
        with CONTACT_REQUESTS_FILE.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            if is_new_file:
                writer.writerow(["timestamp", "name", "email", "subject", "message"])
            writer.writerow(
                [
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    name,
                    email,
                    subject,
                    message,
                ]
            )
    except OSError:
        return False
    return True


def _save_replacement_request(
    first_name: str,
    last_name: str,
    email: str,
    phone: str,
    position: str,
    message: str,
) -> bool:
    is_new_file = not REPLACEMENT_REQUESTS_FILE.exists()
    try:
        with REPLACEMENT_REQUESTS_FILE.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            if is_new_file:
                writer.writerow(
                    [
                        "timestamp",
                        "first_name",
                        "last_name",
                        "email",
                        "phone",
                        "position",
                        "message",
                    ]
                )
            writer.writerow(
                [
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    first_name,
                    last_name,
                    email,
                    phone,
                    position,
                    message,
                ]
            )
    except OSError:
        return False
    return True


def _is_allowed_cv_filename(filename: str) -> bool:
    extension = Path(filename).suffix.lower()
    return extension in ALLOWED_CV_EXTENSIONS


def _save_freelance_application(
    first_name: str,
    last_name: str,
    email: str,
    phone: str,
    geo_area: str,
    available_job: str,
    cv_file: Any,
) -> Optional[Dict[str, str]]:
    original_filename = secure_filename(cv_file.filename or "").strip()
    if not original_filename or not _is_allowed_cv_filename(original_filename):
        return None

    extension = Path(original_filename).suffix.lower()
    candidate_slug = secure_filename(f"{last_name}-{first_name}") or "candidat"
    unique_token = uuid4().hex[:10]
    stored_filename = (
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{candidate_slug}_{unique_token}{extension}"
    )
    target_path = FREELANCE_CV_DIR / stored_filename

    try:
        cv_file.save(target_path)
    except OSError:
        return None

    is_new_file = not FREELANCE_APPLICATIONS_FILE.exists()
    try:
        with FREELANCE_APPLICATIONS_FILE.open("a", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            if is_new_file:
                writer.writerow(
                    [
                        "timestamp",
                        "first_name",
                        "last_name",
                        "email",
                        "phone",
                        "geo_area",
                        "available_job",
                        "original_cv_filename",
                        "stored_cv_filename",
                    ]
                )
            writer.writerow(
                [
                    datetime.utcnow().isoformat(timespec="seconds") + "Z",
                    first_name,
                    last_name,
                    email,
                    phone,
                    geo_area,
                    available_job,
                    original_filename,
                    stored_filename,
                ]
            )
    except OSError:
        try:
            target_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    return {
        "original_filename": original_filename,
        "stored_filename": stored_filename,
        "stored_path": str(target_path),
    }


def _deep_get(dictionary: Dict[str, Any], key_path: str) -> Any:
    current: Any = dictionary
    for part in key_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def tr(key: str, **kwargs: Any) -> Any:
    language = getattr(g, "lang", DEFAULT_LANGUAGE)
    value = _deep_get(I18N.get(language, {}), key)
    if value is None:
        value = _deep_get(I18N[DEFAULT_LANGUAGE], key)
    if value is None:
        return key
    if isinstance(value, str) and kwargs:
        try:
            return value.format(**kwargs)
        except (KeyError, ValueError):
            return value
    return value


def _page_title(page_key: Optional[str] = None) -> str:
    if not page_key:
        return "S.V.H Management"
    return f"S.V.H Management - {tr(page_key)}"


@app.before_request
def apply_language() -> None:
    language = session.get(LANGUAGE_SESSION_KEY, DEFAULT_LANGUAGE)
    if language not in LANGUAGES:
        language = DEFAULT_LANGUAGE
    g.lang = language


@app.context_processor
def inject_i18n_context() -> Dict[str, Any]:
    full_path = request.full_path
    current_path = full_path[:-1] if full_path.endswith("?") else full_path
    language_options = []
    for code, meta in LANGUAGES.items():
        language_options.append(
            {
                "code": code,
                "name": meta["name"],
                "flag": meta["flag"],
                "url": url_for("set_language", lang_code=code, next=current_path),
            }
        )

    return {
        "tr": tr,
        "current_lang": g.lang,
        "language_options": language_options,
        "style_version": STYLE_VERSION,
    }


@app.route("/set-language/<lang_code>")
def set_language(lang_code: str):
    normalized = lang_code.lower().strip()
    if normalized not in LANGUAGES:
        normalized = DEFAULT_LANGUAGE
    session[LANGUAGE_SESSION_KEY] = normalized

    next_path = request.args.get("next", "").strip()
    if next_path.startswith("/"):
        return redirect(next_path)
    return redirect(url_for("home"))


@app.route("/healthz")
def healthz():
    return {
        "status": "ok",
        "email_mode": _email_transport_mode(),
        "resend_configured": bool(RESEND_API_KEY and RESEND_EMAIL_FROM and RESEND_EMAIL_TO),
        "smtp_configured": bool(SMTP_HOST and CONTACT_EMAIL_TO and CONTACT_EMAIL_FROM),
    }, 200


def render_site_page(
    template_name: str,
    *,
    page_title: str,
    active_key: str,
    hero_title: str,
    hero_image: str,
    hero_logo: Optional[str] = None,
    extra_context: Optional[Dict[str, object]] = None,
):
    context = extra_context or {}
    return render_template(
        template_name,
        page_title=page_title,
        active_key=active_key,
        hero_title=hero_title,
        hero_image=hero_image,
        hero_logo=hero_logo,
        nav_items=NAV_ITEMS,
        shared_logo=SHARED_LOGO,
        **context,
    )


@app.route("/")
@app.route("/accueil")
def home():
    return render_site_page(
        "index.html",
        page_title=_page_title(),
        active_key="accueil",
        hero_title="",
        hero_image=_hero_image("accueil"),
        hero_logo=None,
    )


@app.route("/a-votre-service")
def a_votre_service():
    return render_site_page(
        "service.html",
        page_title=_page_title("pages.service"),
        active_key="service",
        hero_title=tr("pages.service"),
        hero_image=url_for("static", filename="img/gallery/recep2.jpg"),
    )


@app.route("/qui-sommes-nous")
def qui_sommes_nous():
    return render_site_page(
        "about.html",
        page_title=_page_title("pages.about"),
        active_key="qui",
        hero_title=tr("pages.about"),
        hero_image=_hero_image("qui"),
    )


@app.route("/formations")
def formations():
    return render_site_page(
        "formations.html",
        page_title=_page_title("pages.formations"),
        active_key="formations",
        hero_title=tr("pages.formations"),
        hero_image=_hero_image("formations"),
    )


@app.route("/formations/nos-programmes-de-formations")
def formations_programmes():
    catalog_items = _load_training_catalog()
    available_categories = {
        item.get("category", "")
        for item in catalog_items
        if item.get("category", "")
    }
    catalog_filters: List[Dict[str, str]] = [
        {"key": "all", "label": str(tr("programs.filter_all"))}
    ]
    for category_key in TRAINING_FILTER_ORDER:
        if category_key not in available_categories:
            continue
        label = str(tr(f"programs.filters.{category_key}"))
        if label == f"programs.filters.{category_key}":
            label = category_key.replace("-", " ").title()
        catalog_filters.append({"key": category_key, "label": label})

    return render_site_page(
        "formations_programmes.html",
        page_title=_page_title("pages.programs"),
        active_key="formations",
        hero_title=tr("pages.programs"),
        hero_image=_hero_image("programmes"),
        extra_context={
            "catalog_items": catalog_items,
            "catalog_count": len(catalog_items),
            "catalog_filters": catalog_filters,
        },
    )


@app.route("/remplacements", methods=["GET", "POST"])
def remplacements():
    replacement_error = ""
    replacement_success = ""
    replacement_form = {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "position": "",
        "message": "",
    }
    freelance_error = ""
    freelance_success = ""
    freelance_open = False
    freelance_form = {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
        "geo_area": "",
        "available_job": "",
    }

    if request.method == "POST":
        form_kind = request.form.get("form_kind", "").strip()

        if form_kind == "replacement_request":
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            position = request.form.get("position", "").strip()
            message = request.form.get("message", "").strip()

            replacement_form = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "position": position,
                "message": message,
            }

            if not all([first_name, last_name, email, phone, position, message]):
                replacement_error = tr("replacements.request_errors.required")
            elif not EMAIL_PATTERN.match(email):
                replacement_error = tr("replacements.request_errors.invalid_email")
            elif not PHONE_PATTERN.match(phone):
                replacement_error = tr("replacements.request_errors.invalid_phone")
            else:
                is_saved = _save_replacement_request(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone=phone,
                    position=position,
                    message=message,
                )
                if not is_saved:
                    replacement_error = tr("replacements.request_errors.save_failed")
                else:
                    email_subject = f"[SVH] Demande de remplacement - {position}"
                    email_body = "\n".join(
                        [
                            "Nouvelle demande de remplacement.",
                            "",
                            f"Nom : {last_name}",
                            f"Prénom : {first_name}",
                            f"Email : {email}",
                            f"Téléphone : {phone}",
                            f"Poste recherché : {position}",
                            "",
                            "Message :",
                            message,
                        ]
                    )
                    email_sent = _send_email_notification(
                        email_subject,
                        email_body,
                        reply_to=email,
                    )
                    if _email_notifications_enabled() and not email_sent and EMAIL_NOTIFICATIONS_REQUIRED:
                        replacement_error = tr("replacements.request_errors.email_failed")
                    else:
                        replacement_success = tr("replacements.request_success")
                        replacement_form = {
                            "first_name": "",
                            "last_name": "",
                            "email": "",
                            "phone": "",
                            "position": "",
                            "message": "",
                        }

        elif form_kind == "freelance":
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            email = request.form.get("email", "").strip()
            phone = request.form.get("phone", "").strip()
            geo_area = request.form.get("geo_area", "").strip()
            available_job = request.form.get("available_job", "").strip()
            cv_file = request.files.get("cv_file")

            freelance_form = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "phone": phone,
                "geo_area": geo_area,
                "available_job": available_job,
            }
            freelance_open = True

            if not all([first_name, last_name, email, phone, geo_area, available_job]):
                freelance_error = tr("replacements.freelance_errors.required")
            elif not EMAIL_PATTERN.match(email):
                freelance_error = tr("replacements.freelance_errors.invalid_email")
            elif not PHONE_PATTERN.match(phone):
                freelance_error = tr("replacements.freelance_errors.invalid_phone")
            elif cv_file is None or not (cv_file.filename or "").strip():
                freelance_error = tr("replacements.freelance_errors.cv_required")
            elif not _is_allowed_cv_filename(secure_filename(cv_file.filename)):
                freelance_error = tr("replacements.freelance_errors.cv_extension")
            else:
                saved_application = _save_freelance_application(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone=phone,
                    geo_area=geo_area,
                    available_job=available_job,
                    cv_file=cv_file,
                )
                if not saved_application:
                    freelance_error = tr("replacements.freelance_errors.upload_failed")
                else:
                    cv_filename = saved_application.get("original_filename", "").strip()
                    cv_stored_path_raw = saved_application.get("stored_path", "").strip()
                    cv_stored_path = Path(cv_stored_path_raw) if cv_stored_path_raw else None
                    email_subject = f"[SVH] Nouvelle candidature freelance - {last_name} {first_name}".strip()
                    email_body = "\n".join(
                        [
                            "Nouvelle candidature freelance reçue.",
                            "",
                            f"Nom : {last_name}",
                            f"Prénom : {first_name}",
                            f"Email : {email}",
                            f"Téléphone : {phone}",
                            f"Zone géographique : {geo_area}",
                            f"Métier disponible : {available_job}",
                            f"CV transmis : {cv_filename}",
                            "Pièce jointe : oui",
                        ]
                    )
                    email_sent = _send_email_notification(
                        email_subject,
                        email_body,
                        reply_to=email,
                        attachment_path=(
                            cv_stored_path
                            if (cv_stored_path and cv_stored_path.is_file())
                            else None
                        ),
                        attachment_name=cv_filename,
                    )
                    if _email_notifications_enabled() and not email_sent and EMAIL_NOTIFICATIONS_REQUIRED:
                        freelance_error = tr("replacements.freelance_errors.email_failed")
                        freelance_open = True
                    else:
                        candidate_subject = "[SVH] Candidature bien reçue"
                        candidate_body = "\n".join(
                            [
                                f"Bonjour {first_name},",
                                "",
                                "Nous avons bien reçu votre candidature freelance chez S.V.H Management.",
                                "Merci pour votre confiance.",
                                "",
                                "Nous reviendrons vers vous dès qu'une mission adaptée à votre profil sera disponible.",
                                "",
                                "Cordialement,",
                                "S.V.H Management",
                                "contact@svhmanagement.fr",
                                "07 67 31 47 55",
                            ]
                        )
                        candidate_email_sent = _send_email_notification(
                            candidate_subject,
                            candidate_body,
                            to_email=email,
                            from_email=(SMTP_USERNAME or CONTACT_EMAIL_FROM),
                            reply_to=CONTACT_EMAIL_TO,
                        )
                        if not candidate_email_sent:
                            app.logger.warning(
                                "Echec envoi accuse reception candidature freelance. email=%s",
                                email,
                            )
                        freelance_success = tr("replacements.freelance_success")
                        freelance_open = False
                        freelance_form = {
                            "first_name": "",
                            "last_name": "",
                            "email": "",
                            "phone": "",
                            "geo_area": "",
                            "available_job": "",
                        }

    return render_site_page(
        "remplacements.html",
        page_title=_page_title("pages.replacements"),
        active_key="remplacements",
        hero_title=tr("pages.replacements"),
        hero_image=_hero_image("remplacements"),
        extra_context={
            "replacement_error": replacement_error,
            "replacement_success": replacement_success,
            "replacement_form": replacement_form,
            "freelance_error": freelance_error,
            "freelance_success": freelance_success,
            "freelance_open": freelance_open,
            "freelance_form": freelance_form,
        },
    )


@app.route("/conseil-assistance")
def conseil_assistance():
    return render_site_page(
        "conseil.html",
        page_title=_page_title("pages.consulting"),
        active_key="conseil",
        hero_title=tr("pages.consulting"),
        hero_image=_hero_image("conseil"),
    )


@app.route("/ressources", methods=["GET", "POST"])
def ressources():
    premium_error = ""
    premium_form = {
        "first_name": "",
        "last_name": "",
        "email": "",
        "phone": "",
    }

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip()
        phone = request.form.get("phone", "").strip()
        premium_form = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "phone": phone,
        }

        if not first_name or not last_name or not email or not phone:
            premium_error = tr("resources.errors.required")
        elif not EMAIL_PATTERN.match(email):
            premium_error = tr("resources.errors.invalid_email")
        elif not PHONE_PATTERN.match(phone):
            premium_error = tr("resources.errors.invalid_phone")
        else:
            _save_premium_lead(first_name, last_name, email, phone)
            session["resources_premium_access"] = True
            session["resources_premium_identity"] = f"{first_name} {last_name}".strip()
            return redirect(url_for("ressources"))

    premium_access = bool(session.get("resources_premium_access", False))
    drive_resources = _load_drive_resources()
    return render_site_page(
        "ressources.html",
        page_title=_page_title("pages.resources"),
        active_key="ressources",
        hero_title=tr("pages.resources"),
        hero_image=_hero_image("ressources"),
        extra_context={
            "premium_access": premium_access,
            "premium_error": premium_error,
            "premium_form": premium_form,
            "drive_resources": drive_resources,
            "resources_count": len(drive_resources),
            "premium_identity": session.get("resources_premium_identity", ""),
        },
    )


@app.route("/contact-et-infos", methods=["GET", "POST"])
def contact_infos():
    contact_error = ""
    contact_success = ""
    contact_form = {
        "name": "",
        "email": "",
        "subject": "",
        "message": "",
    }

    if request.method == "POST":
        form_kind = request.form.get("form_kind", "").strip()
        if form_kind == "contact_request":
            name = request.form.get("name", "").strip()
            email = request.form.get("email", "").strip()
            subject = request.form.get("subject", "").strip()
            message = request.form.get("message", "").strip()
            contact_form = {
                "name": name,
                "email": email,
                "subject": subject,
                "message": message,
            }

            if not all([name, email, subject, message]):
                contact_error = tr("contact.errors.required")
            elif not EMAIL_PATTERN.match(email):
                contact_error = tr("contact.errors.invalid_email")
            else:
                is_saved = _save_contact_request(
                    name=name,
                    email=email,
                    subject=subject,
                    message=message,
                )
                if not is_saved:
                    contact_error = tr("contact.errors.save_failed")
                else:
                    email_subject = f"[SVH] Contact - {subject}"
                    email_body = "\n".join(
                        [
                            "Nouveau message depuis le formulaire Contact.",
                            "",
                            f"Nom : {name}",
                            f"Email : {email}",
                            f"Objet : {subject}",
                            "",
                            "Message :",
                            message,
                        ]
                    )
                    email_sent = _send_email_notification(
                        email_subject,
                        email_body,
                        reply_to=email,
                    )
                    if _email_notifications_enabled() and not email_sent and EMAIL_NOTIFICATIONS_REQUIRED:
                        contact_error = tr("contact.errors.email_failed")
                    else:
                        contact_success = tr("contact.form_success")
                        contact_form = {
                            "name": "",
                            "email": "",
                            "subject": "",
                            "message": "",
                        }

    return render_site_page(
        "contact.html",
        page_title=_page_title("pages.contact"),
        active_key="contact",
        hero_title=tr("pages.contact"),
        hero_image=_hero_image("contact"),
        extra_context={
            "contact_error": contact_error,
            "contact_success": contact_success,
            "contact_form": contact_form,
        },
    )


# Legacy URLs kept for compatibility with the previous local prototype.
@app.route("/about")
def legacy_about():
    return redirect(url_for("qui_sommes_nous"), code=301)


@app.route("/contact")
def legacy_contact():
    return redirect(url_for("contact_infos"), code=301)


@app.route("/conseil")
def legacy_conseil():
    return redirect(url_for("conseil_assistance"), code=301)


if __name__ == "__main__":
    debug_mode = _env_flag("FLASK_DEBUG", False)
    port = _env_int("PORT", 5001)
    app.run(host="0.0.0.0", port=port, debug=debug_mode)
