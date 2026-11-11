# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Orientation du chatbot vers la rédaction d'actes, sans classification distante."""
import re
import branding
from legal_sources import _plain,classify

# Ajoutée à chaque relance ; ce n'est pas une précision de l'utilisateur, elle ne compte pas dans le seuil de précision.
PRECISION='\n\nPrécision la plus récente, prioritaire :\n'
QUESTION_COMMUNE='Pour quelle commune ou quel organisme préparez-vous cet arrêté ? Je conserve votre demande pour rédiger le document dès cette précision.'
RELANCE_COMMUNE=('Je n’ai pas reconnu de nom de collectivité dans votre réponse. Écrivez-le sous la forme « Commune de … » '
                 '(par exemple « Commune de Démoville » pour un essai) : je rédige l’arrêté aussitôt.')
PROPOSITION_EXEMPLE=('Il me manque encore des éléments pour rédiger cet arrêté. Décrivez la mesure, le lieu, le motif et la période, '
                     'ou répondez « un exemple » : je rédigerai un exemple fictif.')
# Exemple fictif (rue, dates et travaux inventés) : stationnement interdit pendant des travaux, un thème que `classify` reconnaît.
EXEMPLE=('Exemple fictif, demandé pour découvrir l’outil : interdire le stationnement des véhicules rue des Essais, du n° 1 au n° 30, '
         'du lundi 11 janvier 2027 à 7 h au vendredi 15 janvier 2027 à 18 h, pendant des travaux de réfection de la chaussée. '
         'L’accès des riverains et des véhicules de secours reste assuré.')
REPONSE_EXEMPLE=('Voici un exemple fictif d’arrêté : interdiction de stationner rue des Essais pendant des travaux de voirie. '
                 'La rue, les dates et les travaux sont inventés. Vous pouvez le modifier ici (par exemple « remplace la rue des Essais '
                 'par la rue des Lilas ») et télécharger le Word. Pour votre propre situation, ouvrez une nouvelle conversation et '
                 'décrivez la mesure, le lieu, le motif et la période.')
# « Par exemple » (repris des relances) et « de type » ne sont pas des demandes d'exemple.
DEMANDE_EXEMPLE=re.compile(r'(?<!par )\b(?:exemples?|modeles?)\b|\barretes?\s+types?\b')


def _mots(texte):
    return len(re.findall(r'\w+',texte))


def _lieu(collectivite):
    """« Commune de Senlis » → « commune de Senlis » ; sans commune enregistrée, la commune fictive des démonstrations."""
    nom=str(collectivite or '').strip()
    if re.match(r'(?:commune|ville)\s+(?:de\s+|d[’\'])\S',nom,re.I):return nom[0].lower()+nom[1:]
    return 'commune de Démoville'


def phrases_types(collectivite):
    """Relance d'une demande vague : des phrases à renvoyer telles quelles, chacune sur un thème reconnu et avec sa commune."""
    lieu=_lieu(collectivite)
    phrases=(f'Interdire le stationnement rue des Essais, {lieu}, du 11 au 15 janvier 2027, pendant des travaux de voirie.',
             f'Réglementer le marché hebdomadaire de la {lieu}, place de la Mairie, le samedi de 8 h à 13 h.',
             f'Interdire le barbecue et les feux dans les parcs de la {lieu} du 1er juillet au 31 août 2027.')
    return ('Je rédige l’arrêté dans cette conversation dès que je connais la situation : la mesure, le lieu, le motif et, '
            's’il s’agit d’une mesure temporaire, la période. Répondez par exemple avec l’une de ces phrases, à adapter :\n\n'
            +'\n'.join('- '+p for p in phrases)
            +'\n\nPour voir d’abord le résultat, répondez « un exemple » : je rédigerai un exemple fictif.')


def est_exemple(data):
    return isinstance(data,dict) and data.get('contexte')==EXEMPLE


def sans_repetition(texte,precedent,data):
    """Filet : jamais deux fois de suite la même relance, sinon l'utilisateur tourne en rond."""
    if texte.strip()!=str(precedent or '').strip():return texte
    candidats=(RELANCE_COMMUNE,) if texte==QUESTION_COMMUNE else (phrases_types((data or {}).get('collectivite','')),PROPOSITION_EXEMPLE)
    return next(c for c in candidats if c.strip()!=texte.strip())


def is_drafting(question,previous=None,assistant=''):
    text=_plain(question)
    if previous and previous.get('kind')=='arrete_clarification':return True
    modifying=r'\b(modifi\w*|remplac\w*|ajout\w*|retir\w*|corrig\w*|decal\w*|prolong\w*|chang\w*|precis\w*|regroup\w*|reuni\w*|plutot|regenere\w*|refai\w*)\b'
    if previous and previous.get('kind')=='arrete' and re.search(modifying,text):return True
    # Après un arrêté, une consigne (« Il faut autoriser… et interdire… ») le complète ; une question sur ces mots reste une question.
    complement=r'\b(il\s+faut|autoris\w*|interdi\w*|prevo\w*|prevoi\w*|fix\w*|limit\w*|etend\w*|redui\w*|supprim\w*)\b'
    if previous and previous.get('kind')=='arrete' and re.search(complement,text) and not text.rstrip().endswith('?'):return True
    if assistant=='arrete':return True
    if not re.search(r'\barrete\b',text):return False
    if re.search(r'\b(?:ne\s+(?:redige|genere|cree)|sans\s+(?:rediger|generer|creer))',text):return False
    return bool(re.search(r'\b(redig\w*|gener\w*|prepar\w*|etabli\w*|creer?\w*|propos\w*|faire|fai[st])\b',text)
                or re.search(r'\barrete\s+(?:pour|de|concernant|relatif)',text))


def collectivity(question):
    match=re.search(r'\b(?:commune|ville|mairie|municipalité)\s+(?:de\s+|d[’\'])?([^,;\n?.]+)',question,re.IGNORECASE)
    if match:
        name=re.split(r'\s+(?:concernant|relative?|portant|afin|pour|sur|avec|interdisant|réglementant|reglementant|entre|du|le|la)\b',match.group(1),maxsplit=1,flags=re.IGNORECASE)[0].strip()
        if name and len(name)<=100:return 'Commune de '+name
    return ''


def prepare(question,previous,data_dir):
    prior=previous.get('inputs',{}) if previous else {}
    kind=previous.get('kind') if previous else ''
    if kind in ('arrete','arrete_clarification'):
        context=prior.get('contexte','')+PRECISION+question
        data={**prior,'contexte':context}
    else:data={'contexte':question}
    # Une commune explicitement citée l'emporte sur l'identité enregistrée.
    identified=collectivity(question)
    if not identified and previous and previous.get('kind')=='arrete_clarification' and not prior.get('collectivite'):
        short_name=re.sub(r'^(?:à|pour)\s+','',question.strip().rstrip('.'),flags=re.I)
        if re.fullmatch(r'[A-ZÀ-Ÿ][\wÀ-ÿ’\' -]{1,70}',short_name) and len(short_name.split())<=5:
            identified='Commune de '+short_name
    identity=branding.profile(data_dir)
    data['collectivite']=identified or data.get('collectivite') or identity['collectivite']
    normalized=_plain(data['contexte'].replace(PRECISION,'\n'))
    vague=classify(normalized)=='indetermine' and _mots(normalized)<22
    demande=_plain(question)
    # Exemple demandé hors de la modification d'un projet, ou deuxième demande vague d'affilée : on rédige un exemple fictif
    # plutôt que de reposer une question. Après une demande déjà précise, seule une réponse brève (« un exemple ») le déclenche.
    bref=_mots(demande)<=5 and classify(demande)=='indetermine'
    exemple=kind!='arrete' and DEMANDE_EXEMPLE.search(demande) and (vague or bref)
    if exemple or (vague and kind=='arrete_clarification'):
        return {**data,'contexte':EXEMPLE,'collectivite':data['collectivite'] or 'Commune de Démoville'},''
    if vague:
        return data,phrases_types(data['collectivite'])
    if not data['collectivite']:
        return data,QUESTION_COMMUNE
    return data,''


def sources(result):
    return [{'id':s['id'],'title':s['title'],'page':s['page'],'excerpt':s['excerpt'],
             'kind':'arrete','space':'Modèle local · OCR','captured':'',
             'url':'','method':s['method']} for s in result.get('examples',[])]
