# Consolidación automática — el ciclo de sueño del motor

<!-- translations:start -->
<p align="center"><a href="../engine-consolidation.md">English</a> · <a href="engine-consolidation.ko.md">한국어</a> · <a href="engine-consolidation.zh.md">中文</a> · <a href="engine-consolidation.ja.md">日本語</a> · <a href="engine-consolidation.ru.md">Русский</a> · <a href="engine-consolidation.es.md">Español</a> · <a href="engine-consolidation.fr.md">Français</a> · <a href="engine-consolidation.de.md">Deutsch</a></p>
<!-- translations:end -->

El cerebro consolida la memoria durante el descanso. Mientras duermes, la experiencia bruta del día se reorganiza, se comprime e integra — lo reciente y ruidoso se pliega en una estructura duradera. El motor de Tesserae hace lo mismo. Cuando un proyecto se **inactiva**, el demonio siempre encendido deja de esperar la siguiente edición y pasa el tiempo reorganizando lo que ya sabe: **comprime y olvida** la memoria reciente ruidosa, permite que el conocimiento que nadie ha recuperado **se desvanezca por desuso**, y **descubre nuevas conexiones** entre lo que sobrevive.

Hasta ahora, ese paso solo se ejecutaba cuando lo solicitabas — `tesserae refresh` bajo presión de memoria, o un `tesserae distill` explícito. El motor recompilaba en cada archivo y evento de sesión, pero nunca se consolidaba automáticamente. El **ciclo de sueño** cierra esa brecha: deja `tesserae engine` en funcionamiento y la consolidación ocurre durante el descanso, sin ningún comando que recordar.

Como todo en el sistema de [memoria en capas](agent-memory.md), esto es **no-op a menos que optes** — el demonio consolida en inactividad, pero la destilación debajo solo funciona cuando se establece `TESSERAE_AGENT_DISTILL`.

## Cuándo se dispara

Un hilo de consolidación dedicado se despierta en un **intervalo de verificación** fijo (por defecto 30 segundos) y evalúa dos desencadenantes independientes contra un reloj de actividad monótono:

- **Disparador de inactividad.** El proyecto no ha visto un evento de disparo ni una ejecución de canalización durante al menos `--consolidate-idle` segundos (por defecto **300s = 5 min**). Este es el caso de "consolidación durante el descanso" — el motor notó que dejaste de trabajar y utilizó la pausa. Un **piso** desde la última consolidación previene cambios bruscos, por lo que un proyecto ocupado que acaba de quedarse tranquilo no se consolida en un disparo sensible.
- **Disparador de techo.** Han transcurrido al menos `--consolidate-every` segundos desde la última consolidación, **independientemente de la actividad** (por defecto **21600s = 6h**). Esto garantiza que un proyecto continuamente ocupado aún se consolide periódicamente en lugar de nunca tener un momento tranquilo. Configurarlo a `0` desactiva el techo — entonces la inactividad es el único disparador.

Cada edición, turno de sesión o recompilación aumenta el reloj de actividad, por lo que la ventana de inactividad solo transcurre durante el descanso genuino. Ambos relojes son **monótonos**, nunca reloj de pared, y nunca se persisten en ningún artefacto — el tiempo de consolidación nunca puede perturbar el gráfico byte-determinista.

## Qué se ejecuta — tres operaciones

Cada disparo carga el gráfico compilado desde `.tesserae/graph.json` (si el archivo está ausente, el paso se omite) y ejecuta tres operaciones de consolidación, en orden. Juntos reflejan lo que hace un cerebro descansado: comprime lo reciente ruidoso, deja que lo nunca revisitado se desvanezca, y crea nuevas asociaciones entre lo que sobrevive.

### 1. Comprimir / olvidar — destilación

Llama al mismo punto de entrada `maybe_distill_on_refresh` que usa `tesserae refresh` para reorganizar, comprimir y olvidar de manera segura la memoria de cada agente. Esa función es **triple compuerta** internamente y nunca se eleva por falla por agente:

1. **Puerta de inclusión** — `TESSERAE_AGENT_DISTILL=1` (o `{"agent_distill": {"enabled": true}}` en `config.json`). Desactivado por defecto; todo el ciclo es un no-op seguro hasta que lo configures.
2. **Marca de agua por agente** — un agente cuyas conclusiones no han cambiado desde su última destilación se omite.
3. **Presión de memoria por agente** — solo los agentes cuyas conclusiones no destiladas ya no caben en la mitad de una lectura de contexto se consolidan (disparador estilo MemGPT).

Entonces, incluso cuando la consolidación se *dispara* en un cronograma, solo *funciona* para los agentes que se incluyeron y realmente acumularon suficiente memoria nueva para justificarlo. Consulta [Memoria de agente en capas](agent-memory.md) para lo que produce la destilación.

### 2. Olvidar por desuso — decaimiento LRU en recuperación, no solo por edad

El decaimiento de la destilación ya no se impulsa solo por la edad de creación. Cada superficie de lectura registra el acceso a las conclusiones que devuelve — `last_accessed_at` y `access_count` — en un **sidecar `node_memory`**, nunca en `graph.json`. Antes de que el paso de destilación calcule el decaimiento, fusiona ese estado de acceso en vivo en su vista de trabajo, por lo que una conclusión que no se ha recuperado desde que se acuñó decae y se vuelve elegible para ser absorbida o degradada, mientras que una que fue leída recientemente se mantiene fresca sin importar su edad. Esto es **recencia de recuperación**, la intuición LRU (menos recientemente usada) aplicada a la memoria: el conocimiento que sigues extrayendo permanece; el conocimiento que nadie pide se desvanece primero. Un sidecar vacío reproduce el antiguo comportamiento solo de edad exactamente, por lo que es completamente compatible hacia atrás.

### 3. Asociar — descubrir nuevas conexiones

La operación final busca *nuevas* relaciones entre lo que sobrevivió. Incrusta notas destiladas y vincula pares cuyos significados están cerca — **compuerta de incrustación**, por lo que solo se ejecuta cuando se configura un backend de incrustación real (el stub hash se omite, nunca produciendo enlaces ruidosos). El descubrimiento se ejecuta dentro del proyecto y **entre agentes**, y las conexiones que encuentra se acuñan como bordes `shares_concept_with` que llevan un marcador `federation_semantic`.

Crucialmente, estos bordes descubiertos se escriben en una **superposición de sidecar** bajo `.tesserae`, *nunca* en `graph.json`. La superposición **acumula entre ciclos** — cada paso de asociación deduplica contra y extiende lo que encontraron los pasos anteriores. En tiempo de lectura (consulta, expansión PPR, vistas de federación) la superposición se fusiona en el gráfico **en memoria solo**, exactamente como la superposición de vista por agente — por lo que el `graph.json` byte-determinista nunca se toca. Toda la operación se envuelve y nunca se eleva al bucle del demonio.

## Seguridad y determinismo

- **Se ejecuta bajo la puerta de compilación.** La consolidación adquiere el mismo bloqueo que una recompilación, por lo que se serializa con compilaciones y **nunca se superpone**. Una compilación pendiente espera una consolidación en vuelo y viceversa — el gráfico nunca se lee durante la escritura.
- **Nunca se eleva al bucle del demonio.** Todo el paso se envuelve; cualquier error se registra y el hilo mantiene el bucle. Una consolidación fallida nunca derriba el motor.
- **No-op cuando la puerta está apagada.** Con `TESSERAE_AGENT_DISTILL` sin establecer, el paso no carga nada costoso y regresa inmediatamente, por lo que dejar el ciclo de sueño encendido cuesta esencialmente nada.
- **Artefactos deterministas, sin cambios.** Los artefactos destilados permanecen deterministas dadas sus entradas; el ciclo de sueño solo cambia *cuándo* se ejecuta la destilación, nunca *qué* produce. El tiempo de inactividad nunca se filtra en `graph.json` o ninguna capa destilada.
- **`graph.json` sigue siendo byte-idempotente.** Ninguna operación nueva lo escribe. El estado de acceso vive en el sidecar `node_memory` y las conexiones descubiertas en una superposición acumulativa — ambas bajo `.tesserae`, ambas fusionadas en memoria solo en tiempo de lectura. Los bytes de gráfico autorizados no se ven afectados por el historial de recuperación o enlaces descubiertos.
- **Apagado limpio.** El hilo de consolidación observa el evento de parada del demonio y sale adecuadamente en `Ctrl-C` / apagado. Es solo una característica de modo de larga duración: `tesserae engine ... --once` nunca la inicia.

## Banderas CLI

| Bandera | Defecto | Efecto |
|---|---|---|
| `--consolidate` / `--no-consolidate` | on | Habilita o deshabilita el ciclo de sueño completamente. Habilitado por defecto (no-op si la puerta de destilación no está configurada). |
| `--consolidate-idle SECONDS` | `300` | Ventana de descanso: consolida después de esta cantidad de segundos sin actividad. |
| `--consolidate-every SECONDS` | `21600` | Techo: consolida al menos tan a menudo independientemente de la actividad. `0` desactiva el techo. |
| `--consolidate-check SECONDS` | `30` | Con qué frecuencia el hilo de consolidación se despierta para reevaluar los disparadores. |

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
