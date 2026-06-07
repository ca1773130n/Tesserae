# Understand-Anything: modo solo grafo de código

<!-- translations:start -->
<p align="center"><a href="../../integrations/understand-anything-code-only.md">English</a> · <a href="understand-anything-code-only.ko.md">한국어</a> · <a href="understand-anything-code-only.zh.md">中文</a> · <a href="understand-anything-code-only.ja.md">日本語</a> · <a href="understand-anything-code-only.ru.md">Русский</a> · <a href="understand-anything-code-only.fr.md">Français</a> · <a href="understand-anything-code-only.de.md">Deutsch</a></p>
<!-- translations:end -->

Este documento es una continuación de [understand-anything.md](../../integrations/understand-anything.md). El documento base explica cómo instalar y habilitar [Understand-Anything](https://github.com/Lum1104/Understand-Anything) (UA) como acompañante que produce un grafo de código en `.understand-anything/knowledge-graph.json`. **Este documento explica cómo hacer que UA aporte ÚNICAMENTE un grafo de código y nunca contamine la capa Concept del grafo de investigación de Tesserae con encabezados de sección extraídos de tus documentos.**

Si alguna vez abriste el grafo tipado tras habilitar UA y encontraste la capa Concept llena de cosas como `'Quickstart'`, `'2) Paste it into your MCP client'`, o el mismo encabezado en siete idiomas, te topaste con el problema que este documento resuelve.

## Por qué ocurre esto

Dos capas del mismo error se acumulan:

1. **UA recorre tus documentos por defecto.** De fábrica, el cargador de fuentes de UA recorre cada archivo legible bajo la raíz de tu proyecto, incluidos `docs/`, `docs/i18n/`, READMEs en cada idioma, etc. Por cada encabezado markdown que ve, registra un nodo en `.understand-anything/knowledge-graph.json` usando el texto del encabezado como nombre de entidad.
2. **Tesserae fusiona el grafo completo de UA de forma nativa.** Cuando `external_tools` lista UA con `sync_mode: "native_graph"`, `ProjectWiki._merge_configured_understand_anything_graph()` lee el artefacto e importa cada nodo de UA al grafo de investigación como un `Concept`. La intención "esto es un símbolo de código" de UA se aplana a "esto es un concepto de investigación", y tus nodos provenientes de encabezados de documentos viajan con ellos.

Efecto neto: cada encabezado traducido aparece como un Concept duplicado (`'Setup'`, `'설정'`, `'安装'`, `'インストール'`, `'Установка'`, `'Configuración'`, `'Configuration'`, `'Einrichtung'`), generando colisiones de slug que el proyector renombra como `setup-2.md`, `setup-3.md`, …, `setup-7.md`.

> [!warning] Lo reconocerás cuando lo veas
> Comprobación de síntomas en un proyecto donde ha ocurrido esto:
> ```bash
> .venv/bin/python -c "
> import json
> from collections import Counter
> nodes = json.load(open('.tesserae/graph.json'))['nodes']
> srcs = Counter(n.get('source_path','') for n in nodes if n['type']=='Concept')
> print(srcs.most_common(3))
> "
> ```
> Si la fuente principal es `.understand-anything/knowledge-graph.json` con cientos de nodos Concept, cada encabezado traducido que tengas está siendo importado como un concepto separado.

## Solución en tres pasos

### Paso 1 — detener la importación de UA como Concepts desde el lado de Tesserae

Edita `.tesserae/config.json` y establece tanto `enabled: false` como `sync_mode: "disabled"` en la entrada de la herramienta UA. La ruta de código de fusión comprueba ambas banderas a modo de cinturón y tirantes:

```jsonc
{
  "external_tools": [
    {
      "id": "understand-anything",
      "enabled": false,            // ← antes era true
      "sync_mode": "disabled",     // ← antes era "native_graph"
      "auto_refresh": false,       // opcional: deja de refrescar UA en cada compilación
      // ...el resto de la entrada se queda igual
    }
  ]
}
```

`enabled: false` hace que `_merge_configured_understand_anything_graph()` omita la herramienta por completo. `sync_mode: "disabled"` es una salvaguarda secundaria por si un bug futuro ignora la bandera `enabled`.

### Paso 2 — eliminar los artefactos obsoletos para no dejar nada atrás

Si previamente ejecutaste una compilación con UA habilitado, los artefactos contaminados siguen en disco:

```bash
rm -f .understand-anything/knowledge-graph.json
rm -f .tesserae/external/understand-anything.md
```

Tesserae regenera `.tesserae/external/understand-anything.md` solo cuando la herramienta está habilitada, por lo que eliminarlo es seguro una vez que el Paso 1 está en su lugar.

### Paso 3 — recompilar + podar el vault de Obsidian

```bash
tesserae compile
tesserae vault sync --prune-orphans
```

La compilación omitirá la fusión de UA, dejando el grafo de investigación libre de Concepts provenientes de UA. El paso de poda elimina cualquier página huérfana en el vault de Obsidian que apuntara a node_ids creados por la fusión.

## Verificación

Tras la recompilación, el script de auditoría anterior debería reportar cero (o casi cero) nodos Concept con origen en `.understand-anything/knowledge-graph.json`. Una segunda comprobación útil:

```bash
.venv/bin/python -c "
import json, re
from collections import defaultdict
nodes = json.load(open('.tesserae/graph.json'))['nodes']
concepts = [n for n in nodes if n['type']=='Concept']
def slug(s): return re.sub(r'[^a-z0-9가-힣]+','-',s.lower()).strip('-')
buckets = defaultdict(list)
for n in concepts: buckets[slug(n['name'])].append(n)
collisions = {s: ns for s, ns in buckets.items() if len(ns)>1}
print(f'{len(collisions)} Concept slug collision(s), {sum(len(ns)-1 for ns in collisions.values())} duplicate page(s)')
"
```

Debería imprimir `0 Concept slug collision(s), 0 duplicate page(s)` si la corrección surtió efecto.

## Cuando realmente quieras recuperar la navegación por grafo de código

El grafo de código de UA es genuinamente útil —aristas de llamada/importación, jerarquías de clases, etc.— cuando no está ahogado en ruido de encabezados de documentos. Para volver a habilitarlo correctamente:

1. **Acota a UA mismo al código, no a los documentos.** UA acepta patrones de inclusión/exclusión; configúralo para recorrer solo `src/`, `lib/`, `tesserae/`, etc. y excluir explícitamente `docs/`, `README*.md` y `docs/i18n/`. La perilla de configuración exacta vive en la propia documentación de UA en [Lum1104/Understand-Anything](https://github.com/Lum1104/Understand-Anything).
2. **Vuelve a habilitar en `.tesserae/config.json`**: cambia `enabled` de nuevo a `true`, `sync_mode` de nuevo a `"native_graph"`, `auto_refresh` de nuevo a `true`.
3. **Recompila** y vuelve a ejecutar la auditoría. Una ejecución limpia de UA debería producir Concepts que mapean a símbolos de código reales (nombres de función, nombres de clase, módulos) en lugar de encabezados de sección en inglés.

La asimetría escuece —deshabilitar es un solo cambio de configuración, rehabilitar limpiamente requiere entender el alcance de fuentes de UA—, pero es el límite correcto. El trabajo de UA son los grafos de código, el trabajo de Tesserae son los grafos de investigación, y la costura entre ambos nunca debería permitir que los encabezados de documentos crucen de un lado al otro.

## Dónde encaja esto

| Capa | Preocupación | Configurada vía |
|---|---|---|
| El propio walker de UA | Qué archivos lee UA en primer lugar | configuración de UA (fuera del alcance de Tesserae) |
| `auto_refresh` en la herramienta UA | Si `tesserae compile` vuelve a ejecutar UA | entrada de external_tools en `.tesserae/config.json` |
| `enabled` en la herramienta UA | Si Tesserae considera UA en absoluto | entrada de external_tools en `.tesserae/config.json` |
| `sync_mode` en la herramienta UA | Si los nodos de UA se fusionan en el grafo de investigación | entrada de external_tools en `.tesserae/config.json` |

Las perillas `enabled` + `sync_mode` son la costura entre ambos proyectos. Las perillas walker + `auto_refresh` son preocupaciones internas de UA.
