# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Retours des agents sur les réponses : note 👍/👎 et signalement volontaire aux référents juridiques.

La note reste dans la conversation de l'agent (et, sans contenu, dans le journal d'activité pour les statistiques).
Le signalement copie la question, la réponse et les titres des sources, jamais le texte des documents cités : un
référent n'a pas forcément accès aux espaces de l'agent. Le nom de l'agent n'y figure que s'il le demande.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base.connexion import ouvrir

CONSERVATION_JOURS = 365
COMMENTAIRE_MAX = 1000
STATUTS = ('nouveau', 'traite')


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _horodatage(moment: datetime) -> str:
    return moment.isoformat(timespec='seconds')


def _reponse_de(c, message_id, utilisateur):
    """La réponse (message de l'assistant) d'une conversation du compte ; « introuvable » sinon."""
    ligne = c.execute('SELECT m.id, m.conversation_id, m.text, m.sources, c.mode, c.assistant_id FROM messages m '
                      "JOIN conversations c ON c.id=m.conversation_id WHERE m.id=? AND m.role='assistant' AND c.proprietaire=?",
                      (message_id, utilisateur)).fetchone()
    if not ligne:
        raise LookupError(message_id)
    return ligne


def noter(base: Path, message_id: int, utilisateur: str, note) -> int:
    if note not in (-1, 0, 1) or isinstance(note, bool):
        raise ValueError('Note invalide.')
    with ouvrir(base) as c:
        _reponse_de(c, message_id, utilisateur)
        c.execute('UPDATE messages SET note=? WHERE id=?', (note, message_id))
    return note


def signaler(base: Path, message_id: int, compte: dict, donnees) -> str:
    if not isinstance(donnees, dict):
        raise ValueError('Signalement invalide.')
    commentaire = donnees.get('commentaire', '')
    if not isinstance(commentaire, str) or len(commentaire) > COMMENTAIRE_MAX:
        raise ValueError(f'Commentaire trop long ({COMMENTAIRE_MAX} caractères au plus).')
    avec_nom = donnees.get('avec_nom') is True
    ident = uuid.uuid4().hex
    with ouvrir(base) as c:
        reponse = _reponse_de(c, message_id, compte['id'])
        if c.execute('SELECT 1 FROM signalements WHERE message_id=?', (message_id,)).fetchone():
            raise ValueError('Cette réponse a déjà été signalée.')
        question = c.execute("SELECT text FROM messages WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
                             (reponse['conversation_id'], message_id)).fetchone()
        assistant = c.execute('SELECT title FROM assistants WHERE id=?', (reponse['assistant_id'],)).fetchone()
        try:
            sources = [{'title': s.get('title', ''), 'page': s.get('page')} for s in json.loads(reponse['sources']) if isinstance(s, dict)]
        except ValueError:
            sources = []
        c.execute('INSERT INTO signalements(id, message_id, quand, auteur_nom, assistant, mode, question, reponse, sources, commentaire) '
                  'VALUES (?,?,?,?,?,?,?,?,?,?)',
                  (ident, message_id, _horodatage(_maintenant()), compte.get('nom', '') if avec_nom else '',
                   assistant['title'] if assistant else '', reponse['mode'], question['text'] if question else '', reponse['text'],
                   json.dumps(sources, ensure_ascii=False), commentaire.strip()))
    return ident


def signalements(base: Path) -> list[dict]:
    with ouvrir(base) as c:
        lignes = c.execute("SELECT * FROM signalements ORDER BY statut='traite', quand DESC").fetchall()
    return [{k: x[k] for k in x.keys() if k != 'message_id'} | {'sources': json.loads(x['sources'])} for x in lignes]


def changer_statut(base: Path, ident: str, statut) -> None:
    if statut not in STATUTS:
        raise ValueError('Statut invalide.')
    with ouvrir(base) as c:
        if not c.execute('UPDATE signalements SET statut=?, traite_le=? WHERE id=?',
                         (statut, _horodatage(_maintenant()) if statut == 'traite' else '', ident)).rowcount:
            raise LookupError(ident)


def supprimer(base: Path, ident: str) -> None:
    with ouvrir(base) as c:
        c.execute('PRAGMA secure_delete=ON')
        if not c.execute('DELETE FROM signalements WHERE id=?', (ident,)).rowcount:
            raise LookupError(ident)


def purger(base: Path, maintenant: datetime | None = None) -> int:
    limite = _horodatage((maintenant or _maintenant()) - timedelta(days=CONSERVATION_JOURS))
    with ouvrir(base) as c:
        c.execute('PRAGMA secure_delete=ON')
        return c.execute('DELETE FROM signalements WHERE quand<?', (limite,)).rowcount
