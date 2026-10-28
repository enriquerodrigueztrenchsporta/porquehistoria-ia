# Política editorial y agentes históricos

El sistema publica historia divulgativa en español. Toda escena tiene que apuntar a hechos con citas localizables en fuentes institucionales. Una ilustración es simbólica: no se presenta como fotografía ni reconstrucción exacta del pasado.

## Qué se ejecuta

1. El recopilador descarga únicamente páginas HTTPS de dominios institucionales autorizados. Comprueba también cada redirección, limita descarga y tiempo, y conserva fecha y huella de la fuente. La caché dura siete días.
2. Un verificador IA compara cada afirmación española con la cita original y su contexto. Que una cita exista no demuestra por sí solo la afirmación.
3. El editor IA selecciona un ángulo y los identificadores de hechos que se pueden emplear.
4. El guionista IA produce entre cuatro y siete escenas con narración y rótulos. Solo puede pedir tipos de animación predefinidos. No se ejecuta código inventado por el modelo.
5. El auditor IA recibe un contexto nuevo, separado del guionista. Revisa título, gancho, narración y textos de animación. Se permite una corrección; dos rechazos detienen el episodio.

Los roles IA usan Ollama en el propio ordenador o en el ejecutor Linux de GitHub. El modo de revisión queda registrado como `separate_context_same_local_model`: no se afirma que sean modelos diferentes ni que varias respuestas del mismo modelo constituyan fuentes independientes. Ollama se utiliza mediante su API local; no necesita API de pago ni una cuenta de generación.

## Fuentes y renovación de temas

`data/topics.json` contiene 38 temas (35 activos; los marcados `disabled` tienen fuentes que bloquean el acceso automatizado) contrastados durante la preparación del proyecto (8 iniciales y 30 añadidos el 28-10-2026; cada cita se comprobó literalmente contra la fuente en vivo con `research_topic`), con notas para evitar errores frecuentes. Algunos tienen una fuente institucional especializada; otros dos museos o instituciones. `institution_count` indica la cantidad real. No se etiqueta una sola institución como doble comprobación independiente.

Cuando se agota la cola, `discover_topic` explora enlaces de secciones históricas de instituciones autorizadas: Royal Museums Greenwich, NASA, British Library y Torre Eiffel. El investigador IA propone hechos solo a partir de páginas descargadas. Las citas propuestas se cotejan literalmente y luego las revisa otro contexto IA. Las URLs no pueden ser inventadas por el modelo. No se reutilizan artículos ya empleados. La búsqueda está acotada; si no encuentra evidencia suficiente, devuelve error para que el programador omita la publicación y reintente después. No hay una promesa de catálogo infinito ni de una publicación exitosa todos los días.

Ejemplos de fuentes iniciales:

- [La bola horaria de Greenwich — Royal Museums Greenwich](https://www.rmg.co.uk/royal-observatory/attractions/greenwich-time-ball).
- [Sutra del Diamante — British Library](https://idp.bl.uk/blog/the-diamond-sutra/).
- [Telar Jacquard — Science Museum Group](https://collection.sciencemuseumgroup.org.uk/objects/co8405056/jacquard-hand-loom) y [National Museums Scotland](https://www.nms.ac.uk/discover-catalogue/the-jacquard-loom-innovation-in-textiles-and-computing).
- [Torre Eiffel y radio — web oficial](https://www.toureiffel.paris/en/news/130-years/why-was-eiffel-tower-kept).
- [Penny Black — Royal Mint Museum](https://www.royalmintmuseum.org.uk/journal/guest-articles/penny-black/).
- [Estatua de la Libertad — fundación del monumento](https://www.statueofliberty.org/statue-of-liberty/overview-history/).
- [Vuelos de los Wright — NASA](https://www.nasa.gov/history/120-years-ago-the-first-powered-flight-at-kitty-hawk/) y [National Park Service](https://www.nps.gov/places/000/fourth-flight-landing.htm).
- [Anticitera — Ministerio de Cultura griego](https://olympicgames.culture.gov.gr/en/texnologia/1_Mixanismos-Antikithirwn.html).

## Límites explícitos

La verificación automática reduce errores, pero no garantiza exactitud absoluta. Las fuentes también pueden discrepar, cambiar o dejar de ser accesibles. El comportamiento ante una duda, cita ausente, JSON incorrecto o verificador negativo es detener el episodio, sin convertir el fallo en una afirmación inventada. Los tiempos de generación dependen del equipo; cada llamada al modelo tiene un límite de cuatro minutos.

Las citas literales del catálogo se limitan a 25 palabras por fuente y tema. La narración se redacta de nuevo; no reproduce artículos completos. La publicación incluye los enlaces a las fuentes. No se descargan imágenes de los museos como material del vídeo: el montaje usa animación propia.

`data/demo_episode.json` es una muestra redactada por el asistente durante la preparación y contrastada con la fuente oficial de Greenwich. Está marcada `curated_demo`, `manual_review=true`, `agent_runs=[]` y `publication_allowed=false`. Sirve para revisar el resultado visual sin fingir que ya se han ejecutado los agentes de Ollama ni publicar la muestra accidentalmente.

## API interna

```python
topics = load_topics("data/topics.json")
topic = choose_topic(topics, used_ids)
evidence = research_topic(topic, cache_dir=Path("data/source_cache"))
episode = generate_episode(topic, evidence, model="qwen2.5:7b")
# Cuando no quedan temas:
topic = discover_topic(ollama_url="http://127.0.0.1:11434", model="qwen2.5:7b",
                       used_topics=published_topics, cache_dir=Path("data/source_cache"))
```
