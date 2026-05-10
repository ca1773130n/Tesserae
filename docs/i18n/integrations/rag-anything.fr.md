# Compagnon multimodal RAG-Anything

<!-- translations:start -->
<p align="center"><a href="../../integrations/rag-anything.md">English</a> · <a href="rag-anything.ko.md">한국어</a> · <a href="rag-anything.zh.md">中文</a> · <a href="rag-anything.ja.md">日本語</a> · <a href="rag-anything.ru.md">Русский</a> · <a href="rag-anything.es.md">Español</a></p>
<!-- translations:end -->

[RAG-Anything](https://github.com/HKUDS/RAG-Anything) est un framework RAG multimodal (basé sur LightRAG) qui parse PDF, documents Office, images et équations via MinerU/Docling/PaddleOCR. LLM-Wiki l'intègre à la fois comme pipeline d'ingestion multimodale (projection native de graphe à la manière UA) et comme backend de mémoire d'exécution aux côtés de Cognee.

## Pourquoi utiliser les deux ?

- LLM-Wiki — mémoire d'agent durable, compilation wiki, projection de graphe.
- RAG-Anything — ingestion multimodale + récupération d'exécution LightRAG.

Les deux se complètent : RAG-Anything apporte la compréhension PDF/Office/images que les chargeurs de sources orientés texte de LLM-Wiki ne fournissent pas ; LLM-Wiki conserve la mémoire durable et interrogeable qui survit aux sessions.

## Workflow actuel à faible friction

Le chemin recommandé est l'assistant de configuration :

```bash
llm_wiki project setup
```

Pour l'automatisation :

```bash
llm_wiki project setup \
  --yes \
  --with-raganything \
  --install-raganything \
  --raganything-parser mineru \
  --run-raganything
llm_wiki project compile
```

LLM-Wiki stocke une commande de rafraîchissement gérée plutôt que de demander aux utilisateurs d'en inventer une :

```bash
llm_wiki project refresh-raganything --parser mineru
```

Pendant la compilation, LLM-Wiki :

1. vérifie si `.llm-wiki/external/raganything/manifest.json` existe et correspond au commit git courant (via le `meta.json#gitCommitHash` stocké) ;
2. exécute le wrapper de rafraîchissement géré s'il est manquant/périmé ou si `--refresh-external-tools` est passé ;
3. découvre les sources non-code (PDF, documents Office, images, markdown) et les parse via le parser configuré ;
4. écrit `manifest.json` + `meta.json` ;
5. poursuit la compilation normale de la mémoire.

Vous pouvez forcer toutes les commandes de rafraîchissement externes configurées avant une compilation :

```bash
llm_wiki project compile --refresh-external-tools
```

## Équivalent manuel

```bash
pip install 'raganything[all]'
python -m llm_wiki.raganything_refresh --project . --parser mineru
llm_wiki project compile
```

## Synchronisation native du graphe

LLM-Wiki importe nativement le manifest parsé pendant compile lorsque l'outil configuré utilise `sync_mode: native_graph`.

L'adaptateur natif lit `.llm-wiki/external/raganything/manifest.json`, projette chaque document parsé dans un `SourceFile` node avec des métadonnées de blocs multimodaux, et écrit un sync manifest :

```text
.llm-wiki/external/raganything-sync.json
```

Mapping actuel :

| RAG-Anything | Direction LLM-Wiki |
|---|---|
| `documents[*]` | `SourceFile` node, `metadata.parser="raganything"` |
| `content_list[type=text]` | replié dans `SourceFile.description` ; concepts via l'extracteur existant |
| `content_list[type=image]` | `SourceFile.metadata.multimodal_blocks[]` (`img_path`, `caption`) |
| `content_list[type=table]` | `SourceFile.metadata.multimodal_blocks[]` (`table_body`, `caption`) |
| `content_list[type=equation]` | `SourceFile.metadata.multimodal_blocks[]` et `metadata.equations[]` (LaTeX préservé) |

La provenance est préservée sur chaque nœud :

```json
{"system": "rag-anything", "id": "doc-<sha256>", "type": "document", "artifact": ".llm-wiki/external/raganything/manifest.json"}
```

## Backend de mémoire d'exécution

`memory_backends.raganything` (valeur par défaut produite par `default_raganything_backend_config`) coexiste avec Cognee. `project ask` essaie les backends dans l'ordre de priorité ; la priorité par projet peut être définie via `memory_backends.priority`. RAG-Anything est opt-in (par défaut `enabled: false`) ; le flag de configuration `--with-raganything` l'active.

## Prérequis système

- **Python 3.10+** (exigence de RAG-Anything ; LLM-Wiki lui-même cible 3.9+).
- **LibreOffice** pour parser `.doc/.docx/.ppt/.pptx/.xls/.xlsx` — installez-le séparément via le gestionnaire de paquets de votre plateforme. RAG-Anything ignore les documents Office avec un avertissement quand LibreOffice est manquant.
- **Les poids du modèle MinerU** sont téléchargés au premier parsing et mis en cache (~Go). Les exécutions suivantes réutilisent le cache.
- **Clés LLM/embedding/vision compatibles OpenAI** (`OPENAI_API_KEY`, `OPENAI_BASE_URL`) pour le backend de mémoire d'exécution. Le mode parser-only ne nécessite pas de clés.

## Principe de collaboration

LLM-Wiki reste le memory compiler. RAG-Anything reste un compagnon indépendant : parser multimodal + moteur de récupération LightRAG.
