# SPDX-License-Identifier: EUPL-1.2
# SPDX-FileCopyrightText: 2026 Rhiad AZZABI (TerritorIA, SIREN 989 227 731)
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import config


class ReglagesTests(unittest.TestCase):
    def test_valeurs_par_defaut(self):
        r=config.charger({})
        self.assertEqual((r.mode,r.port,r.origine,r.delai_n8n),('solo',8878,'http://127.0.0.1:8878',960))
        self.assertEqual((r.ecoute,r.donnees),('127.0.0.1',Path('./territoria-donnees/territoria')))
        self.assertEqual((r.webhook_conversation,r.webhook_arrete,r.n8n_status),
                         ('http://n8n:5678/webhook/territoria-conversation',
                          'http://n8n:5678/webhook/territoria-arrete',
                          'http://n8n:5678/healthz/readiness'))
        self.assertEqual((r.webhook_token,r.service_token),('',''))
        self.assertEqual((r.llm_url,r.modele),('http://127.0.0.1:1234/v1','gemma-4-12b-it-mlx'))
        self.assertEqual((r.pdftoppm,r.tesseract),('pdftoppm','tesseract'))
        self.assertEqual(r.transcription_modele,'large-v3-turbo')
        self.assertIs(r.hors_ligne,False)

    def test_modele_de_transcription_lu_dans_l_environnement(self):
        self.assertEqual(config.charger({'TERRITORIA_TRANSCRIPTION_MODELE':'medium'}).transcription_modele,'medium')

    def test_origine_suit_le_port(self):
        self.assertEqual(config.charger({'TERRITORIA_PORT':'9001'}).origine,'http://127.0.0.1:9001')

    def test_origine_explicite_prime_sur_le_port(self):
        r=config.charger({'TERRITORIA_PORT':'9001','TERRITORIA_ORIGINE':'http://localhost:9001/'})
        self.assertEqual(r.origine,'http://localhost:9001')

    def test_variables_lues_dans_l_environnement(self):
        r=config.charger({'TERRITORIA_DONNEES':'/donnees','TERRITORIA_DELAI_N8N':'30','TERRITORIA_MODELE':'autre',
                          'TERRITORIA_WEBHOOK_TOKEN':'w','TERRITORIA_SERVICE_TOKEN':'s'})
        self.assertEqual((r.donnees,r.delai_n8n,r.modele,r.webhook_token,r.service_token),(Path('/donnees'),30,'autre','w','s'))

    def test_valeur_vide_vaut_valeur_par_defaut(self):
        r=config.charger({'TERRITORIA_PORT':'','TERRITORIA_MODELE':'  '})
        self.assertEqual((r.port,r.modele),(8878,'gemma-4-12b-it-mlx'))

    def test_nombre_invalide_refuse(self):
        with self.assertRaisesRegex(ValueError,'TERRITORIA_PORT'):config.charger({'TERRITORIA_PORT':'http'})
        with self.assertRaisesRegex(ValueError,'TERRITORIA_DELAI_N8N'):config.charger({'TERRITORIA_DELAI_N8N':'vite'})

    def test_hors_ligne_accepte_zero_un_ou_vide(self):
        self.assertIs(config.charger({'TERRITORIA_HORS_LIGNE':'1'}).hors_ligne,True)
        for valeur in ('0','','  '):
            self.assertIs(config.charger({'TERRITORIA_HORS_LIGNE':valeur}).hors_ligne,False)

    def test_hors_ligne_autre_valeur_refusee(self):
        for valeur in ('oui','true','True','2','non'):
            with self.subTest(valeur=valeur),self.assertRaisesRegex(ValueError,'TERRITORIA_HORS_LIGNE'):
                config.charger({'TERRITORIA_HORS_LIGNE':valeur})

    def test_ia_en_ligne_par_defaut_vide(self):
        self.assertEqual(config.charger({}).ia_en_ligne,'')
        self.assertEqual(config.charger({}).mistral_cle,'')

    def test_ia_en_ligne_mistral(self):
        self.assertEqual(config.charger({'TERRITORIA_IA_EN_LIGNE':'mistral'}).ia_en_ligne,'mistral')

    def test_ia_en_ligne_invalide_refusee(self):
        for valeur in ('openrouter','oui','Mistral'):
            with self.subTest(valeur=valeur),self.assertRaisesRegex(ValueError,'TERRITORIA_IA_EN_LIGNE doit être vide ou valoir « mistral »'):
                config.charger({'TERRITORIA_IA_EN_LIGNE':valeur})

    def test_cle_mistral_rognee_et_absente_de_la_representation(self):
        reglages=config.charger({'TERRITORIA_MISTRAL_CLE':'  k-essai \n'})
        self.assertEqual(reglages.mistral_cle,'k-essai')
        self.assertNotIn('k-essai',repr(reglages))

    def test_demonstration_par_defaut_eteinte(self):
        self.assertIs(config.charger({}).demonstration,False)
        self.assertEqual(config.charger({}).domaine,'')

    def test_demonstration_activee(self):
        reglages=config.charger({'TERRITORIA_DEMONSTRATION':'1','TERRITORIA_DOMAINE':'demo.exemple.fr'})
        self.assertIs(reglages.demonstration,True)
        self.assertEqual(reglages.domaine,'demo.exemple.fr')

    def test_demonstration_valeur_invalide_refusee(self):
        for valeur in ('oui','true','2'):
            with self.subTest(valeur=valeur),self.assertRaisesRegex(ValueError,'TERRITORIA_DEMONSTRATION doit valoir « 0 » ou « 1 »'):
                config.charger({'TERRITORIA_DEMONSTRATION':valeur})

    def test_secrets_absents_de_la_representation(self):
        texte=repr(config.charger({'TERRITORIA_SERVICE_TOKEN':'jeton-secret-x','TERRITORIA_WEBHOOK_TOKEN':'jeton-secret-y'}))
        self.assertNotIn('jeton-secret-x',texte)
        self.assertNotIn('jeton-secret-y',texte)
        self.assertIn('port=8878',texte)

    def test_mode_inconnu_refuse(self):
        with self.assertRaisesRegex(ValueError,'TERRITORIA_MODE'):config.charger({'TERRITORIA_MODE':'public'})

    def test_mode_equipe_verrouille(self):
        with self.assertRaisesRegex(SystemExit,'sous-projet 2'):
            config.verifier_demarrage(config.charger({'TERRITORIA_MODE':'equipe'}))

    def test_mode_equipe_message_exact(self):
        with self.assertRaises(SystemExit) as erreur:
            config.verifier_demarrage(config.charger({'TERRITORIA_MODE':'equipe'}))
        self.assertEqual(str(erreur.exception),
                         "Le mode équipe n'est pas encore disponible : l'authentification (sous-projet 2) est requise avant toute ouverture au réseau.")

    def test_mode_solo_demarre(self):
        self.assertIsNone(config.verifier_demarrage(config.charger({})))

    def test_reglages_geles(self):
        with self.assertRaises(FrozenInstanceError):config.charger({}).port=1

    def test_reglages_du_processus_charges_a_l_import(self):
        self.assertIsInstance(config.REGLAGES,config.Reglages)


if __name__=='__main__':unittest.main()
