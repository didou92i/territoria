# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Test de bout en bout embarqué : `python -m outils verifier` parcourt l'application comme un navigateur.

Il prépare un espace d'essai (réutilisé d'une exécution à l'autre : l'application ne sait pas supprimer un espace),
importe un PDF fictif de deux pages, interroge les documents, rédige un arrêté, vérifie les refus d'accès, puis
supprime tout ce qu'il a créé d'autre (par les routes DELETE). Il suppose un serveur IA répondant au format OpenAI ;
dans la CI, c'est `tests/faux_llm.py`, avec le mode `strict`. Sans `strict` (installation réelle, vrai modèle), il ne
vérifie pas la formulation de la réponse du modèle, seulement que la chaîne complète a répondu.

Il n'affiche rien et ne lève jamais d'exception portant un contenu : un contrôle qui échoue donne
`AssertionError('<nom du contrôle>')`, sans message de serveur, sans question, sans texte de document.
"""
from __future__ import annotations

import http.client
import io
import json
import re
import time
from typing import Callable
from urllib.parse import urlencode, urlsplit

from config import REGLAGES

CIBLE = 'http://127.0.0.1:8878'
DELAI = 30  # secondes par requête ordinaire (espaces, import, recherche, téléchargement, suppression)
MARGE_IA = 30  # secondes ajoutées au délai de Territoria pour n8n (TERRITORIA_DELAI_N8N) sur POST /api/chat
PAUSE_REESSAI = 3.0  # secondes avant de rejouer une demande qui a rendu 502 (n8n qui n'a pas fini d'enregistrer ses webhooks)
TITRE_ESPACE = 'Essai de bout en bout'
NOM_PDF = 'essai-bout-en-bout.pdf'
# Deux pages ASCII : la première ne partage aucun mot avec la recherche, donc la page 2 est la seule à répondre.
PAGES_PDF = ('Recueil d essai, page de garde.', 'Les archives municipales fictives ouvrent le mardi matin.')
RECHERCHE = 'archives municipales fictives'
QUESTION_SOURCEE = 'Quand ouvrent les archives municipales fictives ?'
QUESTION_SANS_SOURCE = 'Quelle est la profondeur de la mer de la Tranquillité ?'
QUESTION_ARRETE = ('Rédige un arrêté pour la Commune de Démoville interdisant le stationnement rue des Essais '
                   'pour un déménagement.')
ORIGINE_TIERCE = 'http://origine-tierce.invalide'
IDENTIFIANT = re.compile(r'[0-9a-f]{32}')
TYPE_WORD = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _delai(chemin: str) -> int:
    """Délai d'une requête. POST /api/chat (réponse sourcée, rédaction de l'arrêté) traverse n8n et le serveur IA :
    le client attend toute la chaîne (le délai de Territoria pour n8n, 960 s par défaut, plus une marge) et
    n'abandonne jamais avant elle ; sur un PC sans carte graphique, ou au premier appel après un redémarrage (modèle
    chargé à la demande), une réponse peut prendre plusieurs minutes. Les autres requêtes gardent un délai court."""
    return REGLAGES.delai_n8n + MARGE_IA if chemin.split('?', 1)[0] == '/api/chat' else DELAI


def _envoyer(cible: str, methode: str, chemin: str, corps: bytes | None, entetes: dict[str, str]):
    """Une requête HTTP ; renvoie (statut, corps en octets, en-têtes de la réponse en minuscules).

    http.client permet d'imposer l'en-tête Host, ce que les contrôles d'origine de l'application exigent ; il ne passe
    par aucun proxy."""
    parties = urlsplit(cible)
    connexion = http.client.HTTPConnection(parties.hostname, parties.port or 80, timeout=_delai(chemin))
    try:
        connexion.request(methode, chemin, body=corps, headers=entetes)
        reponse = connexion.getresponse()
        return reponse.status, reponse.read(), {cle.lower(): valeur for cle, valeur in reponse.getheaders()}
    finally:
        connexion.close()


def _exiger(condition) -> None:
    if not condition:
        raise AssertionError


class _Essai:
    """État d'un parcours : l'adresse visée, l'origine déclarée et tout ce qui a été créé (pour le supprimer)."""

    def __init__(self, cible: str, origine: str, strict: bool = False, jeton: str = ''):
        self.cible = cible
        self.jeton = jeton  # session du compte technique quand l'instance a des comptes
        self.strict = strict
        self.origine = origine.rstrip('/')
        self.hote = urlsplit(origine).netloc
        self.espace = ''
        self.arrete = ''  # adresse de téléchargement du Word rédigé
        self.documents: list[str] = []
        self.conversations: list[str] = []

    def appeler(self, methode: str, chemin: str, corps: bytes | None = None, *, type_contenu: str = 'application/json',
                hote: str | None = None, origine: str | None = None, entetes_en_plus: dict[str, str] | None = None):
        """Requête de navigateur : Host de l'origine, et Origin sur POST et DELETE (`origine=''` retire l'en-tête)."""
        entetes = {'Host': self.hote if hote is None else hote, 'Accept': 'application/json', **(entetes_en_plus or {})}
        if self.jeton:
            entetes['Cookie'] = f'territoria_session={self.jeton}'
        envoyee = self.origine if origine is None else origine
        if methode in ('POST', 'DELETE') and envoyee:
            entetes['Origin'] = envoyee
        if corps is not None:
            entetes['Content-Type'] = type_contenu
        return _envoyer(self.cible, methode, chemin, corps, entetes)

    def json(self, methode: str, chemin: str, donnees=None, **options):
        """Appel JSON ; renvoie (statut, objet JSON ou None quand le corps n'est pas du JSON)."""
        corps = None if donnees is None else json.dumps(donnees, ensure_ascii=False).encode('utf-8')
        statut, brut, _ = self.appeler(methode, chemin, corps, **options)
        try:
            return statut, json.loads(brut)
        except ValueError:
            return statut, None

    def chemins_a_supprimer(self) -> list[str]:
        return ([f'/api/conversations/{ident}' for ident in self.conversations]
                + [f'/api/documents/{ident}' for ident in self.documents])

    def supprimer(self, chemin: str) -> int:
        statut, _ = self.json('DELETE', chemin)
        return statut


def _pdf_deux_pages() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    sortie = io.BytesIO()
    page = canvas.Canvas(sortie, pagesize=A4, invariant=1)
    for texte in PAGES_PDF:
        page.setFont('Helvetica', 12)
        page.drawString(72, 760, texte)
        page.showPage()
    page.save()
    return sortie.getvalue()


def _identifiant(valeur) -> bool:
    return isinstance(valeur, str) and bool(IDENTIFIANT.fullmatch(valeur))


def _discuter(e: _Essai, question: str, reessai_502: bool = False) -> dict:
    """POST /api/chat dans l'espace d'essai ; la conversation créée est notée aussitôt pour être supprimée.

    Avec `reessai_502`, une réponse 502 (le workflow n8n ou le modèle n'a pas répondu) est rejouée une seule fois."""
    tentatives = 2 if reessai_502 else 1
    for tentative in range(tentatives):
        statut, reponse = e.json('POST', '/api/chat', {'question': question, 'space': e.espace, 'mode': 'sources'})
        if isinstance(reponse, dict) and _identifiant(reponse.get('conversation')):
            e.conversations.append(reponse['conversation'])
        if statut != 502 or tentative == tentatives - 1:
            break
        time.sleep(PAUSE_REESSAI)
    _exiger(statut == 200 and isinstance(reponse, dict))
    return reponse


def _espace(e: _Essai) -> None:
    """Réutilise l'espace d'essai d'une exécution précédente, sinon le crée.

    Les espaces ne se suppriment pas (l'application n'a de DELETE que pour les conversations et les documents) : sans
    réutilisation, chaque exécution en laisserait un de plus dans l'interface. Seul un espace vide au titre exact est
    réutilisé, pour que les recherches ne rencontrent jamais un document laissé par une exécution interrompue."""
    statut, espaces = e.json('GET', '/api/spaces')
    _exiger(statut == 200 and isinstance(espaces, list))
    libres = [x for x in espaces if isinstance(x, dict) and x.get('title') == TITRE_ESPACE and x.get('count') == 0
              and _identifiant(x.get('id'))]
    if libres:
        e.espace = libres[0]['id']
        return
    statut, corps = e.json('POST', '/api/spaces', {'title': TITRE_ESPACE})
    _exiger(statut == 201 and _identifiant(corps.get('id')))
    e.espace = corps['id']


def _import_pdf(e: _Essai) -> None:
    statut, brut, _ = e.appeler('POST', f'/api/upload/{e.espace}?' + urlencode({'name': NOM_PDF}), _pdf_deux_pages(),
                                type_contenu='application/pdf')
    corps = json.loads(brut)
    if statut == 201 and _identifiant(corps.get('id')):
        e.documents.append(corps['id'])
    _exiger(statut == 201 and e.documents)


def _recherche_page2(e: _Essai) -> None:
    statut, resultats = e.json('GET', '/api/search?' + urlencode({'q': RECHERCHE, 'space': e.espace}))
    _exiger(statut == 200 and isinstance(resultats, list) and resultats)
    _exiger(resultats[0].get('id') == e.documents[0] and resultats[0].get('page') == 2)


def _sans_source_sans_ia(e: _Essai) -> None:
    reponse = _discuter(e, QUESTION_SANS_SOURCE)
    _exiger(reponse.get('model_used') is False and reponse.get('sources') == [])


def _reponse_sourcee(e: _Essai) -> None:
    reponse = _discuter(e, QUESTION_SOURCEE, reessai_502=True)
    sources = reponse.get('sources')
    _exiger(reponse.get('model_used') is True)
    if e.strict:  # un vrai modèle peut oublier la citation : l'application répond alors par son message de prudence
        _exiger('[1]' in str(reponse.get('answer')))
    _exiger(isinstance(sources, list) and sources and sources[0].get('page') == 2
            and sources[0].get('id') == e.documents[0])


def _texte_word(contenu: bytes) -> str:
    from docx import Document
    return '\n'.join(paragraphe.text for paragraphe in Document(io.BytesIO(contenu)).paragraphs)


def _arrete_word(e: _Essai) -> None:
    # Hors mode strict, un vrai modèle peut une fois sur quelques-unes rendre un JSON que la validation refuse (502).
    reponse = _discuter(e, QUESTION_ARRETE, reessai_502=not e.strict)
    artefact = reponse.get('artifact')
    _exiger(isinstance(artefact, dict) and artefact.get('kind') == 'arrete' and _identifiant(artefact.get('id')))
    _exiger(artefact.get('download') == f"/api/arretes/{artefact['id']}/file")
    articles = artefact['document']['articles']
    _exiger(isinstance(articles, list) and articles)
    statut, contenu, entetes = e.appeler('GET', artefact['download'])
    _exiger(statut == 200 and entetes.get('content-type', '').startswith(TYPE_WORD))
    texte = _texte_word(contenu)
    _exiger(all(article['title'] in texte for article in articles))
    e.arrete = artefact['download']


def _origine_tierce_refusee(e: _Essai) -> None:
    # Titre vide : si le refus manquait, la réponse serait une erreur 400 sans rien créer, jamais un 403.
    statut, _ = e.json('POST', '/api/spaces', {'title': ''}, origine=ORIGINE_TIERCE)
    _exiger(statut == 403)
    statut, _ = e.json('POST', '/api/spaces', {'title': ''}, origine='')
    _exiger(statut == 403)
    statut, _ = e.json('GET', '/api/spaces', hote='hote-tiers.invalide')
    _exiger(statut == 403)


def _interne_sans_jeton_refuse(e: _Essai) -> None:
    statut, _ = e.json('POST', '/api/verify', {})
    _exiger(statut == 403)
    statut, _ = e.json('POST', '/api/verify', {}, entetes_en_plus={'X-Territoria-Service-Token': 'jeton-invalide'})
    _exiger(statut == 403)


def _nettoyage(e: _Essai) -> None:
    chemins = e.chemins_a_supprimer()
    _exiger(chemins)
    # Les listes complètes sont parcourues (pas de court-circuit) : un échec n'empêche pas de supprimer le reste.
    _exiger(all([e.supprimer(chemin) == 200 for chemin in chemins]))
    _exiger(all([e.supprimer(chemin) == 404 for chemin in chemins]))
    statut, resultats = e.json('GET', '/api/search?' + urlencode({'q': RECHERCHE, 'space': e.espace}))
    _exiger(statut == 200 and resultats == [])
    statut, _, _ = e.appeler('GET', e.arrete)
    _exiger(statut == 404)
    e.conversations.clear()
    e.documents.clear()


CONTROLES: tuple[tuple[str, Callable[[_Essai], None]], ...] = (
    ('espace', _espace),
    ('import_pdf', _import_pdf),
    ('recherche_page2', _recherche_page2),
    ('sans_source_sans_ia', _sans_source_sans_ia),
    ('reponse_sourcee', _reponse_sourcee),
    ('arrete_word', _arrete_word),
    ('origine_tierce_refusee', _origine_tierce_refusee),
    ('interne_sans_jeton_refuse', _interne_sans_jeton_refuse),
    ('nettoyage', _nettoyage),
)


def _jouer(nom: str, controle: Callable[[_Essai], None], e: _Essai) -> None:
    """Exécute un contrôle ; tout écart devient `AssertionError(nom)`, levée hors du `except` pour ne rien chaîner."""
    try:
        controle(e)
        reussi = True
    except Exception:  # réponse inattendue, serveur injoignable, JSON ou Word illisible : jamais de détail
        reussi = False
    if not reussi:
        raise AssertionError(nom)


def _nettoyer_au_mieux(e: _Essai) -> None:
    for chemin in e.chemins_a_supprimer():
        try:
            e.supprimer(chemin)
        except Exception:
            pass


def executer(cible: str = CIBLE, origine: str = REGLAGES.origine, strict: bool = False, jeton: str = '') -> list[str]:
    """Joue les contrôles dans l'ordre et renvoie leurs noms ; le premier échec lève `AssertionError(<nom>)`.

    `strict` (la CI avec la fausse IA) exige en plus la citation `[1]` dans la réponse sourcée et ne rejoue pas la
    rédaction de l'arrêté. Sans lui, la réponse sourcée doit seulement être arrivée par le modèle avec la bonne source,
    et la rédaction de l'arrêté est rejouée une fois après un 502 (la demande sourcée l'est dans les deux modes).
    Ce qui a été créé (hors l'espace d'essai) est supprimé dans tous les cas, y compris quand un contrôle échoue."""
    essai = _Essai(cible, origine, strict, jeton)
    faits: list[str] = []
    termine = False
    try:
        for nom, controle in CONTROLES:
            _jouer(nom, controle, essai)
            faits.append(nom)
        termine = True
    finally:
        if not termine:
            _nettoyer_au_mieux(essai)
    return faits
