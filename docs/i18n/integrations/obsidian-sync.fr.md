# Sync bidirectionnelle Obsidian — conception proposée

<!-- translations:start -->
<p align="center"><a href="../../integrations/obsidian-sync.md">English</a> · <a href="obsidian-sync.ko.md">한국어</a> · <a href="obsidian-sync.zh.md">中文</a> · <a href="obsidian-sync.ja.md">日本語</a> · <a href="obsidian-sync.ru.md">Русский</a> · <a href="obsidian-sync.es.md">Español</a> · <a href="obsidian-sync.de.md">Deutsch</a></p>
<!-- translations:end -->

> **Statut : livré (Tier 1, v0.5.0).** Le lecteur d’overlay, les zones d’ajout de notes utilisateur, le mode watch et l’élagage des orphelins décrits ci-dessous sont actifs derrière `tesserae vault sync`. Cette page sert à la fois de justification de conception et de guide utilisateur. La fédération multi-vaults (Tier 3) reste hors périmètre.

L’[export Obsidian](obsidian.fr.md) était auparavant strictement à sens unique : le graphe typé de `.tesserae/graph.json` se projette vers le vault, et `project compile` écrase les fichiers projetés. `obsidian-sync` ajoute la direction opposée — éditez une description dans Obsidian, et elle survit à la recompilation.

Ce document explicite comment cela fonctionne sans rendre le modèle de données incohérent.

## Le virage stratégique, énoncé clairement

Le README actuel décline l’édition en direct :

> Tesserae choisit compiler-depuis-la-source plutôt que l’édition en direct. Si vous voulez éditer des notes dans une UI, utilisez Logseq ou Obsidian.

La sync bidirectionnelle **change ce contrat** pour un sous-ensemble de champs. Cela mérite d’être délibéré. Le but n’est pas « Obsidian devient l’éditeur » — c’est « les éditions Obsidian de l’utilisateur ne sont pas silencieusement détruites à la recompilation ».

## L’idée centrale : des overlays, pas des merges

Plutôt que d’essayer de fusionner deux copies divergentes du même nœud, traiter le vault comme une **couche de diff** au-dessus de la projection :

```text
source markdown  ──extract──▶  base_graph
                                    +
                              vault_overrides     ◀── computed from vault
                                    ↓
                              final_graph  ──project──▶  vault (.md files)
```

`vault_overrides.json` vit dans `.tesserae/` et est **calculé**, pas écrit à la main. À chaque compilation, Tesserae parcourt le vault, compare chaque page projetée à ce que la projection précédente a écrit, et enregistre chaque changement introduit par l’utilisateur comme une entrée d’overlay. Le graphe final est `base_graph` avec les overlays appliqués. La projection suivante réécrit le résultat sur disque.

Stable en aller-retour. Recompiler le même vault sans changements côté source ne produit aucun diff.

## Propriété par champ

Chaque champ d’un nœud a un propriétaire. La propriété décide de ce qui se passe quand la source et le vault sont en désaccord.

| Champ | La source possède | Le vault peut surcharger | Notes |
|---|---|---|---|
| `id`, `type` | oui | non | Contrôlés par le schéma ; possédés par l’extracteur |
| `name` | initial | oui | L’utilisateur connaît souvent le nom canonique mieux que l’extracteur |
| `aliases` | initial | oui | Append-only depuis le vault ; les entrées du vault sont toujours préservées |
| `description` | initial | **oui** | L’édition Obsidian la plus courante |
| `source_path` | oui | non | Provenance ; ne peut pas être éditée |
| `metadata` (clés déclarées) | initial | oui | P. ex. `arxiv_id`, `github_repo` — l’utilisateur peut corriger |
| `metadata.user.*` | n/a | oui | Espace de noms réservé aux clés utilisateur ; l’extracteur n’y écrit jamais |
| Arêtes sortantes (typées) | oui | non | Les arêtes vivent dans l’ontologie, pas dans le vault |
| Nouveaux wikilinks tapés par l’utilisateur | n/a | oui | Exposés comme `edge_type=user_link`, écrits dans le graphe |
| Bloc de corps `<!-- user-notes -->` | jamais écrit | toujours préservé | Zone append-only que le projecteur ne touche jamais |

## Cas de conflit et défauts

| Cas | Défaut | Pourquoi |
|---|---|---|
| La `description` du vault diffère de la `description` re-extraite de la source | **Le vault gagne**, journalisé dans `.tesserae/lint-report.md` sous « diverged fields » | Respect des éditions utilisateur : l’utilisateur a clairement voulu l’édition. La piste d’audit permet de revoir plus tard. |
| Fichier source supprimé, page projetée toujours dans le vault | Retirer le nœud du graphe, lister dans `.tesserae/orphans.md` | La source fait autorité pour l’existence ; le journal des orphelins vous laisse décider de restaurer ou d’accepter |
| L’utilisateur a écrit un wikilink vers un slug qui n’existe pas | Créer un nœud pierre tombale (type `Stub`), le faire remonter dans le rapport de lint | Ne pas jeter l’intention de l’utilisateur ; la signaler pour nettoyage |
| L’utilisateur a ajouté une clé de frontmatter que le schéma ne connaît pas | Préserver comme `metadata.user.<key>`, ne jamais écraser | Compatible vers l’avant sans polluer le graphe typé |
| Deux vaults sur des machines différentes éditent le même nœud, tous deux synchronisés via Obsidian Sync | **Hors périmètre pour la v1.** Le dernier écrivain gagne au niveau du système de fichiers. | La vraie fédération multi-vaults est le Tier 3 ; différée jusqu’à un vrai cas d’usage |

## Zone d’ajout de notes utilisateur

Chaque page projetée reçoit une zone clôturée que le projecteur ne touche jamais :

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
1. Les utilisateurs peuvent annoter n’importe quelle page (p. ex. « voir le chapitre 4 de mes notes ») sans la perdre à la reconstruction.
2. La passe de pull scanne le bloc de notes utilisateur à la recherche de wikilinks et les expose comme des arêtes `user_link` typées par l’ontologie, leur donnant l’atteignabilité du graphe sans polluer les types d’arêtes formels.

## Transport distant — non-objectif explicite

Tesserae ne construit **pas** de serveur de sync, de couche d’auth, de daemon de résolution de conflits, ni de vault hébergé. « Bidirectionnel » signifie ici « la compilation lit depuis le vault » — comment le vault arrive à la machine qui compile est le problème de l’utilisateur, résolu par des outils qui existent déjà :

| Pile | Coût | Notes |
|---|---|---|
| Obsidian Sync | Payant, 4-8 $/mois | Chiffré de bout en bout, officiel, ultra simple |
| iCloud / Dropbox / OneDrive | Fourni avec l’OS | Fonctionne mais l’UX de conflit est hostile |
| Syncthing | Gratuit, auto-hébergé | Idéal pour le multi-appareils solo |
| Git (vault commité) | Gratuit | L’UX de conflit convient le mieux aux utilisateurs techniques |
| LiveSync (plugin CouchDB) | Gratuit, requiert un serveur | Multi-appareils en temps réel |

Les cinq sont compatibles avec le modèle d’overlay parce que Tesserae voit le vault comme des fichiers-sur-disque, pas comme un flux de mutations.

## Surface CLI

`tesserae vault sync` applique les éditions du vault sur le graphe typé et re-projette :

```bash
# Apply the overlay once: pull user edits, re-project to the vault.
tesserae vault sync

# Inspect what would change first. Writes .tesserae/diverged-fields.md and
# does NOT apply or re-project.
tesserae vault sync --dry-run

# Point at a specific vault for this call (resolution order:
# --vault > config.obsidian.vault_path > .tesserae/obsidian_vault/).
tesserae vault sync --vault ~/Documents/tesserae-vault

# Make that vault path the default for future commands.
tesserae vault sync --vault ~/Documents/tesserae-vault --persist-vault

# Long-running watch: re-apply the overlay every time the vault changes.
# Ctrl-C to stop; tune the poll cadence with --interval (default 1.5s).
tesserae vault sync --watch --interval 1.5

# Delete projected pages whose source node no longer exists (the projector
# only overwrites, never deletes). Pages with user-notes are kept unless you
# also pass --force-prune-with-notes.
tesserae vault sync --prune-orphans
tesserae vault sync --prune-orphans --force-prune-with-notes
```

La commande slash `/tesserae:obsidian-sync` l’enrobe, et `tesserae refresh`
(plus la macro `/tesserae:refresh`) exécute l’overlay comme dernière étape de sa
chaîne import → compile → sync.

## Statut de livraison

| Tier | Périmètre | Statut |
|---|---|---|
| **1a** | Lecteur d’overlay : parcourir le vault, construire `vault_overrides.json`, appliquer à la sync. Les divergences atterrissent dans `.tesserae/diverged-fields.md`. | Livré |
| **1b** | Zones d’ajout de notes utilisateur : le projecteur ne touche jamais les blocs `<!-- user-notes:start --> ... <!-- user-notes:end -->`. | Livré |
| **2** | Mode watch : `obsidian-sync --watch` de longue durée relance l’overlay dans une boucle de sondage à mesure que le vault change. | Livré |
| **3** | Fédération multi-vaults : le graphe stocke la provenance par vault, supporte les éditions concurrentes entre vaults synchronisés. | Différé jusqu’à un vrai cas d’usage |

## Non-objectifs (explicitement)

- Un serveur de sync / auth / backend hébergé.
- L’édition collaborative en temps réel dans Obsidian (utilisez LiveSync si vous en avez besoin).
- Réécrire l’extracteur pour faire l’aller-retour de chaque champ — le markdown source reste canonique pour tout ce qui est hors de la table d’override.
- La sync du site HTML statique (`build-site` reste projection seulement).

## Décisions tranchées

C’étaient les questions ouvertes au moment de la conception ; l’implémentation livrée des Tiers 1–2 les a tranchées ainsi :

1. **Forme du rapport de lint.** Les champs divergents apparaissent dans un fichier dédié `.tesserae/diverged-fields.md` (écrit par `--dry-run` et à chaque application) pour pouvoir être diffé dans git, plutôt que comme une section de `lint-report.md`.
2. **Type de nœud pierre tombale.** Ajouter `Stub` comme un vrai type de schéma, ou s’appuyer sur `OpenQuestion` avec un discriminateur `_kind: stub` ? Proposé : un vrai type, nommé `Stub`, caché des index publics.
3. **Pull-à-la-compilation par défaut.** ON ou OFF par défaut ? Proposé : ON quand un vault existe au chemin configuré, avec une invite de confirmation unique à la première activation pour que les utilisateurs optent délibérément.
4. **Qu’est-ce qui compte comme « la projection précédente » pour le diff ?** Snapshot stocké dans `.tesserae/vault_snapshot.json`, ou re-projeter à la volée à chaque compilation ? Proposé : snapshot, écrit à la fin de chaque compilation. Moins cher et évite que le non-déterminisme de l’extracteur ne fuie dans l’overlay.
5. **Projection de vault multilingue.** La projection actuelle est monolingue (la source). Les overlays devraient-ils être conscients de la locale (p. ex. une édition de `description` dans un vault coréen ne s’applique qu’à la projection coréenne) ? Proposé : hors périmètre pour la v1 ; le vault est monolingue, aligné sur la langue principale du projet.

## Comment cela apparaît dans `obsidian.md`

Le guide côté utilisateur reste centré sur « vous pouvez lire et interroger le vault », puis lie ici pour l’histoire de l’aller-retour avec un résumé d’une ligne : « Éditez des champs dans Obsidian, ils survivent à la recompilation. Voir [obsidian-sync.md](obsidian-sync.fr.md) pour le modèle complet. »
