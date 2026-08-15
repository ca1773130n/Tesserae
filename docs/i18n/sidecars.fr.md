# `.tesserae/` — ce qu'il contient, et ce que coûte une suppression

<!-- translations:start -->
<p align="center"><a href="../sidecars.md">English</a> · <a href="sidecars.ko.md">한국어</a> · <a href="sidecars.zh.md">中文</a> · <a href="sidecars.ja.md">日本語</a> · <a href="sidecars.ru.md">Русский</a> · <a href="sidecars.es.md">Español</a> · <a href="sidecars.fr.md">Français</a> · <a href="sidecars.de.md">Deutsch</a></p>
<!-- translations:end -->
Un projet mature accumule une soixantaine d'entrées sous `.tesserae/`, et un
listing de répertoire ne dit rien de ce qu'une compilation reconstruit
gratuitement, de ce qui coûte une passe de LLM, et de ce qui porte un travail
que rien ne peut reconstituer. `compile.lock` et un fichier tmp orphelin de zéro
octet ressemblent exactement à `candidate-same-as.json`, qui porte des verdicts
humains.

Cette page est cette réponse, ordonnée par conséquence. La classification
elle-même vit dans `tesserae/sidecars.py` — une entrée de registre par fichier,
chacune consignant son propriétaire, son genre et ce qui est perdu en le
supprimant. Le registre fait foi ; cette page en est la projection lisible, et
`tesserae doctor` affiche la version vivante.

Chaque entrée porte deux champs indépendants :

| Genre | D'où viennent les octets |
|---|---|
| `derived` | republié par une compilation à partir des sources |
| `accumulated` | s'accumule dans le temps ; aucune compilation ne le redérive |
| `cache` | une réponse stockée à une question que l'on peut reposer |
| `scratch` | intendance de processus : verrous, pidfiles, débris tmp |

Le genre dit d'où viennent les octets. Il ne dit **pas** si la suppression est
sûre : `safe_to_delete` est un champ distinct, et les deux divergent assez
souvent pour que cela compte. Un `cache` dont la réponse vient d'un modèle n'est
pas sûr à supprimer, et un fichier `derived` peut porter des approbations
humaines. Les sections ci-dessous sont ordonnées par ce second champ, car c'est
celui qui vous intéresse vraiment.

## Récupérable sans risque — une compilation les refait

Supprimez n'importe lequel de ceux-ci et la compilation suivante le remet en
place, octet pour octet, sans appeler le moindre modèle :

<!-- sidecars:safe-list -->
`agent_harness` · `code-graph.json` · `combined-graph.json` ·
`competitive_report.md` · `diverged-fields.md` · `doctor-report.json` ·
`doctor-report.md` · `graph.json` · `graph.kuzu` · `graphiti_episodes.jsonl` ·
`hierarchy.json` · `lint-report.json` · `lint-report.md` · `log.md` ·
`markdown_projection` · `merge-ledger.json` · `okf` ·
`okf-imported.graph.json` · `output-snapshot.json` · `report.md` · `research` ·
`schema-drift.md` · `site` · `summaries` · `temporal_facts.jsonl` · `wiki` ·
`.watch-cache.json` · `arxiv-cache.json` · `code-graph-cache.json` · `external`

`graph.json` figure dans cette liste à dessein. Le graphe compilé est une
fonction pure des sources et des sidecars accumulés ci-dessous — c'est
précisément pour cela que ce sont *ceux-là* qu'il faut protéger, et que le
réflexe « je supprime `.tesserae/` et je recompile » est faux, même si le
fichier le plus visible du répertoire est bien jetable.

## Coûte une passe de modèle — et change les octets de `graph.json`

Ce sont des réponses de LLM stockées. Les reconstruire coûte une passe, et le
modèle ne rend jamais deux fois les mêmes mots : tout ce qui en dépend change
donc d'octets aussi.

| Entrée | Genre | Ce que coûte une reconstruction |
|---|---|---|
| `session_findings` | `cache` | le cas le plus tranchant : ces findings deviennent des **nœuds** du graphe, donc perdre le cache relance un extracteur non déterministe et le `graph.json` suivant diffère en octets — la rupture d'idempotence octet à octet que ce dépôt a déjà encaissée quatre fois |
| `community_summaries` | `cache` | résumés de communauté écrits par un LLM, indexés sur le hash des membres |
| `distill_cache` | `cache` | résultats de distillation d'agents |
| `distillation_cache` | `cache` | résultats de distillation |
| `extraction_guidance_cache` | `cache` | une puce formulée par le LLM par grappe de retours |
| `schema_drift_cache` | `cache` | propositions de sous-types du LLM par type hôte |
| `supersede_cache` | `cache` | arbitrage de remplacement (supersede) par le LLM |
| `schema-drift-proposals.json` | `derived` | octets dérivés, contenu non dérivable : l'enregistrement porte la barrière humaine `approved` et un `proposed_type` éditable, donc une reconstruction coûte une passe **et** jette les approbations |

## Irrécupérable — rien ne reconstruit ceci

Aucune compilation ne redérive quoi que ce soit ici. En supprimer un est une
perte de données, pas un délai.

| Entrée | Genre | Ce que vous perdez |
|---|---|---|
| `candidate-same-as.json` | `accumulated` | les verdicts same-as humains. Une compilation qui ne le trouve pas n'échoue pas : elle repose silencieusement une question déjà tranchée par un humain, et une paire rejetée revient non rejetée |
| `sqlite.db` | `accumulated` | mixte ; voir ci-dessous |
| `agent-writes.jsonl` | `accumulated` | la couche écrite par les agents, rejouée comme cinquième producteur à chaque compilation ; la supprimer efface chaque écriture d'agent |
| `vault_snapshot.json` | `accumulated` | la base de référence à laquelle `vault_pull` se compare. La supprimer en pleine édition empêche la compilation suivante de distinguer votre modification de sa propre projection antérieure — tout le mécanisme de surcharge du vault |
| `obsidian_vault` | `accumulated` | bidirectionnel et détenu par l'utilisateur : vos modifications y sont retirées vers le graphe, ce n'est donc pas une projection que l'on redessine |
| `config.json` | `accumulated` | configuration du projet, y compris `obsidian.vault_path` — saisie utilisateur, jamais regénérée |
| `charter` | `derived` | chaque compilation le dérive de `graph.json`, mais aucune reconstruction ne le reproduit : les slugs sont frappés à partir des ancres que la reconstruction choisit, donc le supprimer refonde chaque domaine sous un nouveau nom, casse tout chemin d'attache épinglé et jette les pierres tombales qui seules disaient où étaient passés les anciens noms |
| `agents` | `accumulated` | le `registry.json` par agent et le `purpose.md` écrit à la main |
| `discovered_links.json` | `accumulated` | la couche d'association accumule des liens notés au fil des exécutions ; une seule ne la reconstruit pas |
| `extraction-feedback.jsonl` | `accumulated` | corrections humaines recueillies lors de la superposition du vault et de review-apply |
| `extraction-guidance.md` | `accumulated` | guidance éditée à la main, dans laquelle une passe evolve fusionne |
| `harness_sessions` | `accumulated` | état des sessions importées |
| `harness_sessions.db` | `accumulated` | sessions d'agents importées, dont les transcriptions amont tournent et disparaissent : une réimportation ne les reconstitue pas |
| `session_chunks.db` | `accumulated` | tours normalisés écrits en direct par le tailer du démon, depuis des transcriptions qui ne restent pas disponibles |
| `manifest.json` | `accumulated` | état d'ingestion par source ; sans lui, le lot suivant réingère tout et relance l'extraction sur des sources déjà lues |
| `.build-history.jsonl` | `accumulated` | une ligne par build avec le `git_head` de la compilation ; le supprimer rend la fraîcheur du graphe définitivement inconnaissable |

### `sqlite.db` est mixte, et classé d'après sa table la plus précieuse

Le miroir du graphe qu'il contient est dérivé et `node_vectors` est un cache
vectoriel jetable — mais le même fichier porte `node_memory` (décroissance,
compteurs d'accès, confiance renforcée), `fact_observed` (temps de transaction,
une véritable horloge qui n'avance que dans un sens) et `read_audit`, dont rien
n'est récupérable. Supprimer le fichier pour récupérer le cache vectoriel
réinitialise à maintenant le « quand l'a-t-on appris » de chaque fait. Récupérez
de l'espace avec `tesserae doctor --fix`, qui fait un vacuum, plutôt qu'en
supprimant la base.

## Verrous, pidfiles et débris

| Entrée | Genre | Avant de supprimer |
|---|---|---|
| `compile.lock` | `scratch` | le mutex de compilation. **Jamais** supprimé par une voie automatique — le mode de défaillance consigné est l'empilement des compilations à SessionEnd, et le contrôle `compile_lock` de doctor est en lecture seule pour la même raison |
| `.recompile.lock.d` | `scratch` | mutex de hook basé sur mkdir ; en supprimer un qui est tenu laisse deux recompilations entrer en course |
| `session_chunks.lock` | `scratch` | le flock « passer si tenu » du backfill ; en supprimer un qui est tenu laisse deux backfills écrire la même journée |
| `daemon*.pid` | `scratch` | pidfile du moteur, à portée d'hôte sous la forme `daemon.<host>.pid`. Doctor n'en supprime un qu'après avoir confirmé que le propriétaire enregistré est mort **sur cette machine** |
| `graph.json.bak-*` | `scratch` | aucun chemin de code Tesserae ne les écrit. Ce sont des copies faites à la main lors d'une restauration : signalées, jamais supprimées, parce qu'un humain les a faites |
| `*.tmp*` | `scratch` | la moitié orpheline d'une écriture tmp+replace, nommée `<target>.tmp.<pid>.<hex>`. Supprimable seulement une fois le pid propriétaire disparu : un écrivain vivant est au milieu de son rename |
| `.*-hook.log*` | `scratch` | diagnostics des hooks shell ; doctor fait tourner ceux qui grossissent trop |

## `~/.tesserae/` — à l'échelle de la machine, même nom de répertoire

Le répertoire de portée utilisateur porte le même nom que celui du projet et
signifie autre chose. `config.json` existe dans les deux : dans le projet, c'est
la configuration du projet ; ici, c'est la configuration LLM de tous les projets
de la machine.

| Entrée | Genre | Ce que vous perdez |
|---|---|---|
| `registry.json` | `accumulated` | le registre des projets. Le supprimer désenregistre tous les projets de cette machine |
| `config.json` | `accumulated` | configuration LLM pour toute la machine ; saisie utilisateur |
| `host_id` | `accumulated` | l'identité de cette machine. La regénérer fait paraître étranger chaque pidfile à portée d'hôte et chaque enregistrement de session sur un stockage partagé |
| `harness_sessions` | `accumulated` | état d'import de sessions pour toute la machine |
| `llm_cache` | `cache` | réponses de LLM mises en cache ; une reconstruction appelle les modèles et ne les reproduit pas |
| `federation` | `cache` | caches de liens et de vecteurs inter-projets — supprimables sans risque |
| `wiki` | `derived` | scratch de serve à portée machine — supprimable sans risque |
| `engine.pid` | `scratch` | pidfile de la flotte ; un fichier périmé a un jour tenu un pid mort depuis six jours, d'où le fait que pidlock valide au lieu de faire confiance |
| `engine.pid.lock` | `scratch` | mutex du pidfile de la flotte ; en supprimer un qui est tenu laisse deux flottes démarrer |
| `*.bak*` | `scratch` | copies de `registry.json` et `config.json` d'avant migration. Aucun chemin de code ne les écrit : elles existent parce que quelqu'un a voulu les garder |

## Voir la classification vivante

```bash
tesserae doctor          # the `sidecars` check, under hygiene
tesserae doctor --fix    # removes orphaned tmp halves, nothing else
```

Le contrôle `sidecars` lit votre `.tesserae/` réel face au registre et signale
trois populations séparément : les moitiés tmp orphelines, les copies
`graph.json.bak-*` faites à la main, et les entrées qu'aucune entrée du registre
ne revendique. `--fix` ne supprime que les premières, et seulement lorsque le pid
de l'écrivain est mort et que le fichier a plus de 24 heures — parce qu'un
écrivain vivant se trouve entre `write_text` et `replace`, et que
`os.kill(pid, 0)` ne répond que pour la table des processus locale alors que
plusieurs hôtes peuvent monter un même `.tesserae/`.

**Les entrées non classées sont signalées et jamais touchées.** Une entrée que le
registre ne revendique pas est plus probablement le fichier de quelqu'un d'autre
— vos notes, le cache d'un autre outil — qu'un bug de Tesserae ; la bonne réponse
quand on en trouve une est de la nommer, pas de la supprimer. C'est aussi ainsi
qu'un nouveau sidecar Tesserae oublié à l'enregistrement devient visible.

Tesserae ne livre aucun verbe `reset` global. La classification est ce qui
rendrait une telle commande possible ; écrire la classification et livrer dans le
même geste une commande destructrice qui s'appuie dessus, c'est l'ordre inverse
du bon.
