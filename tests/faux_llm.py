# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
"""Fausse IA locale pour les tests de bout en bout : un serveur compatible OpenAI, sans dépendance externe.

Elle remplace LM Studio dans la CI et dans `tests/integration.sh` (service `faux-llm` de compose.ci.yaml), pour que
le parcours complet (n8n, rédaction d'un arrêté, réponse sourcée) se déroule sans aucun vrai modèle.
Réponses fixes :
  GET  /v1/models            -> un seul modèle, « faux-modele » ;
  POST /v1/chat/completions  -> si un message contient « JSON » (demande de rédaction d'arrêté) : un objet JSON
                                objet / considerants / mesures, sans aucun chiffre, que `arretes.read_completion`
                                accepte ; sinon : une phrase citant la source [1].
Le serveur n'écrit aucun journal : il ne laisse rien de ce qu'il reçoit.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELE = 'faux-modele'
PORT = 1234
MAX_CORPS = 4 * 1024 * 1024
REPONSE_SOURCEE = 'Les archives municipales fictives ouvrent le mardi matin [1].'
# Aucun chiffre : `read_completion` rejette tout nombre absent des faits, et aucune référence juridique (L2212-1…).
REPONSE_ARRETE = {
    'objet': 'portant interdiction temporaire du stationnement à l’occasion d’un déménagement',
    'considerants': ['Considérant le déménagement annoncé dans le périmètre et pendant la période définis au présent '
                     'arrêté, et la nécessité de réglementer temporairement le stationnement pour permettre son '
                     'déroulement,'],
    'mesures': ['Le stationnement de tout véhicule est interdit dans le périmètre défini au présent arrêté, '
                'pendant la durée du déménagement.'],
}


def demande_un_arrete(messages) -> bool:
    """Vrai quand l'un des messages contient « JSON » : seule la rédaction d'arrêté le réclame."""
    return isinstance(messages, list) and any(
        isinstance(m, dict) and 'JSON' in str(m.get('content', '')) for m in messages)


def reponse_chat(corps: dict) -> dict:
    contenu = (json.dumps(REPONSE_ARRETE, ensure_ascii=False) if demande_un_arrete(corps.get('messages'))
               else REPONSE_SOURCEE)
    return {'id': 'faux-0001', 'object': 'chat.completion', 'model': MODELE,
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': contenu}}]}


class Gestionnaire(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _envoyer(self, statut: int, donnees: dict) -> None:
        corps = json.dumps(donnees, ensure_ascii=False).encode('utf-8')
        self.send_response(statut)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self):
        if self.path.split('?')[0] == '/v1/models':
            self._envoyer(200, {'data': [{'id': MODELE}]})
        else:
            self._envoyer(404, {'error': 'introuvable'})

    def do_POST(self):
        try:
            longueur = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            longueur = -1
        if not 0 <= longueur <= MAX_CORPS:
            self.close_connection = True
            return self._envoyer(400, {'error': 'requête invalide'})
        brut = self.rfile.read(longueur)
        if self.path.split('?')[0] != '/v1/chat/completions':
            return self._envoyer(404, {'error': 'introuvable'})
        try:
            corps = json.loads(brut or b'{}')
        except ValueError:
            return self._envoyer(400, {'error': 'JSON invalide'})
        self._envoyer(200, reponse_chat(corps if isinstance(corps, dict) else {}))

    def log_message(self, *args):  # aucune ligne de journal : ni chemin, ni contenu
        pass


def creer_serveur(hote: str = '0.0.0.0', port: int = PORT) -> ThreadingHTTPServer:
    serveur = ThreadingHTTPServer((hote, port), Gestionnaire)
    serveur.daemon_threads = True
    return serveur


def main(argv: list[str] | None = None) -> int:
    """`python faux_llm.py [port]` : le port 1234 par défaut ; un autre port sert aux essais sur une machine où il est pris."""
    argv = sys.argv[1:] if argv is None else argv
    serveur = creer_serveur(port=int(argv[0]) if argv else PORT)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
