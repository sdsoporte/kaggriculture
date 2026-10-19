# Análisis de partidos (matches) de la competencia Kaggriculture usando la CLI de Kaggle

## Resumen
Kaggriculture es una competencia de simulación agrícola basada en turnos donde cada envío (submission) corresponde a un agente que juega uno o más episodios (partidos). Aunque la CLI de Kaggle no ofrece un endpoint directo para obtener los detalles de cada episodio, podemos:

1. Listar las submissions y obtener su metadata (score, fecha, descripción).
2. Construir la URL pública del episodio a partir del `submissionId` y `episodeId` mostrados en la interfaz web.
3. Descargar los archivos de la competencia (si los hubiera) para inspeccionar el entorno o los logs.
4. Utilizar la información obtenida para análisis manual o mediante scripts externos.

Este documento explica los pasos para obtener la información disponible mediante la CLI y cómo usarla para analizar los partidos.

## Requisitos previos
- Tener instalada la CLI de Kaggle (ver [instalación](#instalación-de-la-cli-de-kaggle)).
- Estar autenticado con tu token de API de Kaggle (el archivo `~/.kaggle/kaggle.json` o las variables de entorno `KAGGLE_USERNAME` y `KAGGLE_KEY`). En este entorno ya tienes el token en `~/.kaggle/access_token`; la CLI lo leerá automáticamente si defines la variable de entorno `KAGGLE_KEY`.

### Instalación de la CLI de Kaggle (si no está disponible)
```bash
# Crear un entorno virtual y instalar la CLI
python3 -m venv /tmp/kaggle_venv
source /tmp/kaggle_venv/bin/activate
pip install kaggle
```
Luego asegúrate de que tu token esté disponible:
```bash
export KAGGLE_KEY=$(cat ~/.kaggle/access_token)
# El username puede ser cualquiera; la CLI solo requiere la key para endpoints públicos.
export KAGGLE_USERNAME=tu_usuario_de_kaggle  # opcional para algunos endpoints
```

## 1. Listar submissions de la competencia
```bash
source /tmp/kaggle_venv/bin/activate
kaggle competitions submissions -c kaggriculture
```
Salida de ejemplo:
```
     ref  fileName    date                        description                                                          status                     publicScore  privateScore  
--------  ----------  --------------------------  -------------------------------------------------------------------  -------------------------  -----------  ------------  
56450455  main_v3.py  2026-09-22 02:31:28.497000  phase3 v3: harvest-priority, DIG weeds, per-crop plant_until         SubmissionStatus.COMPLETE  236.0                      
56445327  main_v2.py  2026-09-21 23:19:37.487000  phase3 v2: four hands, wheat-carrot-tomato rotation, reactive sales  SubmissionStatus.COMPLETE  277.4                      
56442827  main_v0.py  2026-09-21 20:17:59.827000  v0: pipeline validation (baseline_v0, wheat-only single farmer)      SubmissionStatus.COMPLETE  218.9                      
```
- `ref` es el **submissionId**.
- La columna `description` suele contener notas sobre la versión del agente.
- `publicScore` es el puntaje que aparece en el leaderboard (puede estar vacío si solo se calcula private score).

## 2. Obtener detalles de una submission específica (metadata)
La CLI no tiene un subcomando para obtener el detalle de una sola submission, pero podemos usar la API pública mediante `curl` (o la propia CLI con la opción `--json` si está disponible). Ejemplo usando `curl`:

```bash
source /tmp/kaggle_venv/bin/activate
curl -s -H "Authorization: Bearer $KAGGLE_KEY" \
     -H "Accept: application/json" \
     https://www.kaggle.com/api/v1/competitions/kaggriculture/submissions/56450455
```
> **Nota**: En algunos casos el endpoint devuelve la página HTML de Kaggle en lugar de JSON. Si ocurre eso, puedes obtener la información necesaria desde la lista de submissions (paso 1) o visitar la URL web directamente:
> ```
> https://www.kaggle.com/competitions/kaggriculture/submissions?submissionId=56450455
> ```

## 3. Construir la URL del episodio (match)
En la página web de una submission se muestra el `episodeId` (identificador del partido). La URL tiene el siguiente patrón:

```
https://www.kaggle.com/competitions/kaggriculture/submissions?submissionId=<SUBMISSION_ID>&episodeId=<EPISODE_ID># 
```

Ejemplo con los datos que proporcionaste:
- submissionId = `56450455`
- episodeId = `111928431`

URL completa:
```
https://www.kaggle.com/competitions/kaggriculture/submissions?submissionId=56450455&episodeId=111928431#
```
Al abrir esa URL en un navegador podrás ver el replay del episodio, el log de acciones, el estado de la granja en cada turno y cualquier métrica que la competencia proporcione.

## 4. Descargar archivos de la competencia (si los hay)
Algunas competencias ofrecen archivos de entrada, datos de entorno o logs descargables. Para Kaggriculture, actualmente solo se listan dos archivos de documentación:

```bash
source /tmp/kaggle_venv/bin/activate
kaggle competitions files -c kaggriculture
```
Salida:
```
name             size  creationDate                
---------  ----------  --------------------------  
AGENTS.md       13708  2026-08-15 17:41:16.967000  
README.md       26824  2026-08-15 17:41:16.854000  
```
Estos archivos contienen la descripción del entorno y las reglas del juego. Puedes descargarlos con:
```bash
kaggle competitions download -c kaggriculture -f AGENTS.md
kaggle competitions download -c kaggriculture -f README.md
```
O descargar todos:
```bash
kaggle competitions download -c kaggriculture
```
Esto te dará un ZIP con los archivos mencionados.

## 5. Análisis adicional mediante scripts
Una vez tengas los IDs de submission y episode, puedes:
- Abrir la URL del episodio en un navegador y extraer manualmente la información (puntaje por turno, acciones tomadas, eventos).
- Si la competencia proporciona un archivo de replay o log descargable (por ejemplo, un archivo JSON dentro de la sección “Files” de la episodio), descríbelo y procesarlo con tu lenguaje de preferencia (Python, jq, etc.).
- Utilizar la información de `AGENTS.md` para entender las acciones disponibles, los recursos y las mecánicas del juego, y luego correlacionarlas con lo observado en el episodio.

### Ejemplo rápido de extracción de datos desde la página (usando herramientas externas)
Si deseas automatizar la captura del replay, puedes usar herramientas como `puppeteer` o `playwright` para cargar la URL del episodio y capturar el contenido del tablero o el log. Esto queda fuera del alcance de la CLI de Kaggle, pero es factible dado que la página es pública (requiere estar logueado en Kaggle para ver submissions propias, pero los episodios de submissions públicas suelen ser visibles).

## Limitaciones
- La CLI de Kaggle no expone un endpoint directo para obtener el `episodeId` ni los detalles del replay; esos datos solo están disponibles a través de la interfaz web.
- No hay forma de descargar automáticamente los archivos de replay o logs desde la CLI (si los hubiera) porque no existen endpoints públicos para ellos.
- Para un análisis profundo y automatizado, se recomienda usar la página web de cada episodio o, si la competencia lo permite, descargar los archivos de replay que a veces se proveen en la sección “Data” de la competencia.

## Próximos pasos
1. Lista las submissions de interés y anota sus `submissionId`.
2. Para cada submission, visita la URL de la competencia para obtener el `episodeId` (o usa la URL que ya tengas).
3. Abre la URL del episodio en un navegador para revisar el replay y tomar notas.
4. Si necesitas comparar múltiples episodios, considera escribir un script que abra las URLs en secuencia y capture métricas (por ejemplo, puntaje final, número de turnos, acciones específicas) usando herramientas de automatización de navegadores.
5. Consulta `AGENTS.md` para comprender el significado de cada acción y poder interpretar los logs del episodio.

## Preguntas frecuentes (FAQ)

**¿Puedo obtener el reward (recompensa) de un episodio específico desde la CLI?**  
No directamente. El premio (score) que aparece en el leaderboard es el que se muestra en la columna `publicScore` o `privateScore` de la lista de submissions. Los detalles por turno no están disponibles vía CLI.

**¿Hay alguna forma de descargar el archivo de replay (.json, .log) de un episodio?**  
Actualmente no hay un endpoint público conocido para ello. La única forma es a través de la interfaz web de Kaggle, donde algunos episodios permiten descargar un archivo de replay si la competencia lo provee.

**¿Puedo usar la API de Kaggle para obtener más información sobre la competencia?**  
Sí, endpoints como `/competitions/list`, `/competitions/{competition}/submissions` y `/competitions/{competition}/files` funcionan y devuelven JSON cuando se autentica correctamente. Para endpoints no documentados (como episodios) la respuesta suele ser HTML.

---
*Documentación generada el 2026-09-22. Los comandos asumen que la variable de entorno `KAGGLE_KEY` contiene tu token de API de Kaggle.*