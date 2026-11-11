# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Administration de l'instance : règles d'usage, assistants, journal d'activité sans contenu, statistiques et santé.

Le journal d'activité ne garde que des codes d'action et des identifiants : jamais une question, un titre ou un nom de
fichier. Il sert à l'écran « Activité » et aux statistiques, et il est effacé au bout de `CONSERVATION_ACTIVITE_JOURS`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base.connexion import ouvrir

REGLES_DEFAUT = {'import_autorise': True, 'assistants_perso_autorises': True, 'partage_autorise': True, 'conservation_jours': 0}
DUREES_CONSERVATION = (0, 30, 90, 180, 365)  # 0 : conversations gardées sans limite
CONSERVATION_ACTIVITE_JOURS = 365
# Fonctions du logiciel présentées comme des assistants : ni désactivables ni modifiables.
FONCTIONS_INTEGREES = frozenset({'arrete', 'deliberation'})
ACTIONS = {
    'question': 'Question posée',
    'seance_creee': 'Séance créée',
    'transcription_lancee': 'Transcription lancée',
    'transcription_validee': 'Transcription validée',
    'seance_redigee': 'Séance rédigée',
    'seance_supprimee': 'Séance supprimée',
    'arrete': 'Arrêté rédigé',
    'deliberation': 'Délibération rédigée',
    'precision': 'Précision demandée pour un arrêté',
    'document_importe': 'Document importé',
    'document_supprime': 'Document supprimé',
    'conversation_supprimee': 'Conversation supprimée',
    'conversations_purgees': 'Anciennes conversations supprimées (durée de conservation)',
    'visiteurs_effaces': 'Comptes de visiteurs effacés avec leurs contenus (démonstration, 24 heures)',
    'espace_cree': 'Espace créé',
    'assistant_cree': 'Assistant créé',
    'assistant_modifie': 'Assistant modifié',
    'assistant_active': 'Assistant activé',
    'assistant_desactive': 'Assistant désactivé',
    'assistant_retabli': 'Assistant rétabli dans sa version d’origine',
    'assistant_supprime': 'Assistant supprimé',
    'identite_modifiee': 'Identité de la collectivité modifiée',
    'acte_numerote': 'Numéro attribué à un acte',
    'numerotation_modifiee': 'Réglages de numérotation modifiés',
    'ia_modifiee': 'Réglages de l’IA modifiés',
    'regles_modifiees': 'Règles d’usage modifiées',
    'premier_compte_cree': 'Premier compte administrateur créé',
    'connexion': 'Connexion',
    'connexion_visiteur': 'Connexion d’un visiteur (démonstration)',
    'publication_ajoutee': 'Acte ajouté à la publication',
    'dossier_publication': 'Dossier de publication préparé',
    'publication_validee': 'Publication validée',
    'publication_en_ligne': 'Acte déclaré mis en ligne',
    'publication_retiree': 'Acte retiré de la publication',
    'publication_supprimee': 'Acte retiré de la liste à publier',
    'connexion_refusee': 'Connexion refusée',
    'deconnexion': 'Déconnexion',
    'compte_cree': 'Compte créé',
    'compte_modifie': 'Compte modifié',
    'compte_desactive': 'Compte désactivé',
    'compte_reactive': 'Compte réactivé',
    'mot_de_passe_reinitialise': 'Mot de passe réinitialisé',
    'mot_de_passe_change': 'Mot de passe changé',
    'contenus_transferes': 'Espaces et assistants transférés',
    'service_cree': 'Service créé',
    'service_modifie': 'Service renommé',
    'service_supprime': 'Service supprimé',
    'partage_modifie': 'Partage accordé ou modifié',
    'partage_retire': 'Partage retiré',
    'evaluation': 'Réponse évaluée',
    'signalement_envoye': 'Réponse signalée aux référents',
    'signalement_traite': 'Signalement marqué traité',
    'signalement_rouvert': 'Signalement rouvert',
    'signalement_supprime': 'Signalement supprimé',
}
STATUTS = frozenset({'ok', 'erreur', 'refuse'})
CODE = re.compile(r'[\w.:-]{0,80}')


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _horodatage(moment: datetime) -> str:
    return moment.isoformat(timespec='seconds')


def _code(valeur) -> str:
    texte = '' if valeur is None else str(valeur)
    return texte if CODE.fullmatch(texte) else 'invalide'


def noter(base: Path, action: str, objet='', detail='', duree_ms: int | None = None, statut='ok', utilisateur='') -> None:
    """Ajoute une ligne au journal d'activité. Une action hors liste est une erreur de programmation, pas une donnée."""
    if action not in ACTIONS or statut not in STATUTS:
        raise ValueError(f'Action ou statut inconnu : {action} / {statut}')
    with ouvrir(base) as c:
        c.execute('INSERT INTO activite(quand, utilisateur, action, objet, detail, duree_ms, statut) VALUES (?,?,?,?,?,?,?)',
                  (_horodatage(_maintenant()), _code(utilisateur), action, _code(objet), _code(detail),
                   int(duree_ms) if duree_ms is not None else None, statut))


def regles(base: Path) -> dict:
    with ouvrir(base) as c:
        ligne = c.execute("SELECT valeur FROM parametres WHERE cle='regles'").fetchone()
    try:
        enregistrees = json.loads(ligne['valeur']) if ligne else {}
    except ValueError:
        enregistrees = {}
    valeurs = {**REGLES_DEFAUT, **{cle: v for cle, v in enregistrees.items() if cle in REGLES_DEFAUT}} if isinstance(enregistrees, dict) else dict(REGLES_DEFAUT)
    return valeurs


def enregistrer_regles(base: Path, donnees) -> dict:
    if not isinstance(donnees, dict):
        raise ValueError('Règles invalides.')
    valeurs = regles(base)
    for cle in ('import_autorise', 'assistants_perso_autorises', 'partage_autorise'):
        if cle in donnees:
            if not isinstance(donnees[cle], bool):
                raise ValueError('Règles invalides.')
            valeurs[cle] = donnees[cle]
    if 'conservation_jours' in donnees:
        jours = donnees['conservation_jours']
        if isinstance(jours, bool) or jours not in DUREES_CONSERVATION:
            raise ValueError('Durée de conservation invalide.')
        valeurs['conservation_jours'] = jours
    with ouvrir(base) as c:
        c.execute("INSERT INTO parametres(cle, valeur, modifie) VALUES ('regles', ?, ?) "
                  'ON CONFLICT(cle) DO UPDATE SET valeur=excluded.valeur, modifie=excluded.modifie',
                  (json.dumps(valeurs), _horodatage(_maintenant())))
    return valeurs


def exiger(base: Path, regle: str, message: str) -> None:
    """Refus côté serveur d'une action désactivée par l'administration (l'écran masque seulement le bouton)."""
    if not regles(base)[regle]:
        raise PermissionError(message)


def _visible(proprietaire):
    """Filtre SQL : les assistants du logiciel, et les assistants personnels de `proprietaire` (tous si None)."""
    return ('', ()) if proprietaire is None else (' WHERE (custom=0 OR proprietaire=?)', (proprietaire,))


def assistants(base: Path, modeles, proprietaire: str | None = None) -> list[dict]:
    """Assistants actifs ou non, avec ce qui les distingue de leur version d'origine. Avec `proprietaire`, les assistants
    personnels des autres comptes n'y figurent pas : ils restent privés, administration comprise."""
    origines = {ident: (titre, description, consigne) for ident, titre, description, consigne in modeles}
    filtre, valeurs = _visible(proprietaire)
    with ouvrir(base) as c:
        lignes = c.execute('SELECT id,title,description,instructions,custom,actif FROM assistants' + filtre + ' ORDER BY custom,rowid', valeurs).fetchall()
    resultat = []
    for ligne in lignes:
        integre = ligne['id'] in FONCTIONS_INTEGREES
        origine = origines.get(ligne['id'])
        resultat.append(dict(ligne) | {
            'actif': bool(ligne['actif']), 'custom': bool(ligne['custom']), 'integre': integre,
            'modifie': bool(origine) and (ligne['title'], ligne['description'], ligne['instructions']) != origine})
    return resultat


def _texte(donnees, cle, maximum, obligatoire):
    valeur = donnees.get(cle)
    if not isinstance(valeur, str):
        raise ValueError('Assistant invalide.')
    valeur = valeur.strip()
    if obligatoire and not valeur:
        raise ValueError('Le nom et les instructions sont obligatoires.')
    if len(valeur) > maximum:
        raise ValueError('Texte trop long.')
    return valeur


def modifier_assistant(base: Path, ident: str, donnees, proprietaire: str | None = None) -> list[str]:
    """Active, désactive ou modifie un assistant ; rend les actions faites, pour le journal d'activité."""
    if not isinstance(donnees, dict):
        raise ValueError('Assistant invalide.')
    if ident in FONCTIONS_INTEGREES:
        raise PermissionError('Cette fonction intégrée ne se modifie pas ici.')
    actions = []
    filtre, valeurs = _visible(proprietaire)
    with ouvrir(base) as c:
        ligne = c.execute('SELECT actif FROM assistants' + (filtre + ' AND' if filtre else ' WHERE') + ' id=?', (*valeurs, ident)).fetchone()
        if not ligne:
            raise LookupError(ident)
        if any(cle in donnees for cle in ('title', 'description', 'instructions')):
            textes = (_texte(donnees, 'title', 100, True), _texte(donnees, 'description', 300, False),
                      _texte(donnees, 'instructions', 6000, True))
            c.execute('UPDATE assistants SET title=?,description=?,instructions=? WHERE id=?', (*textes, ident))
            actions.append('assistant_modifie')
        if 'actif' in donnees:
            if not isinstance(donnees['actif'], bool):
                raise ValueError('Assistant invalide.')
            if donnees['actif'] != bool(ligne['actif']):
                c.execute('UPDATE assistants SET actif=? WHERE id=?', (int(donnees['actif']), ident))
                actions.append('assistant_active' if donnees['actif'] else 'assistant_desactive')
    return actions


def retablir_assistant(base: Path, ident: str, modeles) -> None:
    origine = next((m for m in modeles if m[0] == ident), None)
    if not origine or ident in FONCTIONS_INTEGREES:
        raise LookupError(ident)
    with ouvrir(base) as c:
        c.execute('UPDATE assistants SET title=?,description=?,instructions=? WHERE id=?', (*origine[1:], ident))


def supprimer_assistant(base: Path, ident: str, proprietaire: str | None = None) -> None:
    """Seuls les assistants personnalisés se suppriment ; ceux du logiciel se désactivent."""
    filtre, valeurs = _visible(proprietaire)
    with ouvrir(base) as c:
        ligne = c.execute('SELECT custom FROM assistants' + (filtre + ' AND' if filtre else ' WHERE') + ' id=?', (*valeurs, ident)).fetchone()
        if not ligne:
            raise LookupError(ident)
        if not ligne['custom']:
            raise PermissionError('Un assistant fourni avec Territoria se désactive mais ne se supprime pas.')
        c.execute('DELETE FROM assistants WHERE id=?', (ident,))


def activite(base: Path, limite=100, avant: int | None = None) -> list[dict]:
    limite = max(1, min(int(limite), 500))
    with ouvrir(base) as c:
        lignes = c.execute('SELECT a.*, COALESCE(u.nom, \'\') AS nom_utilisateur FROM activite a LEFT JOIN utilisateurs u ON u.id=a.utilisateur'
                           + (' WHERE a.id<?' if avant else '') + ' ORDER BY a.id DESC LIMIT ?',
                           ((int(avant), limite) if avant else (limite,))).fetchall()
    return [dict(ligne) | {'libelle': ACTIONS.get(ligne['action'], ligne['action'])} for ligne in lignes]


def purger_activite(base: Path, maintenant: datetime | None = None) -> int:
    limite = _horodatage((maintenant or _maintenant()) - timedelta(days=CONSERVATION_ACTIVITE_JOURS))
    with ouvrir(base) as c:
        return c.execute('DELETE FROM activite WHERE quand<?', (limite,)).rowcount


def statistiques(base: Path, jours=30, maintenant: datetime | None = None) -> dict:
    """Usage des `jours` derniers jours, tiré du journal d'activité : des comptes et des durées, jamais de contenu."""
    fin = (maintenant or _maintenant()).astimezone(timezone.utc)
    debut = (fin - timedelta(days=jours - 1)).date()
    depuis = debut.isoformat()
    with ouvrir(base) as c:
        par_jour = {ligne['jour']: dict(ligne) for ligne in c.execute(
            "SELECT substr(quand,1,10) AS jour, SUM(action='question') AS questions, SUM(action IN ('arrete','deliberation')) AS arretes "
            "FROM activite WHERE quand>=? AND action IN ('question','arrete','deliberation') GROUP BY jour", (depuis,))}
        totaux = dict(c.execute(
            "SELECT SUM(action IN ('question','arrete','deliberation')) AS demandes, SUM(action='question') AS questions, "
            "SUM(action IN ('arrete','deliberation')) AS arretes, SUM(action='document_importe' AND statut='ok') AS documents_importes, "
            "SUM(action IN ('question','arrete','deliberation','precision') AND statut='erreur') AS erreurs, "
            "CAST(AVG(CASE WHEN action IN ('question','arrete','deliberation') AND statut='ok' THEN duree_ms END) AS INTEGER) AS duree_moyenne_ms, "
            "MAX(CASE WHEN action IN ('question','arrete','deliberation') AND statut='ok' THEN duree_ms END) AS duree_max_ms "
            'FROM activite WHERE quand>=?', (depuis,)).fetchone())
        par_assistant = [dict(ligne) for ligne in c.execute(
            "SELECT objet AS assistant, COUNT(*) AS nombre FROM activite WHERE quand>=? AND action IN ('question','arrete','deliberation') "
            "AND objet!='' GROUP BY objet ORDER BY nombre DESC, objet LIMIT 8", (depuis,))]
        par_mode = {ligne['detail']: ligne['nombre'] for ligne in c.execute(
            "SELECT detail, COUNT(*) AS nombre FROM activite WHERE quand>=? AND action='question' GROUP BY detail", (depuis,))}
        # Dernière note de chaque réponse (un agent peut changer d'avis) : 1 utile, -1 à améliorer, 0 retirée.
        notes = {ligne['detail']: ligne['nombre'] for ligne in c.execute(
            "SELECT detail, COUNT(*) AS nombre FROM activite WHERE id IN (SELECT MAX(id) FROM activite WHERE action='evaluation' "
            'AND quand>=? GROUP BY objet) GROUP BY detail', (depuis,))}
        signales = c.execute("SELECT COUNT(*) FROM activite WHERE action='signalement_envoye' AND quand>=?", (depuis,)).fetchone()[0]
    serie = []
    for decalage in range(jours):
        jour = (debut + timedelta(days=decalage)).isoformat()
        ligne = par_jour.get(jour, {})
        serie.append({'jour': jour, 'questions': ligne.get('questions') or 0, 'arretes': ligne.get('arretes') or 0})
    return {'jours': jours, 'par_jour': serie, 'totaux': {cle: valeur if cle.startswith('duree') else valeur or 0 for cle, valeur in totaux.items()},
            'par_assistant': par_assistant, 'par_mode': {'sources': par_mode.get('sources', 0), 'libre': par_mode.get('libre', 0)},
            'retours': {'utiles': notes.get('1', 0), 'a_ameliorer': notes.get('-1', 0), 'signalements': signales}}


def _taille(dossier: Path, extension='') -> tuple[int, int]:
    """Nombre de fichiers portant l'extension (tous si vide) et taille totale du dossier."""
    nombre = taille = 0
    if dossier.is_dir():
        for chemin in dossier.iterdir():
            if chemin.is_file():
                nombre += chemin.suffix == extension or not extension
                taille += chemin.stat().st_size
    return nombre, taille


BATTEMENT_MAX_S = 120  # au-delà, le conteneur de transcription est tenu pour arrêté


def _transcription(donnees: Path, base: Path) -> dict:
    """État du conteneur de transcription, d'après son battement (écrit toutes les 30 s), et séances qui l'attendent."""
    from travaux import protocole
    battement = protocole.lire_json(donnees / 'audio' / protocole.BATTEMENT) or {}
    maj = battement.get('maj') if isinstance(battement.get('maj'), str) else None
    try:
        date = datetime.fromisoformat(maj) if maj else None
        actif = date is not None and (datetime.now(timezone.utc) - date).total_seconds() < BATTEMENT_MAX_S
    except (TypeError, ValueError):
        actif = False
    with ouvrir(base) as c:
        attente = c.execute("SELECT COUNT(*) FROM seances WHERE etat IN ('attente', 'transcription')").fetchone()[0]
    modeles_ok = battement.get('modeles_ok')
    return {'actif': actif, 'maj': maj, 'modele': battement.get('modele') if isinstance(battement.get('modele'), str) else None,
            'modeles_ok': modeles_ok if isinstance(modeles_ok, bool) else None, 'en_attente': attente}


def sante(donnees: Path, base: Path) -> dict:
    """Volumes et place disponible ; l'état de l'IA est ajouté par la couche HTTP."""
    with ouvrir(base) as c:
        comptes = dict(c.execute(
            'SELECT (SELECT COUNT(*) FROM documents) AS documents, (SELECT COUNT(*) FROM spaces) AS espaces, '
            '(SELECT COUNT(*) FROM conversations) AS conversations, '
            '(SELECT COUNT(*) FROM assistants WHERE actif=1) AS assistants_actifs').fetchone())
        version = c.execute('PRAGMA user_version').fetchone()[0]
    base_octets = sum(os.path.getsize(f) for f in (base, Path(str(base) + '-wal')) if os.path.exists(f))
    _, documents_octets = _taille(donnees / 'documents')
    arretes, arretes_octets = _taille(donnees / 'arretes', '.docx')
    disque = shutil.disk_usage(donnees)
    return {'comptes': comptes | {'projets_arretes': arretes},
            'octets': {'base': base_octets, 'documents': documents_octets, 'arretes': arretes_octets},
            'disque': {'libre': disque.free, 'total': disque.total}, 'version_base': version,
            'transcription': _transcription(donnees, base)}
