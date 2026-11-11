# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Comptes, sessions et droits d'accès.

Tant qu'aucun compte n'existe, Territoria fonctionne en mode « un poste » : chaque requête est faite par un utilisateur
implicite, administrateur, propriétaire de tout (`IMPLICITE`). Le premier compte créé est un administrateur, qui reçoit
les contenus existants ; dès lors, chaque requête doit porter une session ouverte par identifiant et mot de passe.

Règles tenues ici, et contrôlées par le serveur (masquer un bouton ne suffit jamais) :
- un espace est lisible par son propriétaire, par ceux avec qui il est partagé (personne, service ou tout le monde) et,
  pour l'espace « Fonds publics », par tous ; l'administrateur n'a aucun accès d'office aux espaces des autres ;
- une conversation n'est visible que de son propriétaire, administrateur compris ;
- un lecteur ne dépasse jamais le niveau « lecture ».
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base.connexion import ouvrir

ROLES = {'administrateur': 'Administrateur', 'referent': 'Référent juridique', 'agent': 'Agent', 'lecteur': 'Lecteur'}
NIVEAUX = ('lecture', 'contribution', 'edition', 'proprietaire')  # ordre croissant
NIVEAUX_PARTAGE = {'espace': ('lecture', 'contribution', 'edition'), 'assistant': ('lecture',)}
CIBLES = ('tous', 'service', 'utilisateur')
ESPACE_PUBLIC = 'public'
LONGUEUR_MIN = 12
ECHECS_MAX = 5
BLOCAGE = timedelta(minutes=15)
INACTIVITE = timedelta(hours=4)
DUREE_MAX = timedelta(hours=12)
COOKIE = 'territoria_session'
# Coût scrypt : 32 Mio par calcul, raisonnable sur un poste modeste où plusieurs connexions peuvent coïncider.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
IDENTIFIANT = re.compile(r'[a-z0-9][a-z0-9._-]{1,63}')
MOTS = ('abeille', 'amande', 'arbre', 'balcon', 'bateau', 'bouleau', 'cabane', 'canal', 'castor', 'cerise', 'chemin',
        'citron', 'colline', 'corail', 'dune', 'ecluse', 'erable', 'etang', 'falaise', 'figue', 'fleuve', 'foret',
        'fougere', 'galet', 'girafe', 'glacier', 'grenier', 'hamac', 'heron', 'jardin', 'lagune', 'lanterne',
        'lavande', 'lierre', 'loutre', 'marche', 'menthe', 'meunier', 'moulin', 'muguet', 'navire', 'noisette',
        'olivier', 'orage', 'palmier', 'panier', 'phare', 'pigeon', 'plage', 'pommier', 'prairie', 'quai', 'radeau',
        'renard', 'rocher', 'roseau', 'sapin', 'sentier', 'source', 'tilleul', 'tulipe', 'vallon', 'verger', 'voile')
IMPLICITE = {'id': '', 'identifiant': '', 'nom': 'Votre espace', 'role': 'administrateur', 'service_id': None,
             'service': '', 'doit_changer': False, 'implicite': True}


class RefusConnexion(Exception):
    """Connexion refusée ; le message est montré tel quel et ne dit pas si l'identifiant existe."""


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _horodatage(moment: datetime) -> str:
    return moment.isoformat(timespec='seconds')


def _b64(octets: bytes) -> str:
    return base64.b64encode(octets).decode('ascii')


def _scrypt(mot_de_passe: str, sel: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(mot_de_passe.encode('utf-8'), salt=sel, n=n, r=r, p=p, maxmem=128 * r * n + 32 * 1024 * 1024, dklen=32)


def empreinte_mot_de_passe(mot_de_passe: str) -> str:
    sel = os.urandom(16)
    return f'scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(sel)}${_b64(_scrypt(mot_de_passe, sel, SCRYPT_N, SCRYPT_R, SCRYPT_P))}'


def verifier_mot_de_passe(mot_de_passe: str, empreinte: str) -> bool:
    try:
        nom, n, r, p, sel, attendu = empreinte.split('$')
        if nom != 'scrypt':
            return False
        calcule = _scrypt(mot_de_passe, base64.b64decode(sel), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(calcule, base64.b64decode(attendu))


_FACTICE = []


def _empreinte_factice() -> str:
    """Empreinte calculée pour un identifiant inconnu : la réponse prend le même temps qu'un mauvais mot de passe."""
    if not _FACTICE:
        _FACTICE.append(empreinte_mot_de_passe(secrets.token_urlsafe(16)))
    return _FACTICE[0]


def valider_mot_de_passe(mot_de_passe) -> str:
    if not isinstance(mot_de_passe, str) or len(mot_de_passe) < LONGUEUR_MIN:
        raise ValueError(f'Le mot de passe doit compter au moins {LONGUEUR_MIN} caractères.')
    if len(mot_de_passe) > 256 or any(ord(x) < 32 for x in mot_de_passe):
        raise ValueError('Mot de passe invalide.')
    return mot_de_passe


def mot_de_passe_provisoire() -> str:
    """Quatre mots et deux chiffres, faciles à dicter ; à changer à la première connexion."""
    return '-'.join(secrets.choice(MOTS) for _ in range(4)) + '-' + str(secrets.randbelow(90) + 10)


IDENTIFIANT_RESERVE = 'Les identifiants qui commencent par « visiteur- » sont réservés aux essais de la démonstration.'


def _identifiant(valeur) -> str:
    texte = valeur.strip().lower() if isinstance(valeur, str) else ''
    if not IDENTIFIANT.fullmatch(texte):
        raise ValueError('Identifiant invalide : 2 à 64 caractères, lettres sans accent, chiffres, point, tiret ou tiret bas.')
    if texte.startswith('visiteur-'):  # en démonstration, un tel compte serait plafonné, puis effacé au bout de 24 heures
        raise ValueError(IDENTIFIANT_RESERVE)
    return texte


def _nom(valeur, message='Indiquez le nom.') -> str:
    texte = valeur.strip() if isinstance(valeur, str) else ''
    if not texte or len(texte) > 100 or any(ord(x) < 32 for x in texte):
        raise ValueError(message)
    return texte


def _role(valeur) -> str:
    if valeur not in ROLES:
        raise ValueError('Rôle invalide.')
    return valeur


# --- Utilisateurs -------------------------------------------------------------------------------------------------

def mode_equipe(base: Path) -> bool:
    with ouvrir(base) as c:
        return c.execute('SELECT 1 FROM utilisateurs LIMIT 1').fetchone() is not None


def _service(c, service_id):
    if service_id in (None, ''):
        return None
    if not isinstance(service_id, str) or not c.execute('SELECT 1 FROM services WHERE id=?', (service_id,)).fetchone():
        raise ValueError('Service introuvable.')
    return service_id


def _public(ligne) -> dict:
    maintenant = _horodatage(_maintenant())
    return {'id': ligne['id'], 'identifiant': ligne['identifiant'], 'nom': ligne['nom'], 'role': ligne['role'],
            'service_id': ligne['service_id'], 'service': ligne['service'] or '', 'actif': bool(ligne['actif']),
            'doit_changer': bool(ligne['doit_changer']), 'bloque': ligne['bloque_jusqu'] > maintenant,
            'derniere_connexion': ligne['derniere_connexion']}


SELECTION = ('SELECT u.*, s.nom AS service FROM utilisateurs u LEFT JOIN services s ON s.id = u.service_id')


def utilisateur(base: Path, ident: str) -> dict | None:
    with ouvrir(base) as c:
        ligne = c.execute(SELECTION + ' WHERE u.id=?', (ident,)).fetchone()
    return _public(ligne) if ligne else None


def utilisateurs(base: Path) -> list[dict]:
    with ouvrir(base) as c:
        return [_public(ligne) for ligne in c.execute(SELECTION + ' ORDER BY u.actif DESC, u.nom COLLATE NOCASE')]


def creer_premier_administrateur(base: Path, donnees) -> dict:
    """Premier compte : un administrateur qui choisit son mot de passe et reçoit les contenus du mode « un poste »."""
    if not isinstance(donnees, dict):
        raise ValueError('Compte invalide.')
    identifiant, nom = _identifiant(donnees.get('identifiant')), _nom(donnees.get('nom'))
    empreinte = empreinte_mot_de_passe(valider_mot_de_passe(donnees.get('mot_de_passe')))
    ident = uuid.uuid4().hex
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM utilisateurs LIMIT 1').fetchone():
            raise PermissionError('Un premier compte existe déjà.')
        c.execute('INSERT INTO utilisateurs(id, identifiant, nom, role, empreinte, doit_changer, cree) VALUES (?,?,?,?,?,0,?)',
                  (ident, identifiant, nom, 'administrateur', empreinte, _horodatage(_maintenant())))
        c.execute("UPDATE spaces SET proprietaire=? WHERE proprietaire='' AND id!=?", (ident, ESPACE_PUBLIC))
        c.execute("UPDATE conversations SET proprietaire=? WHERE proprietaire=''", (ident,))
        c.execute("UPDATE assistants SET proprietaire=? WHERE proprietaire='' AND custom!=0", (ident,))
        c.execute("UPDATE seances SET proprietaire=? WHERE proprietaire=''", (ident,))  # séances du mode « un poste »
    return utilisateur(base, ident)


def creer_utilisateur(base: Path, donnees) -> tuple[dict, str]:
    """Rend le compte et son mot de passe provisoire, montré une seule fois à l'administrateur."""
    if not isinstance(donnees, dict):
        raise ValueError('Compte invalide.')
    identifiant, nom, role = _identifiant(donnees.get('identifiant')), _nom(donnees.get('nom')), _role(donnees.get('role'))
    provisoire = mot_de_passe_provisoire()
    ident = uuid.uuid4().hex
    with ouvrir(base) as c:
        service = _service(c, donnees.get('service_id'))
        if c.execute('SELECT 1 FROM utilisateurs WHERE identifiant=?', (identifiant,)).fetchone():
            raise ValueError('Cet identifiant est déjà utilisé.')
        c.execute('INSERT INTO utilisateurs(id, identifiant, nom, role, service_id, empreinte, cree) VALUES (?,?,?,?,?,?,?)',
                  (ident, identifiant, nom, role, service, empreinte_mot_de_passe(provisoire), _horodatage(_maintenant())))
    return utilisateur(base, ident), provisoire


def _autres_administrateurs(c, ident) -> bool:
    return c.execute("SELECT 1 FROM utilisateurs WHERE role='administrateur' AND actif=1 AND id!=?", (ident,)).fetchone() is not None


def modifier_utilisateur(base: Path, ident: str, donnees) -> dict:
    if not isinstance(donnees, dict):
        raise ValueError('Compte invalide.')
    with ouvrir(base) as c:
        ligne = c.execute('SELECT * FROM utilisateurs WHERE id=?', (ident,)).fetchone()
        if not ligne:
            raise LookupError(ident)
        nom = _nom(donnees['nom']) if 'nom' in donnees else ligne['nom']
        role = _role(donnees['role']) if 'role' in donnees else ligne['role']
        service = _service(c, donnees['service_id']) if 'service_id' in donnees else ligne['service_id']
        actif = ligne['actif']
        if 'actif' in donnees:
            if not isinstance(donnees['actif'], bool):
                raise ValueError('Compte invalide.')
            actif = int(donnees['actif'])
        perd_l_administration = ligne['role'] == 'administrateur' and ligne['actif'] and (role != 'administrateur' or not actif)
        if perd_l_administration and not _autres_administrateurs(c, ident):
            raise PermissionError('Il doit rester au moins un administrateur actif.')
        c.execute('UPDATE utilisateurs SET nom=?, role=?, service_id=?, actif=? WHERE id=?', (nom, role, service, actif, ident))
        if not actif:
            c.execute('DELETE FROM sessions WHERE utilisateur_id=?', (ident,))
    return utilisateur(base, ident)


def reinitialiser_mot_de_passe(base: Path, ident: str) -> str:
    provisoire = mot_de_passe_provisoire()
    with ouvrir(base) as c:
        if not c.execute("UPDATE utilisateurs SET empreinte=?, doit_changer=1, echecs=0, bloque_jusqu='' WHERE id=?",
                         (empreinte_mot_de_passe(provisoire), ident)).rowcount:
            raise LookupError(ident)
        c.execute('DELETE FROM sessions WHERE utilisateur_id=?', (ident,))
    return provisoire


def definir_mot_de_passe(base: Path, identifiant: str, mot_de_passe: str) -> None:
    """Commande de secours : mot de passe choisi par l'exploitant, compte réactivé et débloqué."""
    empreinte = empreinte_mot_de_passe(valider_mot_de_passe(mot_de_passe))
    with ouvrir(base) as c:
        if not c.execute("UPDATE utilisateurs SET empreinte=?, doit_changer=0, echecs=0, bloque_jusqu='', actif=1 WHERE identifiant=?",
                         (empreinte, identifiant.strip().lower())).rowcount:
            raise LookupError(identifiant)


def changer_mot_de_passe(base: Path, ident: str, ancien, nouveau) -> None:
    nouveau = valider_mot_de_passe(nouveau)
    with ouvrir(base) as c:
        ligne = c.execute('SELECT empreinte FROM utilisateurs WHERE id=?', (ident,)).fetchone()
        if not ligne:
            raise LookupError(ident)
        if not isinstance(ancien, str) or not verifier_mot_de_passe(ancien, ligne['empreinte']):
            raise ValueError('Mot de passe actuel incorrect.')
        if nouveau == ancien:
            raise ValueError('Choisissez un mot de passe différent de l’actuel.')
        c.execute('UPDATE utilisateurs SET empreinte=?, doit_changer=0 WHERE id=?', (empreinte_mot_de_passe(nouveau), ident))


def transferer(base: Path, depuis: str, vers: str) -> dict:
    """Confie les espaces et les assistants personnels d'un agent qui part à un autre compte actif."""
    with ouvrir(base) as c:
        if depuis == vers or not c.execute('SELECT 1 FROM utilisateurs WHERE id=?', (depuis,)).fetchone():
            raise LookupError(depuis)
        if not c.execute('SELECT 1 FROM utilisateurs WHERE id=? AND actif=1', (vers,)).fetchone():
            raise ValueError('Choisissez un compte actif pour recevoir les contenus.')
        espaces = c.execute('UPDATE spaces SET proprietaire=? WHERE proprietaire=?', (vers, depuis)).rowcount
        assistants = c.execute('UPDATE assistants SET proprietaire=? WHERE proprietaire=?', (vers, depuis)).rowcount
    return {'espaces': espaces, 'assistants': assistants}


def annuaire(base: Path) -> dict:
    """Ce que chacun voit pour partager : les noms des comptes actifs et des services, rien d'autre."""
    with ouvrir(base) as c:
        return {'utilisateurs': [dict(x) for x in c.execute('SELECT id, nom FROM utilisateurs WHERE actif=1 ORDER BY nom COLLATE NOCASE')],
                'services': [dict(x) for x in c.execute('SELECT id, nom FROM services ORDER BY nom COLLATE NOCASE')]}


# --- Services -----------------------------------------------------------------------------------------------------

def services(base: Path) -> list[dict]:
    with ouvrir(base) as c:
        return [dict(x) for x in c.execute(
            'SELECT s.id, s.nom, COUNT(u.id) AS membres FROM services s LEFT JOIN utilisateurs u ON u.service_id=s.id AND u.actif=1 '
            'GROUP BY s.id ORDER BY s.nom COLLATE NOCASE')]


def enregistrer_service(base: Path, ident: str | None, donnees) -> dict:
    nom = _nom(donnees.get('nom') if isinstance(donnees, dict) else None, 'Indiquez le nom du service.')
    with ouvrir(base) as c:
        doublon = c.execute('SELECT id FROM services WHERE nom=?', (nom,)).fetchone()
        if doublon and doublon['id'] != ident:
            raise ValueError('Ce service existe déjà.')
        if ident is None:
            ident = uuid.uuid4().hex
            c.execute('INSERT INTO services(id, nom, cree) VALUES (?,?,?)', (ident, nom, _horodatage(_maintenant())))
        elif not c.execute('UPDATE services SET nom=? WHERE id=?', (nom, ident)).rowcount:
            raise LookupError(ident)
    return {'id': ident, 'nom': nom}


def supprimer_service(base: Path, ident: str) -> None:
    with ouvrir(base) as c:
        c.execute('UPDATE utilisateurs SET service_id=NULL WHERE service_id=?', (ident,))
        c.execute("DELETE FROM partages WHERE cible='service' AND cible_id=?", (ident,))
        if not c.execute('DELETE FROM services WHERE id=?', (ident,)).rowcount:
            raise LookupError(ident)


# --- Connexion et sessions ----------------------------------------------------------------------------------------

def _empreinte_jeton(jeton: str) -> str:
    return hashlib.sha256(jeton.encode('utf-8', 'surrogatepass')).hexdigest()


def connecter(base: Path, identifiant, mot_de_passe, maintenant: datetime | None = None) -> tuple[dict, str]:
    """Rend le compte et le jeton de session à poser en cookie ; 5 échecs bloquent le compte 15 minutes."""
    maintenant = maintenant or _maintenant()
    refus = 'Identifiant ou mot de passe incorrect.'
    if not isinstance(identifiant, str) or not isinstance(mot_de_passe, str) or len(mot_de_passe) > 256:
        raise RefusConnexion(refus)
    with ouvrir(base) as c:
        ligne = c.execute('SELECT * FROM utilisateurs WHERE identifiant=?', (identifiant.strip().lower(),)).fetchone()
        if not ligne or not ligne['actif']:
            verifier_mot_de_passe(mot_de_passe, _empreinte_factice())
            raise RefusConnexion(refus)
        if ligne['bloque_jusqu'] > _horodatage(maintenant):
            raise RefusConnexion('Trop d’essais : ce compte est bloqué un quart d’heure. Votre administrateur peut le débloquer.')
        if not verifier_mot_de_passe(mot_de_passe, ligne['empreinte']):
            echecs = ligne['echecs'] + 1
            bloque = _horodatage(maintenant + BLOCAGE) if echecs >= ECHECS_MAX else ''
            c.execute('UPDATE utilisateurs SET echecs=?, bloque_jusqu=? WHERE id=?', (0 if bloque else echecs, bloque, ligne['id']))
            c.commit()
            raise RefusConnexion(refus)
        jeton = secrets.token_urlsafe(32)
        moment = _horodatage(maintenant)
        c.execute("UPDATE utilisateurs SET echecs=0, bloque_jusqu='', derniere_connexion=? WHERE id=?", (moment, ligne['id']))
        c.execute('INSERT INTO sessions(empreinte, utilisateur_id, cree, activite) VALUES (?,?,?,?)',
                  (_empreinte_jeton(jeton), ligne['id'], moment, moment))
    return utilisateur(base, ligne['id']), jeton


VISITEURS_PAR_HEURE = 30  # démonstration : comptes « visiteur » créés au plus par heure, pour ménager les crédits de l'IA
MESSAGE_VISITEURS = 'Beaucoup de visiteurs en ce moment : réessayez dans une heure, ou demandez un compte.'


def creer_visiteur(base: Path) -> tuple[dict, str]:
    """Démonstration : un compte « visiteur » propre à chaque essai (rôle agent, aucun mot de passe connu) et sa session.

    Un compte par visiteur : aucun ne voit les conversations ni les espaces d'un autre."""
    maintenant = _maintenant()
    ident = uuid.uuid4().hex
    depuis, moment = _horodatage(maintenant - timedelta(hours=1)), _horodatage(maintenant)
    empreinte = empreinte_mot_de_passe(secrets.token_urlsafe(32))
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')  # le plafond et la création d'un seul tenant : deux arrivées ensemble ne passent pas toutes les deux
        recents = c.execute("SELECT COUNT(*) FROM utilisateurs WHERE identifiant LIKE 'visiteur-%' AND cree>?", (depuis,)).fetchone()[0]
        if recents >= VISITEURS_PAR_HEURE:
            raise PermissionError(MESSAGE_VISITEURS)
        c.execute('INSERT INTO utilisateurs(id, identifiant, nom, role, empreinte, doit_changer, cree) VALUES (?,?,?,?,?,0,?)',
                  (ident, 'visiteur-' + secrets.token_hex(4), 'Visiteur', 'agent', empreinte, moment))
    return utilisateur(base, ident), ouvrir_session(base, ident)


def visiteurs_crees_avant(base: Path, moment: datetime) -> list[str]:
    with ouvrir(base) as c:
        return [x['id'] for x in c.execute("SELECT id FROM utilisateurs WHERE identifiant LIKE 'visiteur-%' AND cree<?", (_horodatage(moment),))]


def supprimer_visiteur(base: Path, ident: str) -> None:
    """Le compte d'un visiteur, ses sessions et les partages qui le visaient ; ses contenus sont effacés avant (server.py)."""
    with ouvrir(base) as c:
        if not c.execute("SELECT 1 FROM utilisateurs WHERE id=? AND identifiant LIKE 'visiteur-%'", (ident,)).fetchone():
            return  # déjà effacé par un autre processus, ou pas un visiteur : rien à faire
        c.execute('DELETE FROM sessions WHERE utilisateur_id=?', (ident,))
        c.execute("DELETE FROM partages WHERE cible='utilisateur' AND cible_id=?", (ident,))
        c.execute('DELETE FROM utilisateurs WHERE id=?', (ident,))


ESSAIS_PAR_VISITEUR = 15  # demandes à l'IA d'un même visiteur
ESSAIS_VISITEURS_PAR_JOUR = 300  # demandes à l'IA de l'ensemble des visiteurs sur 24 heures
MESSAGE_ESSAIS_VISITEUR = ("Limite de l'essai atteinte (15 demandes à l'IA). Pour continuer, installez TerritorIA chez vous : "
                           'https://territoria.ovh')
MESSAGE_ESSAIS_JOUR = ("La démonstration a atteint sa limite du jour. Revenez demain, ou installez TerritorIA chez vous : "
                       'https://territoria.ovh')


def compter_essai_ia(base: Path, utilisateur_id: str) -> None:
    """Démonstration : note une demande à l'IA d'un visiteur ; refusée (PermissionError) au-delà des plafonds, pour qu'aucun
    visiteur ne consomme les crédits d'IA de l'administrateur."""
    maintenant = _maintenant()
    depuis, moment = _horodatage(maintenant - timedelta(days=1)), _horodatage(maintenant)
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')  # les plafonds et le compte d'un seul tenant : deux demandes arrivées ensemble ne passent pas toutes les deux
        if c.execute('SELECT COUNT(*) FROM essais_ia WHERE utilisateur_id=?', (utilisateur_id,)).fetchone()[0] >= ESSAIS_PAR_VISITEUR:
            raise PermissionError(MESSAGE_ESSAIS_VISITEUR)
        if c.execute('SELECT COUNT(*) FROM essais_ia WHERE moment>?', (depuis,)).fetchone()[0] >= ESSAIS_VISITEURS_PAR_JOUR:
            raise PermissionError(MESSAGE_ESSAIS_JOUR)
        c.execute('INSERT INTO essais_ia(utilisateur_id, moment) VALUES (?,?)', (utilisateur_id, moment))


def purger_essais_ia(base: Path, maintenant: datetime | None = None) -> None:
    """Une demande à l'IA comptée depuis plus de 24 heures ne sert plus à aucun plafond : son visiteur est déjà effacé."""
    with ouvrir(base) as c:
        c.execute('DELETE FROM essais_ia WHERE moment<?', (_horodatage((maintenant or _maintenant()) - timedelta(days=1)),))


def ouvrir_session(base: Path, ident: str) -> str:
    """Session ouverte sans mot de passe, juste après la création du premier compte par son titulaire."""
    jeton, moment = secrets.token_urlsafe(32), _horodatage(_maintenant())
    with ouvrir(base) as c:
        c.execute('INSERT INTO sessions(empreinte, utilisateur_id, cree, activite) VALUES (?,?,?,?)',
                  (_empreinte_jeton(jeton), ident, moment, moment))
        c.execute('UPDATE utilisateurs SET derniere_connexion=? WHERE id=?', (moment, ident))
    return jeton


# Compte de la vérification automatique (`outils verifier`, lancée par les mises à jour) : créé seulement si des comptes
# existent déjà, supprimé aussitôt après. Son identifiant est fixe, pour que l'espace d'essai soit réutilisé d'une fois sur l'autre.
TECHNIQUE = 'verification-automatique'


def ouvrir_compte_technique(base: Path) -> str:
    with ouvrir(base) as c:
        c.execute('INSERT OR REPLACE INTO utilisateurs(id, identifiant, nom, role, empreinte, doit_changer, cree) VALUES (?,?,?,?,?,0,?)',
                  (TECHNIQUE, TECHNIQUE, 'Vérification automatique', 'administrateur',
                   empreinte_mot_de_passe(secrets.token_urlsafe(32)), _horodatage(_maintenant())))
    return ouvrir_session(base, TECHNIQUE)


def fermer_compte_technique(base: Path) -> None:
    with ouvrir(base) as c:
        c.execute('DELETE FROM sessions WHERE utilisateur_id=?', (TECHNIQUE,))
        c.execute('DELETE FROM utilisateurs WHERE id=?', (TECHNIQUE,))


def session(base: Path, jeton: str, maintenant: datetime | None = None) -> dict | None:
    """Compte de la session, ou None (absente, expirée après 4 h d'inactivité ou 12 h en tout, compte désactivé)."""
    if not jeton or len(jeton) > 200:
        return None
    maintenant = maintenant or _maintenant()
    empreinte = _empreinte_jeton(jeton)
    with ouvrir(base) as c:
        ligne = c.execute('SELECT s.*, u.actif FROM sessions s JOIN utilisateurs u ON u.id=s.utilisateur_id WHERE s.empreinte=?',
                          (empreinte,)).fetchone()
        if not ligne:
            return None
        expiree = (ligne['activite'] < _horodatage(maintenant - INACTIVITE) or ligne['cree'] < _horodatage(maintenant - DUREE_MAX)
                   or not ligne['actif'])
        if expiree:
            c.execute('DELETE FROM sessions WHERE empreinte=?', (empreinte,))
            return None
        if ligne['activite'] < _horodatage(maintenant - timedelta(minutes=1)):  # une écriture par minute au plus
            c.execute('UPDATE sessions SET activite=? WHERE empreinte=?', (_horodatage(maintenant), empreinte))
    return utilisateur(base, ligne['utilisateur_id'])


def deconnecter(base: Path, jeton: str) -> None:
    with ouvrir(base) as c:
        c.execute('DELETE FROM sessions WHERE empreinte=?', (_empreinte_jeton(jeton or ''),))


def purger_sessions(base: Path, maintenant: datetime | None = None) -> None:
    maintenant = maintenant or _maintenant()
    with ouvrir(base) as c:
        c.execute('DELETE FROM sessions WHERE activite<? OR cree<?',
                  (_horodatage(maintenant - INACTIVITE), _horodatage(maintenant - DUREE_MAX)))


# --- Droits -------------------------------------------------------------------------------------------------------

def rang(niveau: str | None) -> int:
    return NIVEAUX.index(niveau) if niveau in NIVEAUX else -1


def _cibles(compte) -> tuple[str, str]:
    return compte['id'], compte.get('service_id') or ''


def _partages_recus(c, ressource, compte):
    uid, service = _cibles(compte)
    return c.execute("SELECT ressource_id, niveau FROM partages WHERE ressource=? AND (cible='tous' "
                     "OR (cible='service' AND cible_id=? AND ?!='') OR (cible='utilisateur' AND cible_id=?))",
                     (ressource, service, service, uid)).fetchall()


def niveaux_espaces(base: Path, compte: dict) -> dict[str, str]:
    """Niveau du compte sur chaque espace qu'il peut au moins lire (les autres sont absents du résultat)."""
    with ouvrir(base) as c:
        espaces = c.execute('SELECT id, proprietaire FROM spaces').fetchall()
        recus = _partages_recus(c, 'espace', compte) if not compte.get('implicite') else []
    if compte.get('implicite'):
        return {e['id']: 'proprietaire' for e in espaces}
    niveaux = {}
    for e in espaces:
        if e['id'] == ESPACE_PUBLIC:
            niveaux[e['id']] = 'edition' if compte['role'] in ('administrateur', 'referent') else 'lecture'
        if e['proprietaire'] == compte['id']:
            niveaux[e['id']] = 'proprietaire'
    existants = {e['id'] for e in espaces}
    for r in recus:
        if r['ressource_id'] in existants and rang(r['niveau']) > rang(niveaux.get(r['ressource_id'])):
            niveaux[r['ressource_id']] = r['niveau']
    if compte['role'] == 'lecteur':
        niveaux = {ident: 'lecture' for ident in niveaux}
    return niveaux


def exiger_espace(base: Path, compte: dict, espace: str, niveau: str) -> str:
    """Rend le niveau du compte ; un espace invisible est « introuvable », un niveau insuffisant est un refus."""
    actuel = niveaux_espaces(base, compte).get(espace)
    if actuel is None:
        raise LookupError(espace)
    if rang(actuel) < rang(niveau):
        raise PermissionError('Vos droits sur cet espace ne permettent pas cette action.')
    return actuel


def assistants_visibles(base: Path, compte: dict) -> set[str]:
    """Assistants que le compte peut utiliser : ceux du logiciel s'ils sont actifs, ses assistants et ceux partagés."""
    with ouvrir(base) as c:
        lignes = c.execute('SELECT id, custom, proprietaire FROM assistants WHERE actif=1').fetchall()
        recus = set() if compte.get('implicite') else {r['ressource_id'] for r in _partages_recus(c, 'assistant', compte)}
    return {a['id'] for a in lignes if not a['custom'] or compte.get('implicite') or a['proprietaire'] == compte['id'] or a['id'] in recus}


def proprietaire_conversation(base: Path, conversation: str) -> str | None:
    with ouvrir(base) as c:
        ligne = c.execute('SELECT proprietaire FROM conversations WHERE id=?', (conversation,)).fetchone()
    return ligne['proprietaire'] if ligne else None


def projet_de(base: Path, projet: str, utilisateur: str) -> bool:
    """Vrai si le projet d'arrêté vient d'une conversation de ce compte (l'identifiant est lu dans l'artefact, jamais dans
    un texte saisi qui pourrait le reprendre)."""
    with ouvrir(base) as c:
        return c.execute("SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE m.role='assistant' "
                         "AND json_valid(m.artifact) AND json_extract(m.artifact, '$.kind') IN ('arrete','deliberation','arrete_rh','arrete_police','arrete_delegation') "
                         "AND json_extract(m.artifact, '$.id')=? AND c.proprietaire=? LIMIT 1", (projet, utilisateur)).fetchone() is not None


# --- Partages -----------------------------------------------------------------------------------------------------

def _peut_partager(c, base, compte, ressource, ressource_id):
    if ressource == 'espace':
        if ressource_id == ESPACE_PUBLIC:
            raise PermissionError('Les fonds publics sont déjà lisibles par tous.')
        exiger_espace(base, compte, ressource_id, 'proprietaire')
    elif ressource == 'assistant':
        ligne = c.execute('SELECT custom, proprietaire FROM assistants WHERE id=?', (ressource_id,)).fetchone()
        if not ligne or not ligne['custom']:
            raise LookupError(ressource_id)
        if not compte.get('implicite') and ligne['proprietaire'] != compte['id']:
            raise PermissionError('Seul le propriétaire de cet assistant peut le partager.')
    else:
        raise ValueError('Ressource invalide.')


def partages(base: Path, compte: dict, ressource: str, ressource_id: str) -> list[dict]:
    with ouvrir(base) as c:
        _peut_partager(c, base, compte, ressource, ressource_id)
        lignes = c.execute(
            "SELECT p.cible, p.cible_id, p.niveau, CASE p.cible WHEN 'tous' THEN 'Tout le monde' "
            "WHEN 'service' THEN (SELECT 'Service ' || nom FROM services WHERE id=p.cible_id) "
            "ELSE (SELECT nom FROM utilisateurs WHERE id=p.cible_id) END AS libelle "
            'FROM partages p WHERE p.ressource=? AND p.ressource_id=? ORDER BY p.cible, libelle', (ressource, ressource_id)).fetchall()
    return [dict(x) for x in lignes]


def partager(base: Path, compte: dict, ressource: str, ressource_id: str, donnees) -> None:
    if not isinstance(donnees, dict):
        raise ValueError('Partage invalide.')
    cible, cible_id, niveau = donnees.get('cible'), donnees.get('cible_id') or '', donnees.get('niveau')
    if cible not in CIBLES or niveau not in NIVEAUX_PARTAGE.get(ressource, ()):
        raise ValueError('Partage invalide.')
    with ouvrir(base) as c:
        _peut_partager(c, base, compte, ressource, ressource_id)
        if cible == 'tous':
            cible_id = ''
        elif cible == 'service' and not c.execute('SELECT 1 FROM services WHERE id=?', (cible_id,)).fetchone():
            raise ValueError('Service introuvable.')
        elif cible == 'utilisateur' and not c.execute('SELECT 1 FROM utilisateurs WHERE id=? AND actif=1', (cible_id,)).fetchone():
            raise ValueError('Compte introuvable.')
        c.execute('INSERT INTO partages(ressource, ressource_id, cible, cible_id, niveau) VALUES (?,?,?,?,?) '
                  'ON CONFLICT(ressource, ressource_id, cible, cible_id) DO UPDATE SET niveau=excluded.niveau',
                  (ressource, ressource_id, cible, cible_id, niveau))


def retirer_partage(base: Path, compte: dict, ressource: str, ressource_id: str, donnees) -> None:
    if not isinstance(donnees, dict) or donnees.get('cible') not in CIBLES:
        raise ValueError('Partage invalide.')
    with ouvrir(base) as c:
        _peut_partager(c, base, compte, ressource, ressource_id)
        c.execute('DELETE FROM partages WHERE ressource=? AND ressource_id=? AND cible=? AND cible_id=?',
                  (ressource, ressource_id, donnees['cible'], donnees.get('cible_id') or ''))
