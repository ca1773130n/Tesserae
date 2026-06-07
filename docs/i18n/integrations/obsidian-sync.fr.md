# Synchronisation bidirectionnelle Obsidian — conception proposée

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **Statut : Livré (Tier 1, v0.5.0).** Le lecteur de surcouches, les zones d'ajout user-notes, le mode watch et le nettoyage des orphelins décrits ci-dessous sont actifs derrière `tesserae project obsidian-sync`. Cette page fait à la fois office de justification de conception et de guide utilisateur. La fédération multi-vaults (Tier 3) reste hors périmètre.

L'[export Obsidian](obsidian.fr.md) était autrefois strictement à sens unique : le graphe typé dans `.tesserae/graph.json` est projeté vers le vault, et `project compile` écrase les fichiers projetés. `obsidian-sync` ajoute le sens inverse — modifiez une description dans Obsidian, et elle survit à la recompilation.

Ce document précise comment cela fonctionne sans rendre incohérent le modèle de données.

## Changement stratégique, énoncé clairement

Le README actuel décline toute responsabilité concernant l'édition en direct :

> Tesserae choisit la compilation depuis la source plutôt que l'édition en direct. Si vous voulez éditer des notes dans une UI, utilisez Logseq ou Obsidian.

La synchronisation bidirectionnelle **modifie ce contrat** pour un sous-ensemble de champs. Cela mérite d'être délibéré. L'objectif n'est pas qu'« Obsidian devienne l'éditeur » — c'est que « les modifications de l'utilisateur dans Obsidian ne soient pas silencieusement détruites lors de la recompilation ».

## L'idée centrale : des surcouches, pas des fusions

Plutôt que d'essayer de fusionner deux copies divergentes du même nœud, traitez le vault comme une **couche de diff** par-dessus la projection :

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` réside dans `.tesserae/` et est **calculé**, pas rédigé manuellement. À chaque compilation, Tesserae parcourt le vault, compare chaque page projetée à ce que la projection précédente avait écrit, et enregistre chaque modification introduite par l'utilisateur sous forme d'entrée de surcouche. Le graphe final est `base_graph` avec les surcouches appliquées. La projection suivante réécrit le résultat sur disque.

Round-trip stable. Recompiler le même vault sans changements côté source ne produit aucun diff.

## Propriété par champ

Chaque champ d'un nœud a un propriétaire. La propriété détermine ce qui se passe lorsque la source et le vault divergent.

| Champ | Propriété source | Surcharge vault possible | Notes |
|---|---|---|---|
| `id`, `type` | oui | non | Contrôlé par le schéma ; appartient à l'extracteur |
| `name` | initial | oui | L'utilisateur connaît souvent mieux le nom canonique que l'extracteur |
| `aliases` | initial | oui | Ajout-seulement depuis le vault ; les entrées du vault sont toujours préservées |
| `description` | initial | **oui** | La modification Obsidian la plus fréquente |
| `source_path` | oui | non | Provenance ; ne peut pas être effacé par édition |
| `metadata` (clés déclarées) | initial | oui | Ex. `arxiv_id`, `github_repo` — l'utilisateur peut corriger |
| `metadata.user.*` | s/o | oui | Espace de noms réservé aux clés utilisateur uniquement ; l'extracteur n'y écrit jamais |
| Arêtes sortantes (typées) | oui | non | Les arêtes vivent dans l'ontologie, pas dans le vault |
| Nouveaux wikilinks tapés par l'utilisateur | s/o | oui | Exposés comme `edge_type=user_link`, écrits dans le graphe |
| Bloc de corps `<!-- user-notes -->` | jamais écrit | toujours préservé | Zone en ajout-seulement que le projecteur ne touche jamais |

## Cas de conflit et valeurs par défaut

| Cas | Par défaut | Pourquoi |
|---|---|---|
| La `description` du vault diffère de la `description` ré-extraite de la source | **Le vault gagne**, journalisé dans `.tesserae/lint-report.md` sous « champs divergents » | Respecter l'édition utilisateur : l'utilisateur a clairement voulu cette modification. La piste d'audit permet de revoir plus tard. |
| Fichier source supprimé, page projetée toujours dans le vault | Retirer le nœud du graphe, le lister dans `.tesserae/orphans.md` | La source fait foi pour l'existence ; le journal des orphelins vous laisse décider de restaurer ou d'accepter |
| L'utilisateur a écrit un wikilink vers un slug qui n'existe pas | Créer un nœud tombstone (type `Stub`), faire apparaître dans le lint report | Ne pas perdre l'intention de l'utilisateur ; signaler pour nettoyage |
| L'utilisateur a ajouté une clé de frontmatter que le schéma ne connaît pas | Préserver en tant que `metadata.user.<key>`, ne jamais écraser | Compatible avec l'avenir sans polluer le graphe typé |
| Deux vaults sur des machines différentes éditent le même nœud, tous deux synchronisés via Obsidian Sync | **Hors périmètre pour la v1.** Last-writer wins au niveau du système de fichiers. | La vraie fédération multi-vaults est de Tier 3 ; à différer jusqu'à un cas d'usage réel |

## Zone d'ajout user-notes

Chaque page projetée se voit dotée d'une zone délimitée que le projecteur ne touche jamais :

```markdown
> [!quote] Paper
> Headline contribution and method sketch projected from the graph...

<!-- user-notes:start -->

Your notes here. Anything between the markers survives recompile forever.
Wikilinks here become `user_link` edges in the graph on the next pull.

<!-- user-notes:end -->

## Outgoing
- ...
```

Deux effets pratiques :
1. Les utilisateurs peuvent annoter n'importe quelle page (par ex. « voir le chapitre 4 de mes notes ») sans rien perdre lors d'une reconstruction.
2. La passe de pull scanne le bloc user-notes à la recherche de wikilinks et les expose comme arêtes typées ontologiquement `user_link`, leur donnant une accessibilité dans le graphe sans polluer les types d'arêtes formels.

## Transport distant — non-objectif explicite

Tesserae ne construit **pas** de serveur de synchronisation, de couche d'authentification, de démon de résolution de conflits, ni de vault hébergé. « Bidirectionnel » signifie ici « la compilation lit depuis le vault » — ce qui amène le vault jusqu'à la machine qui compile est le problème de l'utilisateur, résolu par des outils qui existent déjà :

| Stack | Coût | Notes |
|---|---|---|
| Obsidian Sync | Payant, 4-8 $/mois | Chiffré de bout en bout, officiel, ultra simple |
| iCloud / Dropbox / OneDrive | Inclus avec l'OS | Fonctionne mais l'UX en cas de conflit est hostile |
| Syncthing | Gratuit, auto-hébergé | Idéal pour le solo multi-appareils |
| Git (vault commité) | Gratuit | L'UX de conflit est la meilleure pour les utilisateurs techniques |
| LiveSync (plugin CouchDB) | Gratuit, requiert un serveur | Temps réel multi-appareils |

Les cinq sont compatibles avec le modèle de surcouches parce que Tesserae voit le vault comme des fichiers sur disque, pas comme un flux de mutations.

## Surface CLI

`tesserae project obsidian-sync` applique les éditions du vault sur le graphe typé et reprojette :

```bash
# Appliquer la surcouche une fois : récupère les éditions utilisateur, reprojette vers le vault.
tesserae project obsidian-sync

# Inspecte d'abord ce qui changerait. Écrit .tesserae/diverged-fields.md et
# N'applique PAS et ne reprojette pas.
tesserae project obsidian-sync --dry-run

# Cibler un vault précis pour cet appel (ordre de résolution :
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae project obsidian-sync --vault ~/Documents/tesserae-vault

# Faire de ce chemin de vault la valeur par défaut des futures commandes.
tesserae project obsidian-sync --vault ~/Documents/tesserae-vault --persist-vault

# Watch long-running : réapplique la surcouche à chaque changement du vault.
# Ctrl-C pour arrêter ; --poll-interval règle la cadence (par défaut 1,5 s).
tesserae project obsidian-sync --watch --poll-interval 1.5

# Supprime les pages projetées dont le nœud source n'existe plus (le projecteur
# ne fait qu'écraser, ne supprime jamais). Les pages avec user-notes sont
# conservées sauf si vous passez aussi --force-prune-with-notes.
tesserae project obsidian-sync --prune-orphans
tesserae project obsidian-sync --prune-orphans --force-prune-with-notes
```

La commande slash `/tesserae:obsidian-sync` l'enveloppe, et `tesserae project refresh`
(plus la macro `/tesserae:refresh`) exécute la surcouche comme dernière étape de sa
chaîne import → compile → sync.

## État de livraison

| Tier | Périmètre | Statut |
|---|---|---|
| **1a** | Lecteur de surcouches : parcourir le vault, construire `vault_overrides.json`, appliquer à la synchronisation. Les divergences atterrissent dans `.tesserae/diverged-fields.md`. | Livré |
| **1b** | Zones d'ajout user-notes : le projecteur ne touche jamais aux blocs `<!-- user-notes:start --> ... <!-- user-notes:end -->`. | Livré |
| **2** | Mode watch : un `obsidian-sync --watch` long-running rejoue la surcouche dans une boucle de sondage au fil des changements du vault. | Livré |
| **3** | Fédération multi-vaults : le graphe stocke la provenance par vault, prend en charge les éditions concurrentes à travers les vaults synchronisés. | Différé jusqu'à un cas d'usage réel |

## Non-objectifs (explicitement)

- Un serveur de sync / une authentification / un backend hébergé.
- L'édition collaborative en temps réel à l'intérieur d'Obsidian (utilisez LiveSync si vous en avez besoin).
- Réécrire l'extracteur pour faire un round-trip de chaque champ — la source markdown reste canonique pour tout ce qui se trouve en dehors de la table de surcharges.
- La synchronisation du site HTML statique (`build-site` reste exclusivement de la projection).

## Décisions tranchées

C'étaient les questions ouvertes au moment de la conception ; l'implémentation livrée de Tier 1–2 les a tranchées ainsi :

1. **Forme du lint report.** Les champs divergents apparaissent comme un fichier dédié `.tesserae/diverged-fields.md` (écrit par `--dry-run` et à chaque application) afin de pouvoir être diffé en git, plutôt que comme une section de `lint-report.md`.
2. **Type de nœud tombstone.** Ajouter `Stub` comme véritable type du schéma, ou se greffer sur `OpenQuestion` avec un discriminant `_kind: stub` ? Proposé : type réel, nommé `Stub`, masqué des index publics.
3. **Pull-on-compile par défaut.** Activé par défaut ou désactivé par défaut ? Proposé : activé lorsqu'un vault existe au chemin configuré, avec une invite de confirmation unique la première fois qu'il s'active, afin que les utilisateurs y consentent délibérément.
4. **Ce qui compte comme « la projection précédente » pour faire le diff.** Un snapshot stocké dans `.tesserae/vault_snapshot.json`, ou re-projeter à la volée à chaque compilation ? Proposé : snapshot, écrit à la fin de chaque compilation. Moins coûteux et évite que le non-déterminisme de l'extracteur ne fuite dans la surcouche.
5. **Projection vault multilingue.** La projection actuelle est monolingue (la source). Les surcouches devraient-elles être locale-aware (par ex. une modification de `description` dans un vault coréen ne s'applique qu'à la projection coréenne) ? Proposé : hors périmètre pour la v1 ; le vault est monolingue, correspondant à la langue principale du projet.

## Comment cela apparaît dans `obsidian.md`

Le guide destiné aux utilisateurs reste centré sur « vous pouvez lire et interroger le vault », puis renvoie ici pour l'histoire de l'aller-retour avec un résumé d'une ligne : « Modifiez des champs dans Obsidian, ils survivent à la recompilation. Voir [obsidian-sync.md](obsidian-sync.md) pour le modèle complet. »
