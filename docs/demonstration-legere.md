# Démonstration légère (hébergement mutualisé, sans conteneurs)

Pour **faire essayer** TerritorIA en ligne, avec une IA par API (OpenRouter ou Mistral), sur un hébergement cPanel
comme O2switch : ni Docker, ni n8n, ni LM Studio. TerritorIA enchaîne lui-même les étapes que n8n fait dans
l'installation complète (`TERRITORIA_ORCHESTRATION=interne`). Pour travailler en local, une collectivité installe la
version complète depuis GitHub, avec le [guide pour débutant](guide-debutant.md).

Limites : pas de transcription des séances (la page est masquée et le serveur refuse leurs routes ; seules restent,
pour le compte qui en possède déjà dans les données, leur liste et leur suppression par
`DELETE /api/seances/<identifiant>`), pas de lecture des
PDF scannés (OCR), et l'application se met en veille après un moment sans visite. Ce qu'un visiteur peut déposer et
la durée de vie de son essai sont décrits dans [`demonstration.md`](demonstration.md).

Exemple en service : <https://demo.territoria.ovh> (O2switch).

## Mise en place (cPanel, accès SSH)

1. **Sous-domaine** dans cPanel (dossier à lui, hors de `public_html` et des dossiers de vos autres sites, par
   exemple `territoria-demo-web`), et enregistrement `A` vers l'adresse de l'hébergement chez le gestionnaire du
   domaine.
2. **Code** dans un dossier privé (par exemple `~/territoria-demo`) : `passenger_wsgi.py`, `app/`, `requirements.lock`, `vendor/`,
   plus `app/public/21st-components.js` (construit par l'image de conteneur, ou `npm run build` dans `app/frontend`).
   Dossiers en 755 et fichiers en 644 : Passenger doit pouvoir les lire.
3. **Application Python** : `cloudlinux-selector create --json --interpreter python --version 3.12 --app-root
   territoria-demo --domain <votre sous-domaine> --app-uri / --startup-file passenger_wsgi.py --entry-point
   application`. L'outil **remplace `passenger_wsgi.py`** par le sien : remettre celui du dépôt ensuite.
4. **Réglages** (`cloudlinux-selector set --env-vars`) : `TERRITORIA_DONNEES` (dossier privé, 0700, hors du web),
   `TERRITORIA_DEMONSTRATION=1`, `TERRITORIA_ORCHESTRATION=interne`, `TERRITORIA_ORIGINE=https://<adresse>`,
   `TERRITORIA_DOMAINE`, `TERRITORIA_HORS_LIGNE=1`.
5. **Dépendances** dans l'environnement de l'application : vérifier `vendor/SHA256SUMS`, puis
   `pip install -r requirements.lock` et `pip install --no-deps vendor/droit_territorial-0.4.0-py3-none-any.whl`.
6. **Corpus fictif** : `python -c 'import demo, config; demo.installer(config.REGLAGES.donnees)'` depuis `app/`, avec
   les mêmes variables.
7. **`.htaccess` du dossier du sous-domaine**, avant le bloc CloudLinux : ne pas hériter des règles d'un `.htaccess`
   parent (une réécriture vers `index.html` y provoquait une erreur 500), renvoyer `http` vers `https`, et poser la
   politique de sécurité de l'application (`CSP` de `app/web.py`) à la place de celle du parent :

   ```apache
   RewriteEngine On
   RewriteCond %{HTTPS} !=on
   RewriteCond %{REQUEST_URI} !^/\.well-known/acme-challenge/
   RewriteRule ^ https://<votre sous-domaine>%{REQUEST_URI} [R=301,L]
   <IfModule mod_headers.c>
     Header always set Content-Security-Policy "<valeur de CSP dans app/web.py>"
   </IfModule>
   ```

   En démonstration, l'application pose elle-même `X-Robots-Tag: noindex, nofollow` et, en HTTPS,
   `Strict-Transport-Security` : rien à ajouter ici pour ces deux en-têtes.
8. **HTTPS** : cPanel › Sécurité › Let's Encrypt SSL, ligne du sous-domaine seule (sans `www.`), validation http-01.
9. **Premier administrateur**, sur le serveur : `python -m outils premier-administrateur <identifiant> --nom "…"
   --base <TERRITORIA_DONNEES>/territoire.sqlite3` (mot de passe sur l'entrée standard ou en saisie masquée). Choisissez
   un identifiant difficile à deviner : sur un serveur public, cinq essais ratés bloquent le compte pendant 15 minutes.
10. **Clé OpenRouter ou Mistral** : saisie par l'administrateur dans Administration › Intelligence artificielle.
    Chez le fournisseur, fixez un **plafond de dépense ferme** sur cette clé : les plafonds de TerritorIA limitent le
    nombre de demandes, pas leur coût.

Après une mise à jour du code : `touch ~/territoria-demo/tmp/restart.txt` redémarre l'application.

---

TerritorIA — © 2026 Rhiad AZZABI (entreprise individuelle TerritorIA, SIREN 989 227 731). Licence EUPL 1.2 : voir
[`NOTICE.md`](../NOTICE.md).
