# Smart EQ 453 — compteur de pompe de roulage

Lecture et remise à zéro du compteur `V_Timer_DrivWEP_ON` (DID `0x3531`) dans le calculateur **N127 / EVC453**. La séquence a été validée sur une **Smart EQ Forfour 453.091**, variante `EVC453_EVC_3180_RH4_810_V2`, avec un **DrewTech MongoosePro JLR**.

## Message au tableau de bord

Le défaut XENTRY **`P0463F1`** concerne la pompe de refroidissement en mode roulage. Le message **« Dysfonctionnement – Visite atelier »** et le **voyant jaune** peuvent accompagner ce défaut. Le même avertissement peut avoir d’autres causes. Le code se vérifie dans le N127. [Message et voyant dans le manuel smart EQ Forfour, p. 184](https://moba.i.mercedes-benz.com/markets-smart/ece-row/baix/cars/smart-eq-forfour-453_2018_c/en_GB/downloads/12_17/BA_smart_453_09_AEJ2017-1b_en_GB.pdf#page=184)

## Utilisation

Il faut **Python 3.8+ pour Windows en 32 bits** et le pilote J2534 DrewTech installé séparément. Branche le Mongoose, mets le contact et ferme XENTRY. Exécute les commandes avec ton interpréteur Python 32 bits.

```powershell
python .\smart_eq_pump_counter.py --check-driver
python .\smart_eq_pump_counter.py --read
```

Après **remplacement de la pompe et vérification de sa circulation**, la commande suivante sauvegarde la valeur initiale dans le dossier courant, demande la remise à zéro une seule fois et relit le compteur.

```powershell
python .\smart_eq_pump_counter.py --reset-after-pump-replacement
```

## Séquence validée

J2534 ISO 15765 à **500 kbit/s**. Requête CAN **`0x7E4`**, réponse CAN **`0x7EC`**. Les octets du tableau sont les données de diagnostic avant encapsulation ISO-TP.

| Opération | Requête | Réponse positive |
|---|---|---|
| Lecture du compteur | `22 35 31` | `62 35 31` et 4 octets |
| Session étendue | `10 C0` | `50 C0` |
| Remise à zéro après remplacement | `2E 35 31 00 00 00 00` | `6E 35 31` |
| Vérification | `22 35 31` | `62 35 31 00 00 00 00` lors de l’essai |

La commande traite uniquement le compteur de pompe. Le diagnostic des défauts et l’entretien périodique du véhicule se font séparément.
