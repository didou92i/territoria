# TerritorIA

© 2026 Rhiad AZZABI, entreprise individuelle TerritorIA (SIREN 989 227 731).
Créé par Rhiad AZZABI.

TerritorIA est distribué sous la **licence publique de l'Union européenne (EUPL 1.2)**. Son texte officiel figure
dans le dépôt en français, dans [`EUPL-1.2-fr.txt`](EUPL-1.2-fr.txt), et en anglais, dans [`LICENSE`](LICENSE) : les
deux versions ont la même valeur. Son utilisation est libre et gratuite, y compris par les
collectivités.

## Conserver les mentions

L'article 5 de l'EUPL impose à quiconque copie, modifie, distribue ou met en ligne TerritorIA de conserver intactes
les mentions de droit d'auteur, ce fichier et la référence à la licence. Chaque fichier source porte aussi les deux
lignes suivantes, à garder en place :

```
SPDX-License-Identifier: EUPL-1.2
SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
```

Toute version modifiée doit signaler ses modifications et rester sous EUPL 1.2, ou sous une licence compatible que
l'EUPL énumère en annexe. Cela vaut aussi quand elle est proposée en ligne.

## Composants tiers inclus dans le dépôt

| Composant | Emplacement | Licence |
|---|---|---|
| React et React DOM | `app/frontend/src/vendor/REACT-LICENSE.txt`, `REACT-DOM-LICENSE.txt` | MIT |
| Fil de messages et zone de saisie (hirael) | `app/frontend/src/vendor/hirael/` | MIT, © 2026 Mohammad Shehadeh |
| Composant de citation (redrob) | `app/frontend/src/vendor/redrob/` | MIT, © 2026 Janghoon Lee (Redrob) et contributeurs |
| Autres paquets de l'interface React | `app/frontend/THIRD_PARTY_LICENSES.txt` | voir ce fichier |
| `droit_territorial` 0.4.0 | `vendor/droit_territorial-0.4.0-py3-none-any.whl` | MIT pour le code (© 2026 didou92i et contributeurs) ; CC BY-SA 4.0 pour les données juridiques qu'il embarque (© 2026 @brissonjo-sudo). Les deux textes sont dans la roue |

Ces composants gardent leur propre licence : ils ne portent pas les lignes SPDX ci-dessus.

## Installés, mais non distribués avec TerritorIA

Les scripts d'installation téléchargent ces logiciels et modèles depuis leurs sources officielles, chacun sous ses
propres conditions :

- **Paquets Python** des images, listés dans `requirements.lock` et `conteneurs/travaux-requirements.lock`. Ceux du
  serveur sont sous licences permissives : MIT, BSD, Apache 2.0, MPL 2.0, PSF et ZPL.
- **n8n**, l'orchestrateur, sous la licence « Sustainable Use License » de n8n GmbH. Ce n'est pas une licence libre :
  lisez-la avant de proposer à des tiers un service qui repose sur n8n.
- **LM Studio**, le serveur d'IA local, sous ses conditions d'utilisation, que la collectivité accepte elle-même
  (<https://lmstudio.ai/app-terms>).
- **Le moteur de conteneurs** : Podman (Apache 2.0) ou Docker. Docker Desktop est payant pour les administrations.
- **Caddy** (Apache 2.0), le serveur HTTPS du mode équipe et du serveur de démonstration.
- **FFmpeg**, pour convertir l'audio des séances : le paquet `ffmpeg` de Debian dans l'image de transcription, et les
  bibliothèques FFmpeg embarquées par le paquet Python `av`. FFmpeg est sous LGPL ou GPL selon les composants.
- **L'API de Mistral**, service d'IA en ligne, seulement si la collectivité la choisit à l'installation
  (`docs/mistral.md`), sous les conditions de Mistral qu'elle accepte elle-même.
- **OpenRouter**, service d'IA en ligne, sur le **seul serveur de démonstration** (`docs/demonstration.md`), sous les
  conditions d'OpenRouter acceptées par l'exploitant de ce serveur. Une installation normale ne l'utilise jamais.
- **Les modèles d'IA** : modèle de langage du palier choisi (Gemma 4 de Google par défaut, voir `install/modeles.json`,
  sous les conditions d'utilisation de Gemma), Whisper large-v3-turbo (MIT), segmentation pyannote (MIT), empreintes
  de voix 3D-Speaker (Apache 2.0). Chacun reste sous ses propres conditions.

## Nom

« TerritorIA » est le nom commercial de l'entreprise individuelle de Rhiad AZZABI (SIREN 989 227 731). La licence
couvre le code, pas le nom : une version modifiée ne doit pas se présenter comme la version d'origine.
