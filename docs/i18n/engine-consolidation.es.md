# Consolidación automática — el ciclo de sueño del motor

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

El cerebro consolida la memoria durante el descanso. Mientras duermes, la experiencia bruta del día se reorganiza, se comprime e integra — lo reciente y ruidoso se pliega en una estructura duradera. El motor de Tesserae hace lo mismo. Cuando un proyecto se **inactiva**, el demonio siempre encendido deja de esperar la siguiente edición y pasa el tiempo reorganizando lo que ya sabe: **comprime y olvida** la memoria reciente ruidosa, permite que el conocimiento que nadie ha recuperado **se desvanezca por desuso**, y **descubre nuevas conexiones** entre lo que sobrevive.

Hasta ahora, ese paso solo se ejecutaba cuando lo solicitabas — `tesserae refresh` bajo presión de memoria, o un `tesserae distill` explícito. El motor recompilaba en cada archivo y evento de sesión, pero nunca se consolidaba automáticamente. El **ciclo de sueño** cierra esa brecha: deja `tesserae engine` en funcionamiento y la consolidación ocurre durante el descanso, sin ningún comando que recordar.

Como todo en el sistema de [memoria en capas](agent-memory.es.md), esto es **no-op a menos que optes** — el demonio consolida en inactividad, pero la destilación debajo solo funciona cuando se establece `TESSERAE_AGENT_DISTILL`.

## Cuándo se dispara

Un hilo de consolidación dedicado se despierta en un **intervalo de verificación** fijo (por defecto 30 segundos) y evalúa dos desencadenantes independientes contra un reloj de actividad monótono:

- **Disparador de inactividad.** El proyecto no ha visto un evento de disparo ni una ejecución de canalización durante al menos `--consolidate-idle` segundos (por defecto **300s = 5 min**). Este es el caso de "consolidación durante el descanso" — el motor notó que dejaste de trabajar y utilizó la pausa. Un **piso** desde la última consolidación previene cambios bruscos, por lo que un proyecto ocupado que acaba de quedarse tranquilo no se consolida en un disparo sensible.
- **Disparador de techo.** Han transcurrido al menos `--consolidate-every` segundos desde la última consolidación, **independientemente de la actividad** (por defecto **21600s = 6h**). Esto garantiza que un proyecto continuamente ocupado aún se consolide periódicamente en lugar de nunca tener un momento tranquilo. Configurarlo a `0` desactiva el techo — entonces la inactividad es el único disparador.

Cada edición, turno de sesión o recompilación aumenta el reloj de actividad, por lo que la ventana de inactividad solo transcurre durante el descanso genuino. Ambos relojes son **monótonos**, nunca reloj de pared, y nunca se persisten en ningún artefacto — el tiempo de consolidación nunca puede perturbar el gráfico byte-determinista.

## Qué se ejecuta — cinco operaciones

Cada disparo carga el gráfico compilado desde `.tesserae/graph.json` (si el archivo está ausente, el paso se omite) y ejecuta cinco operaciones de consolidación, en orden. Juntos reflejan lo que hace un cerebro descansado: comprime lo reciente ruidoso, deja que lo nunca revisitado se desvanezca, crea nuevas asociaciones entre lo que sobrevive, y ensaya — dedica un pequeño esfuerzo ahora, mientras nadie está esperando, en las descripciones que un lector querrá después.

### 1. Comprimir / olvidar — destilación

Llama al mismo punto de entrada `maybe_distill_on_refresh` que usa `tesserae refresh` para reorganizar, comprimir y olvidar de manera segura la memoria de cada agente. Esa función es **triple compuerta** internamente y nunca se eleva por falla por agente:

1. **Puerta de inclusión** — `TESSERAE_AGENT_DISTILL=1` (o `{"agent_distill": {"enabled": true}}` en `config.json`). Desactivado por defecto; todo el ciclo es un no-op seguro hasta que lo configures.
2. **Marca de agua por agente** — un agente cuyas conclusiones no han cambiado desde su última destilación se omite.
3. **Presión de memoria por agente** — solo los agentes cuyas conclusiones no destiladas ya no caben en la mitad de una lectura de contexto se consolidan (disparador estilo MemGPT).

Entonces, incluso cuando la consolidación se *dispara* en un cronograma, solo *funciona* para los agentes que se incluyeron y realmente acumularon suficiente memoria nueva para justificarlo. Consulta [Memoria de agente en capas](agent-memory.es.md) para lo que produce la destilación.

### 2. Olvidar por desuso — decaimiento LRU en recuperación, no solo por edad

El decaimiento de la destilación ya no se impulsa solo por la edad de creación. Cada superficie de lectura registra el acceso a las conclusiones que devuelve — `last_accessed_at` y `access_count` — en un **sidecar `node_memory`**, nunca en `graph.json`. Antes de que el paso de destilación calcule el decaimiento, fusiona ese estado de acceso en vivo en su vista de trabajo, por lo que una conclusión que no se ha recuperado desde que se acuñó decae y se vuelve elegible para ser absorbida o degradada, mientras que una que fue leída recientemente se mantiene fresca sin importar su edad. Esto es **recencia de recuperación**, la intuición LRU (menos recientemente usada) aplicada a la memoria: el conocimiento que sigues extrayendo permanece; el conocimiento que nadie pide se desvanece primero. Un sidecar vacío reproduce el antiguo comportamiento solo de edad exactamente, por lo que es completamente compatible hacia atrás.

### 3. Asociar — descubrir nuevas conexiones

La operación final busca *nuevas* relaciones entre lo que sobrevivió. Incrusta notas destiladas y vincula pares cuyos significados están cerca — **compuerta de incrustación**, por lo que solo se ejecuta cuando se configura un backend de incrustación real (el stub hash se omite, nunca produciendo enlaces ruidosos). El descubrimiento se ejecuta dentro del proyecto y **entre agentes**, y las conexiones que encuentra se acuñan como bordes `shares_concept_with` que llevan un marcador `federation_semantic`.

Crucialmente, estos bordes descubiertos se escriben en una **superposición de sidecar** bajo `.tesserae`, *nunca* en `graph.json`. La superposición **acumula entre ciclos** — cada paso de asociación deduplica contra y extiende lo que encontraron los pasos anteriores. En tiempo de lectura (consulta, expansión PPR, vistas de federación) la superposición se fusiona en el gráfico **en memoria solo**, exactamente como la superposición de vista por agente — por lo que el `graph.json` byte-determinista nunca se toca. Toda la operación se envuelve y nunca se eleva al bucle del demonio.

### 4. Resumir — pre-calentar los cachés de resumen de comunidad en los que descienden los agentes

`graph_map` sirve una tarjeta por alcance. Un alcance cuyo caché de resumen está frío obtiene una tarjeta *estructural* determinista — un recuento de miembros y una lista de los mejores miembros — y el primer agente que lo visita paga una llamada LLM síncrona para obtener prosa. Esta operación traslada ese costo fuera de la ruta de lectura: dentro de un presupuesto por tick (`--summarize-budget`, predeterminado 25; `0` lo desactiva) materializa resúmenes para los alcances más probables de ser visitados a continuación, para que la visita encuentre un caché caliente.

Los candidatos se clasifican por **demanda** — los propios incrementos de acceso del alcance de la travesía `graph_map` más los conteos de acceso de sus miembros — luego por tamaño, grado y nivel, en un orden total, por lo que dos ticks sobre estado idéntico eligen los mismos alcances. Un caché que ya está caliente y aún tiene digest válido no cuesta presupuesto; solo una materialización fría lo hace. Sin un cliente LLM, toda la operación es una operación sin cambios.

### 5. Breve — pre-calentar los resúmenes de dominio del acta

La misma forma, un eje sobre: los candidatos son los dominios activos de [la acta](../README.md) en lugar de las comunidades del dendrograma. Un dominio frío se representa como una tarjeta estructural dondequiera que aparezca — en `graph_map`, en el corpus de puntuación de `charter_route`, y en el censo `CHARTER_FALLBACK` de lint — así que este pase es lo que le da a la institución estatutaria prosa en definitiva.

El presupuesto es su propio control (`--brief-budget`, predeterminado 8; `0` lo desactiva), deliberadamente separado de `--summarize-budget` para que ninguna operación pueda agotar la otra, y deliberadamente más pequeño: las **divisiones** del acta son lo que `graph_map` sirve como su conjunto de tarjeta raíz, y hay solo un puñado de ellas, así que 8 calienta el punto de entrada en el primer tick inactivo y los niveles más profundos lo siguen a 8 por tick detrás.

El orden es **primero en amplitud**, no una clasificación por demanda. El conjunto de miembros de un dominio contiene su subárbol completo, por lo que la demanda de un padre siempre domina la de sus hijos y ningún dominio se calienta antes de sus ancestros. Eso es deliberado: los agentes descienden desde la raíz, por lo que la tarjeta gruesa es la que se lee primero y la que vale la pena tener prosa para. Los conteos de acceso ordenan dominios donde ninguno contiene al otro, y las **divisiones** activas — dominios sin padre activo, la misma regla que usa `graph_map` en su raíz, no `tier == 1` — se clasifican por delante de todo lo demás.

Algunos dominios nunca cuestan un espacio de presupuesto, porque un espacio está destinado a ser una llamada LLM: dominios retirados, el censo `intake` (que no tiene sujeto, así que un resumen escrito desde 25 de sus miles de miembros sería una descripción confiada de una fracción de un por ciento), un dominio cuyos miembros han dejado el gráfico, y cualquier cosa ya caliente. Y un dominio cuya materialización **falla** — más a menudo porque su prosa no citó ninguno de sus hijos y fue rechazada — se mantiene alejado por un número duplicador de ticks en lugar de reintentarse en el mismo rango para siempre, así que un dominio permanentemente que no se puede calentar no puede mantener un espacio que uno calentable pudiera usar.

### Qué cuesta esto por hora

Ambos presupuestos son por **tick**, y un tick se dispara como máximo una vez por ventana `--consolidate-idle`. En los valores predeterminados:

| | por tick | ticks/hora en `--consolidate-idle 300` | techo |
|---|---|---|---|
| Resumir | 25 | 12 | 300 llamadas LLM/hora |
| Breve | 8 | 12 | 96 llamadas LLM/hora |
| **Total** | **33** | **12** | **396 llamadas LLM/hora** |

Ese es un **techo alcanzado solo mientras los cachés están fríos**, y decae a **cero**: un caché caliente y con digest válido no cuesta ninguna llamada ni espacio, así que una vez que se resumen los alcances y dominios de un proyecto, el ciclo de sueño no gasta nada hasta que el gráfico cambia. Establece cualquier presupuesto en `0` para apagar su operación, o sube `--consolidate-idle` para hacer los ticks más raros.

**Un presupuesto es un techo, no una cuota.** Ambos presupuestos se gastan *secuencialmente* dentro de un tick, y el tick mantiene la puerta de compilación durante todo el paso — entonces en los predeterminados un tick podría ocupar la puerta a través de 33 llamadas LLM consecutivas. Un guardado de archivo que llega a mitad de tick tuvo que esperar a que se completara cada llamada restante antes de que su ejecución de canalización pudiera comenzar, que con un proveedor CLI son minutos. Ambos bucles de precalentamiento ahora verifican, en la parte superior de cada iteración, si una ejecución de canalización está bloqueada en la puerta, y **abandonan su presupuesto restante** si es así:

- la verificación sucede *entre* llamadas, nunca a mitad de llamada, así que la ejecución ya en vuelo siempre se completa y la canalización espera como máximo esa una llamada;
- detenerse temprano no tiene pérdida. El calentamiento es idempotente, así que un alcance o dominio que el tick nunca alcanzó simplemente sigue frío en el siguiente, en el mismo rango — nada se pierde, se corrompe, o se paga dos veces;
- un dominio abandonado no toma **ningún golpe de retroceso**. Los golpes son para un dominio cuyo intento de calentamiento gastó una llamada y falló; uno abandonado nunca fue intentado, así que cobrarlo empujaría un dominio que se puede calentar hacia abajo en la cola porque un archivo no relacionado fue guardado;
- se reporta, no silenciosamente. El dict resumen del tick gana `abandoned` y `unspent` (cuántos espacios de presupuesto quedaron sin usar), así que el registro del demonio distingue "se puso de lado por una canalización" de "no había nada que calentar".

**Por qué aquí y no en compile.** Un resumen cuesta una llamada LLM. Acuñarlos durante la compilación pondría una llamada por dominio en cada compilación, y la compilación es la ruta que este proyecto mantiene determinista y barata. Acuñarlos perezosamente en lectura significaría que una llamada `graph_map` podría bloquearse en un modelo. El ciclo de sueño inactivo es el único lugar que queda que puede gastar una llamada en la que nadie está esperando.

## Seguridad y determinismo

- **Se ejecuta bajo la puerta de compilación, para todo el paso.** La consolidación adquiere el mismo bloqueo que una recompilación, por lo que se serializa con compilaciones y **nunca se superpone**. Una compilación pendiente espera una consolidación en vuelo y viceversa — el gráfico nunca se lee durante la escritura. La puerta deliberadamente **no** se libera entre llamadas LLM: cada operación en un tick lee el mismo `graph.json` que el tick cargó, así que devolver la puerta a mitad de paso permitiría que una compilación reescribiera el gráfico debajo y dejara los resúmenes escritos temprano en un paso describiendo un gráfico diferente de los que fueron escritos tarde. Por eso un tick esperando en una canalización **abandona su presupuesto restante** en lugar de ceder la puerta — cambia calentamiento especulativo por latencia, nunca consistencia por latencia.
- **Nunca se eleva al bucle del demonio.** Todo el paso se envuelve; cualquier error se registra y el hilo mantiene el bucle. Una consolidación fallida nunca derriba el motor.
- **No-op cuando la puerta está apagada.** Con `TESSERAE_AGENT_DISTILL` sin establecer, el paso no carga nada costoso y regresa inmediatamente, por lo que dejar el ciclo de sueño encendido cuesta esencialmente nada.
- **Artefactos deterministas, sin cambios.** Los artefactos destilados permanecen deterministas dadas sus entradas; el ciclo de sueño solo cambia *cuándo* se ejecuta la destilación, nunca *qué* produce. El tiempo de inactividad nunca se filtra en `graph.json` o ninguna capa destilada.
- **`graph.json` sigue siendo byte-idempotente.** Ninguna operación aquí lo escribe. El estado de acceso vive en el sidecar `node_memory`, las conexiones descubiertas en una superposición acumulativa, y tanto los resúmenes como los resúmenes de dominio en el caché `community_summaries` — todos bajo `.tesserae`, todos fusionados en memoria solo en tiempo de lectura. Los bytes de gráfico autorizados no se ven afectados por el historial de recuperación, enlaces descubiertos o prosa pre-calentada. Los resúmenes y resúmenes son **cachés, no conocimiento**: eliminar el directorio de caché cuesta al lector siguiente una tarjeta estructural y nada más.
- **Apagado limpio.** El hilo de consolidación observa el evento de parada del demonio y sale adecuadamente en `Ctrl-C` / apagado. Es solo una característica de modo de larga duración: `tesserae engine ... --once` nunca la inicia.

## Banderas CLI

| Bandera | Defecto | Efecto |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Habilita o deshabilita el ciclo de sueño completamente. Habilitado por defecto (no-op si la puerta de destilación no está configurada). |
| `--consolidate-idle SECONDS` | `300` | Ventana de descanso: consolida después de esta cantidad de segundos sin actividad. |
| `--consolidate-every SECONDS` | `21600` | Techo: consolida al menos tan a menudo independientemente de la actividad. `0` desactiva el techo. |
| `--consolidate-check SECONDS` | `30` | Con qué frecuencia el hilo de consolidación se despierta para reevaluar los disparadores. |
| `--summarize-budget N` | `25` | Máximo de llamadas LLM por tick gastadas en pre-calentar resúmenes de comunidad. `0` desactiva la operación SUMMARIZE. |
| `--brief-budget N` | `8` | Máximo de llamadas LLM por tick gastadas en pre-calentar resúmenes de dominio del acta. `0` desactiva la operación BRIEF. |

## Comportamiento de flota(`--all`)

`tesserae engine --all` mantiene fresco cada proyecto registrado en un proceso. Cada unidad de proyecto obtiene su propio hilo de consolidación con los mismos controles, y todas las unidades comparten una puerta de compilación de todo el parque — entonces una consolidación en un proyecto se serializa contra compilaciones en todo el parque, nunca superponiéndose a ninguna.

## Ejemplo trabajado

Activa la destilación, luego ejecuta el motor con un ciclo de sueño más rápido para una demostración — consolida después de 60s inactivo, y al menos cada 30 min sin importar qué:

```bash
export TESSERAE_AGENT_DISTILL=1
tesserae engine \
  --consolidate-idle 60 \
  --consolidate-every 1800 \
  --consolidate-check 15
```

Trabaja en tu editor y agentes como de costumbre; el motor observa, debounces y recompila cada cambio. Detente durante un minuto y se dispara el disparador de inactividad: el hilo de consolidación adquiere la puerta de compilación y destila cualquier agente bajo presión de memoria — reorganizando, comprimiendo y olvidando de manera segura — luego vuelve a dormir. Continúa trabajando más allá de la marca de media hora sin nunca pausar y el techo también se dispara, por lo que un proyecto despiadado aún se consolida.

Para mantener el motor funcionando pero dejar la consolidación para ejecuciones manuales de `tesserae distill`, pasa `--no-consolidate`. Para que se ejecute en inactividad pero nunca en un cronograma fijo, pasa `--consolidate-every 0`.
