# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Actes à publier : un acte du registre ou la version finale déposée, ses passages à masquer, et ses statuts
`a_relire` → `valide` → `publie`. Rien n'est publié sans validation, et un acte publié ne se modifie plus.

Fichiers d'un acte : `<données>/publication/<id>/` (0700) : la source déposée et `texte.json` (paragraphes), en 0600."""
from __future__ import annotations

import json
import os
import re
import shutil
import uuid
import zipfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import anonymisation, nomenclature
from .base.connexion import ouvrir

STATUTS = ('a_relire', 'valide', 'publie')
TYPES = ('arrete', 'deliberation')
SOURCES = ('registre', 'depot')
CATEGORIES = ('nom', 'adresse', 'telephone', 'courriel', 'naissance', 'nir', 'iban', 'immatriculation', 'manuel')
ORIGINES = ('regle', 'ia', 'manuel')
VOTES = ('effectif', 'reel', 'pour', 'contre', 'abstention')
MESSAGE_PUBLIE = 'Cet acte est publié : il ne se modifie plus.'
MESSAGE_RETIRE = 'Cet acte a été retiré de la publication : il ne se modifie plus.'
MOTIF_MAX = 300
MESSAGE_MATIERE = 'Choisissez la matière de la délibération (nomenclature ACTES).'
MESSAGE_PROJET = ('Cet acte contient une mention de projet ou un champ à compléter. '
                  'Déposez le document définitif dans « La version signée ».')
MENTION_PROJET = re.compile(r'\bprojet\s*[—–-]\s*non\s+signé\b', re.I)
_CHAMP_PROJET = re.compile(r'\[(?:[^\[\]\n]{0,80}\bà\s+(?:compléter|préciser|attribuer)\b[^\[\]\n]{0,80}'
                           r'|\s*(?:date(?:\s+de\s+signature)?|nom\s+et\s+signature)\s*)\]'
                           r"|\bsignature à apposer par l[’']autorité compétente\b", re.I)
MESSAGE_WORD = 'Fichier Word trop volumineux ou invalide.'
MESSAGE_GRAPHIQUE = ('Le dossier de publication restitue uniquement le texte. Cette source comporte un élément graphique, '
                     'une annexe ou un contenu que TerritorIA ne peut pas reproduire fidèlement. '
                     'Préparez la copie publique complète avec un outil adapté.')
WORD_ENTREES_MAX = 1000
WORD_TAILLE_MAX = 50 * 1024 * 1024  # décompressé, en tout
WORD_COMPRESSIBLE_MAX = 5 * 1024 * 1024  # décompressé, parties très compressibles : le XML lu en mémoire
TEXTE_MAX = 2_000_000  # caractères d'un acte
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_ACTE = re.compile(r'[0-9a-f]{32}')


def _maintenant() -> str:
    return datetime.now(ZoneInfo('Europe/Paris')).isoformat(timespec='seconds')


def aujourdhui() -> str:
    """Date du jour à Paris (AAAA-MM-JJ), quel que soit le fuseau du serveur."""
    return _maintenant()[:10]


def dossier(donnees_dir: Path, ident: str) -> Path:
    """Dossier privé d'un acte à publier (créé au besoin, 0700)."""
    if not _ACTE.fullmatch(ident):
        raise LookupError(ident)
    chemin = Path(donnees_dir) / 'publication' / ident
    chemin.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    chemin.mkdir(exist_ok=True, mode=0o700)
    os.chmod(chemin, 0o700)
    return chemin


def ecrire_prive(chemin: Path, contenu: bytes) -> None:
    temporaire = chemin.with_name('.' + chemin.name + '.tmp')
    temporaire.write_bytes(contenu)
    os.chmod(temporaire, 0o600)
    os.replace(temporaire, chemin)


def _blocs(element):
    """Texte dans l'ordre du document : paragraphes (insertions suivies comprises, suppressions exclues), tableaux
    (une rangée par bloc, cellules séparées par « | », tableaux imbriqués compris) et contrôles de contenu."""
    for enfant in element:
        if enfant.tag == W + 'p':
            morceaux = []
            for noeud in enfant.iter():
                if noeud.tag == W + 't':
                    morceaux.append(noeud.text or '')
                elif noeud.tag in (W + 'br', W + 'cr', W + 'tab'):
                    morceaux.append(' ')
            yield ''.join(morceaux).strip()
        elif enfant.tag == W + 'tbl':
            for rangee in enfant.findall(W + 'tr'):
                cellules = [' '.join(b for b in _blocs(cellule) if b) for cellule in rangee.findall(W + 'tc')]
                yield ' | '.join(c for c in cellules if c)
        elif enfant.tag == W + 'sdt':
            contenu = enfant.find(W + 'sdtContent')
            if contenu is not None:
                yield from _blocs(contenu)


def verifier_archive_word(source) -> None:
    """Refuse un Word invalide ou démesuré (bombe de décompression) sans le lire ; `source` : un chemin ou un flux d'octets."""
    try:
        with zipfile.ZipFile(source) as archive:
            entrees = archive.infolist()
    except (zipfile.BadZipFile, OSError):
        raise ValueError(MESSAGE_WORD) from None
    # Le XML coûte une vingtaine de fois sa taille à la lecture ; une image (déjà compressée) n'est pas comptée.
    compressible = sum(e.file_size for e in entrees if e.file_size > 2 * e.compress_size)
    if (len(entrees) > WORD_ENTREES_MAX or sum(e.file_size for e in entrees) > WORD_TAILLE_MAX
            or compressible > WORD_COMPRESSIBLE_MAX):
        raise ValueError(MESSAGE_WORD)


def verifier_version(objet: str, paragraphes: list[str], *, fiche_existante: bool = False) -> None:
    """Repère les marques explicites d'un brouillon avant anonymisation."""
    if any(_CHAMP_PROJET.search(bloc) or MENTION_PROJET.fullmatch(bloc.strip()) for bloc in (objet, *paragraphes)):
        precision = ' Retirez d’abord cette fiche de la liste des actes à publier pour réutiliser son numéro.' if fiche_existante else ''
        raise ValueError(MESSAGE_PROJET + precision)


def paragraphes_docx(chemin: Path) -> list[str]:
    """Texte intégral d'un Word, bloc par bloc ; une archive démesurée (bombe de décompression) est refusée avant lecture."""
    verifier_archive_word(chemin)
    from docx import Document
    from docx.parts.hdrftr import FooterPart, HeaderPart
    try:
        document = Document(str(chemin))
    except Exception:
        raise ValueError(MESSAGE_WORD) from None
    # La numérotation automatique héritée d'un style n'est pas dans les fragments de texte.
    styles_numerotes = set()
    for style in document.styles:
        if style.element.find('.//' + W + 'numPr') is not None:
            styles_numerotes.add(style.style_id)
    entetes = [p.element for p in document.part.related_parts.values() if isinstance(p, HeaderPart)]
    pieds = [p.element for p in document.part.related_parts.values() if isinstance(p, FooterPart)]
    blocs = []
    for element in [*entetes, document.element.body, *pieds]:
        for noeud in element.iter():
            if noeud.tag in _WORD_NON_RESTITUE:
                raise ValueError(MESSAGE_GRAPHIQUE)
            if noeud.tag == W + 'pStyle' and noeud.get(W + 'val') in styles_numerotes:
                raise ValueError(MESSAGE_GRAPHIQUE)
        blocs.extend(b for b in _blocs(element) if b)
    if sum(len(b) for b in blocs) > TEXTE_MAX:
        raise ValueError(MESSAGE_WORD)
    return blocs


_WORD_NON_RESTITUE = {W + nom for nom in ('drawing', 'pict', 'object', 'altChunk', 'footnoteReference', 'endnoteReference',
                                          'sym', 'numPr', 'subDoc', 'contentPart')} | {
    '{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath',
    '{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent'}
# Ces opérateurs peignent une forme, une image ou une ressource, ou découpent le rendu visible.
_PDF_NON_RESTITUE = set(b'S s f F f* B B* b b* sh Do W W*'.split()) | {b'INLINE IMAGE'}


def paragraphes_pdf(chemin: Path) -> list[str]:
    """Publication des PDF texte. Une transcription OCR ne peut pas reproduire le périmètre d'un plan."""
    from pypdf import PdfReader
    lecteur = PdfReader(str(chemin))
    if lecteur.is_encrypted:
        raise ValueError('Ce PDF est protégé ; fournissez une version non chiffrée.')
    if lecteur.root_object.get('/AF') or '/EmbeddedFiles' in (lecteur.root_object['/Names'] if '/Names' in lecteur.root_object else {}):
        raise ValueError(MESSAGE_GRAPHIQUE)
    paragraphes, courant = [], []
    for numero, page in enumerate(lecteur.pages, 1):
        contenu = page.get_contents()
        if (page.get('/Annots') or page.get('/AF') or
                (contenu is not None and any(op in _PDF_NON_RESTITUE for _, op in contenu.operations))):
            raise ValueError(f'Page {numero}. {MESSAGE_GRAPHIQUE}')
        texte = page.extract_text() or ''
        if not texte.strip() and contenu is not None and any(op in (b'Tj', b'TJ', b"'", b'"') for _, op in contenu.operations):
            raise ValueError(f'Page {numero}. {MESSAGE_GRAPHIQUE}')
        for ligne in texte.splitlines():
            if MENTION_PROJET.fullmatch(ligne.strip()):
                raise ValueError(MESSAGE_PROJET)
            if ligne.strip():
                courant.append(ligne.strip())
                if ligne.rstrip().endswith(('.', ';', ':')):
                    paragraphes.append(' '.join(courant))
                    courant = []
    return paragraphes + ([' '.join(courant)] if courant else [])


def paragraphes_du_registre(donnees_dir: Path, acte_id: str) -> list[str]:
    """Texte d'un acte rédigé par TerritorIA (son Word `arretes/<acte_id>.docx`)."""
    chemin = Path(donnees_dir) / 'arretes' / f'{acte_id}.docx'
    if not _ACTE.fullmatch(acte_id or '') or not chemin.is_file():
        raise LookupError(acte_id)
    return paragraphes_docx(chemin)


def _nom_fichier(numero: str) -> str:
    return re.sub(r'[^A-Za-z0-9._-]', '-', numero)


def entree_du_registre(base: Path, numero: str) -> dict:
    """Acte numéroté par TerritorIA : type, identifiant du Word, objet et date d'attribution du numéro."""
    with ouvrir(base) as c:
        ligne = c.execute('SELECT numero, type, acte_id, objet, attribue_le FROM registre_actes WHERE numero=?', (numero,)).fetchone()
    if not ligne:
        raise LookupError(numero)
    return dict(ligne)


def _date(valeur) -> str:
    try:
        return date.fromisoformat(str(valeur)).isoformat()
    except ValueError:
        raise ValueError('Date de l’acte invalide : AAAA-MM-JJ attendue.') from None


def _texte(valeur, message: str, longueur: int) -> str:
    texte = ' '.join(str(valeur or '').split())
    if not texte or len(texte) > longueur:
        raise ValueError(message)
    return texte


def _votes(valeur) -> dict:
    votes = {}
    for cle, nombre in (valeur or {}).items():
        if cle not in VOTES or nombre in ('', None):
            continue
        if not isinstance(nombre, int) or isinstance(nombre, bool) or nombre < 0:
            raise ValueError('Les votes sont des nombres entiers positifs.')
        votes[cle] = nombre
    return votes


# Démonstration : les actes d'essai des visiteurs ne partagent ni les numéros ni les listes de l'administration.
_VISITEURS = "SELECT id FROM utilisateurs WHERE identifiant LIKE 'visiteur-%'"


def creer(base: Path, donnees_dir: Path, *, type: str, numero: str, date_acte: str, objet: str, paragraphes: list[str],
          source: str, acte_id: str = '', matiere_code: str = '', votes: dict | None = None, par: str,
          visiteurs_a_part: bool = False, avant_ecriture=None) -> dict:
    """`avant_ecriture(c)` : contrôle rejoué dans la transaction d'écriture (plafond d'un visiteur de la démonstration)."""
    if type not in TYPES:
        raise ValueError('Type d’acte inconnu : arrêté ou délibération.')
    if source not in SOURCES:
        raise ValueError('Source inconnue.')
    if type == 'deliberation':
        if not matiere_code:
            raise ValueError(MESSAGE_MATIERE)
        nomenclature.nom(matiere_code)
    else:
        matiere_code = ''
    paragraphes = [p.strip() for p in paragraphes if isinstance(p, str) and p.strip()]
    if not paragraphes:
        raise ValueError('L’acte ne contient aucun texte.')
    if sum(len(p) for p in paragraphes) > TEXTE_MAX:
        raise ValueError('Acte trop long pour être publié ici.')
    objet = _texte(objet, 'Indiquez l’objet de l’acte.', 300)
    verifier_version(objet, paragraphes)
    numero = _texte(numero, 'Indiquez le numéro de l’acte.', 60)
    date_acte = _date(date_acte)
    ligne = {'id': uuid.uuid4().hex, 'type': type, 'numero': numero,
             'date_acte': date_acte, 'objet': objet, 'source': source,
             'acte_id': acte_id or '', 'matiere_code': matiere_code, 'votes': json.dumps(_votes(votes)),
             'cree_par': par or '', 'cree_le': _maintenant()}
    ecrire_prive(dossier(donnees_dir, ligne['id']) / 'texte.json', json.dumps(paragraphes, ensure_ascii=False).encode('utf-8'))
    try:
        with ouvrir(base) as c:
            c.execute('BEGIN IMMEDIATE')
            if avant_ecriture:
                avant_ecriture(c)
            # Une seule version active par nom de fichier, dans le périmètre du registre concerné.
            requete = 'SELECT id, numero, type, date_acte, statut FROM publications'
            parametres = ()
            if visiteurs_a_part:
                if c.execute(_VISITEURS + ' AND id=?', (par or '',)).fetchone():
                    requete += ' WHERE cree_par=?'
                    parametres = (par,)
                else:
                    requete += f' WHERE cree_par NOT IN ({_VISITEURS})'
            fichier = _nom_fichier(numero).casefold()
            ligne['remplace_id'] = ''
            for precedente in c.execute(requete + ' ORDER BY rowid DESC', parametres):
                if _nom_fichier(precedente['numero']).casefold() != fichier:
                    continue
                if (precedente['statut'] != 'retire' or precedente['numero'] != numero or
                        precedente['type'] != type or precedente['date_acte'] != date_acte):
                    raise ValueError('Ce numéro est déjà dans les actes à publier.')
                if not ligne['remplace_id']:
                    ligne['remplace_id'] = precedente['id']
            c.execute('INSERT INTO publications(id, type, numero, date_acte, objet, source, acte_id, matiere_code, votes, cree_par, '
                      'cree_le, remplace_id) VALUES (:id, :type, :numero, :date_acte, :objet, :source, :acte_id, :matiere_code, :votes, '
                      ':cree_par, :cree_le, :remplace_id)', ligne)
    except Exception:
        shutil.rmtree(dossier(donnees_dir, ligne['id']), ignore_errors=True)  # texte déjà écrit pour un acte qui n'existera pas
        raise
    return lire(base, donnees_dir, ligne['id'])


def _public(ligne) -> dict:
    acte = dict(ligne)
    acte['passages'] = json.loads(acte['passages'])
    acte['votes'] = json.loads(acte['votes'])
    acte['matiere_nom'] = nomenclature.nom(acte['matiere_code']) if acte['matiere_code'] else ''
    return acte


def refuser_si_fige(acte) -> None:
    """Un acte publié ou retiré ne se modifie plus et ne se supprime pas : le PDF et son empreinte restent la preuve."""
    if acte['statut'] == 'publie':
        raise PermissionError(MESSAGE_PUBLIE)
    if acte['statut'] == 'retire':
        raise PermissionError(MESSAGE_RETIRE)


def _ligne(c, ident: str):
    ligne = c.execute('SELECT * FROM publications WHERE id=?', (ident,)).fetchone()
    if not ligne:
        raise LookupError(ident)
    return ligne


def lire(base: Path, donnees_dir: Path, ident: str) -> dict:
    with ouvrir(base) as c:
        acte = _public(_ligne(c, ident))
    acte['paragraphes'] = json.loads((dossier(donnees_dir, ident) / 'texte.json').read_text(encoding='utf-8'))
    return acte


def avec_apercu(acte: dict) -> dict:
    """Aperçu de la fiche de relecture, calculé avec les occurrences du PDF pour chaque bloc."""
    blocs = [acte['objet'], *acte['paragraphes']]
    return acte | {'zones_relecture': [anonymisation.zones_relecture(bloc, acte['passages']) for bloc in blocs]}


def liste(base: Path, proprietaire: str | None = None, visiteurs_a_part: bool = False) -> list[dict]:
    """Les actes d'un propriétaire, ou tous ; en démonstration, « tous » laisse de côté les actes d'essai des visiteurs."""
    with ouvrir(base) as c:
        return [_public(l) for l in _lignes_visibles(c, proprietaire, visiteurs_a_part)]


def _lignes_visibles(c, proprietaire, visiteurs_a_part, *, index=False):
    sans_visiteurs = f' AND cree_par NOT IN ({_VISITEURS})' if visiteurs_a_part and proprietaire is None else ''
    colonnes, statut = ('id, revision', " AND statut='publie'") if index else ('*', '')
    return c.execute(f'SELECT {colonnes} FROM publications WHERE (? IS NULL OR cree_par=?)' + sans_visiteurs + statut +
                     ' ORDER BY cree_le DESC, rowid DESC', (proprietaire, proprietaire))


def _passages(valeur) -> list[dict]:
    if not isinstance(valeur, list) or len(valeur) > 500:
        raise ValueError('Passages invalides.')
    passages = []
    for p in valeur:
        texte = p.get('texte') if isinstance(p, dict) else None
        if (not isinstance(texte, str) or not texte.strip() or len(texte) > 500 or p.get('categorie') not in CATEGORIES
                or p.get('origine') not in ORIGINES or not isinstance(p.get('coche'), bool)):
            raise ValueError('Passages invalides.')
        passages.append({'texte': texte.strip(), 'categorie': p['categorie'], 'origine': p['origine'], 'coche': p['coche']})
    return passages


class RevisionPerimee(ValueError):
    """La fiche à l'écran et la version actuellement enregistrée ne correspondent plus."""


def verifier_revision(acte, attendue):
    if type(attendue) is not int or attendue != acte['revision']:
        raise RevisionPerimee('La version affichée est ancienne ou absente. Rouvrez la fiche ou la liste et relisez-la avant de continuer.')


def _enregistrer_relecture(base, ident, passages, revision, reperage=None):
    passages = _passages(passages)
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        acte = _ligne(c, ident)
        refuser_si_fige(acte)
        verifier_revision(acte, revision)
        date, avertissement = reperage if reperage is not None else (acte['repere_le'], acte['avertissement'])
        c.execute("UPDATE publications SET passages=?, repere_le=?, avertissement=?, statut='a_relire', valide_par='', "
                  "valide_le='', empreinte_pdf='', date_dossier='' WHERE id=?",
                  (json.dumps(passages, ensure_ascii=False), date, avertissement, ident))
        return _public(_ligne(c, ident))


def enregistrer_passages(base: Path, ident: str, passages: list[dict], revision: int) -> dict:
    """Une édition s'applique uniquement à la version dont les cases ont été relues."""
    return _enregistrer_relecture(base, ident, passages, revision)


def enregistrer_reperage(base: Path, ident: str, passages: list[dict], avertissement: str | None, revision: int) -> dict:
    return _enregistrer_relecture(base, ident, passages, revision, (_maintenant(), avertissement or ''))


def valider(base: Path, donnees_dir: Path, ident: str, par: str, revision: int) -> dict:
    """Vérifie la fiche relue et engage la validation seulement si cette même révision est toujours en vigueur."""
    acte = lire(base, donnees_dir, ident)
    refuser_si_fige(acte)
    verifier_revision(acte, revision)
    if not acte['repere_le']:
        raise ValueError('Lancez d’abord le repérage des données personnelles.')
    verifier_version(acte['objet'], acte['paragraphes'], fiche_existante=True)
    blocs = [acte['objet']] + acte['paragraphes']
    for passage in acte['passages']:
        if passage['coche'] and not any(anonymisation.occurrences(bloc, passage) for bloc in blocs):
            raise ValueError(f'Passage introuvable dans le texte : « {passage["texte"]} ». Décochez-le, puis sélectionnez '
                             'le passage exact dans le texte.')
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        verifier_revision(_ligne(c, ident), revision)
        c.execute("UPDATE publications SET statut='valide', valide_par=?, valide_le=? WHERE id=?", (par or '', _maintenant(), ident))
        return _public(_ligne(c, ident))


def enregistrer_dossiers(base, donnees_dir, produits, revisions, date_dossier, *, index=None):
    """Engage ensemble les empreintes du lot après vérification de toutes les révisions utilisées pour le rendre."""
    date_dossier = _date(date_dossier)
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        if index is not None:
            proprietaire, visiteurs_a_part, attendus = index
            actuels = [tuple(l) for l in _lignes_visibles(c, proprietaire, visiteurs_a_part, index=True)]
            if actuels != attendus:
                raise RevisionPerimee('Les actes publiés ont changé. Rouvrez la liste et préparez un nouveau dossier.')
        for ident in produits:
            acte = _ligne(c, ident)
            verifier_revision(acte, revisions.get(ident))
            if acte['statut'] != 'valide':
                raise ValueError('Seul un acte validé entre dans un dossier de publication.')
        anciens = {}
        try:
            for ident, produit in produits.items():
                fichier = dossier(donnees_dir, ident) / 'publie.pdf'
                contenu_ancien = fichier.read_bytes() if fichier.exists() else None
                ecrire_prive(fichier, produit['pdf'])
                anciens[fichier] = contenu_ancien
                c.execute('UPDATE publications SET empreinte_pdf=?, date_dossier=? WHERE id=?',
                          (produit['empreinte'], date_dossier, ident))
            c.commit()
        except BaseException:
            for fichier, contenu in anciens.items():
                if contenu is None:
                    fichier.unlink(missing_ok=True)
                else:
                    ecrire_prive(fichier, contenu)
            raise


def enregistrer_dossier(base, donnees_dir, ident, empreinte, pdf, date_dossier, revision):
    enregistrer_dossiers(base, donnees_dir, {ident: {'empreinte': empreinte, 'pdf': pdf}}, {ident: revision}, date_dossier)


def marquer_publie(base: Path, ident: str, revision: int) -> dict:
    """Mise en ligne déclarée : à la date du dossier, imprimée sur le PDF déposé."""
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        ligne = _ligne(c, ident)
        verifier_revision(ligne, revision)
        if ligne['statut'] != 'valide':
            raise ValueError('Seul un acte validé peut être déclaré mis en ligne.')
        if not ligne['empreinte_pdf'] or not ligne['date_dossier']:
            raise ValueError('Préparez d’abord le dossier de publication.')
        if ligne['date_dossier'] > aujourdhui():
            raise ValueError('La date prévue est à venir. Confirmez la mise en ligne après le dépôt sur le site.')
        c.execute("UPDATE publications SET statut='publie', publie_le=date_dossier WHERE id=?", (ident,))
        return _public(_ligne(c, ident))


def retirer(base: Path, ident: str, motif: str, par: str, revision: int) -> dict:
    """Retrait de la publication d'un acte mis en ligne (publié par erreur, donnée personnelle restée visible…) : il sort
    de l'index et du fichier national des prochains dossiers ; son PDF et son empreinte restent conservés. Le fichier
    déposé sur le site de la collectivité est à retirer par l'agent."""
    motif = ' '.join(str(motif or '').split())
    if not motif or len(motif) > MOTIF_MAX:
        raise ValueError('Indiquez le motif du retrait.' if not motif else f'Motif trop long ({MOTIF_MAX} caractères au plus).')
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        acte = _ligne(c, ident)
        verifier_revision(acte, revision)
        if acte['statut'] != 'publie':
            raise ValueError('Seul un acte publié peut être retiré de la publication.')
        c.execute("UPDATE publications SET statut='retire', retire_le=?, retire_par=?, retire_motif=? WHERE id=?",
                  (_maintenant(), par or '', motif, ident))
        return _public(_ligne(c, ident))


def supprimer(base: Path, donnees_dir: Path, ident: str, revision: int) -> None:
    """Un acte saisi par erreur, tant qu'il n'est pas publié."""
    with ouvrir(base) as c:
        c.execute('BEGIN IMMEDIATE')
        acte = _ligne(c, ident)
        refuser_si_fige(acte)
        verifier_revision(acte, revision)
        c.execute('DELETE FROM publications WHERE id=?', (ident,))
    shutil.rmtree(dossier(donnees_dir, ident), ignore_errors=True)
