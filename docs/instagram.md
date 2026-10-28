# Conectar Instagram y publicar

El módulo utiliza la API oficial de Meta. No usa contraseñas, cookies copiadas del navegador ni automatización de la interfaz de Instagram. La primera autorización de la cuenta es imprescindible. No se ha conectado ni publicado en ninguna cuenta durante el desarrollo.

## Ruta recomendada para este proyecto: archivo local

Para subir el MP4 desde este ordenador sin pagar alojamiento, se implementa Facebook Login y la subida de archivo a `rupload.facebook.com`. Hace falta una cuenta profesional de Instagram (creador o empresa), una Página de Facebook vinculada, una aplicación de Meta con acceso a la cuenta y un token autorizado para publicar.

1. Convertir la cuenta de Instagram a profesional si es personal y vincularla a una Página que administras.
2. Crear/configurar la aplicación en el [panel oficial de Meta](https://developers.facebook.com/apps/), con Instagram API y Facebook Login. La configuración y acceso disponibles dependen de la cuenta y del panel vigente.
3. Autorizar `instagram_basic`, `instagram_content_publish`, `pages_read_engagement` y `pages_show_list` para identificar la Página. Puede haber permisos adicionales si los activos se administran mediante Business Manager. Usar solo los que exija Meta para esos activos.
4. Obtener el identificador numérico de Instagram profesional vinculado a la Página y un token válido para ella. Un `@nombre` no sustituye al identificador numérico. No pegar tokens en conversaciones ni guardarlos en Git: introducirlos mediante el asistente local/configuración privada del proyecto.
5. Comprobar la conexión con `InstagramPublisher(config).check_connection()`, que consulta la cuenta y los límites sin publicar.
6. Conservar los diarios locales de publicación. Registran fecha, hashes e identificadores, nunca el token.

Para cuentas propias con roles en la aplicación puede bastar acceso de desarrollo/estándar; publicar para cuentas de terceros requiere los accesos/revisión que Meta establezca. La revisión de Meta y una eventual verificación empresarial no pueden ser resueltas por este código.

El token debe tener vigencia suficiente para ejecución desatendida. Los tokens de Página y los tokens de usuario no son intercambiables en su gestión. Ningún token puede garantizarse permanente: una revocación, un cambio de credenciales o permisos, o los controles de Meta pueden exigir autorización de nuevo. El programa no intenta sortearlos.

### Asistente seguro para GitHub Actions

La cuenta de destino acordada es `@porquehistoria`. Para vincular la Página desde el móvil: Instagram → perfil → Editar perfil → Información pública del negocio / Información del perfil → Página → Conectar o crear → elegir una Página que administras o crear una nueva. Los nombres de los menús pueden variar. [Ayuda oficial de Instagram sobre la vinculación](https://help.instagram.com/570895513091465).

Después de configurar tu aplicación de Meta, abrir el [Explorador de la API Graph](https://developers.facebook.com/tools/explorer/), seleccionar **tu propia aplicación**, generar un token de usuario de Facebook y autorizar los permisos de publicación anteriores para la Página correcta. Usar el [Depurador oficial de tokens](https://developers.facebook.com/tools/debug/accesstoken/) para comprobar el token y, si está disponible para ese tipo de token/aplicación, extenderlo a larga duración. No usar un token efímero de prueba para la programación diaria. La interfaz de Meta y los permisos habilitados dependen del estado de la aplicación; si no permite generar/extender el token, resolver primero el acceso en Meta.

Con un **token de usuario de larga duración** disponible, ejecutar en PowerShell:

```powershell
.\scripts\connect_instagram.ps1 -Repository enriquerodrigueztrenchsporta/porquehistoria-ia
```

El asistente pide el token en una entrada oculta, descubre la Página vinculada a `porquehistoria`, obtiene su token, comprueba la cuenta y el acceso a publicación, y escribe `INSTAGRAM_USER_ID` y `INSTAGRAM_ACCESS_TOKEN` en GitHub Actions Secrets mediante entrada estándar. Guarda también `IG_LOGIN_MODE`, `IG_EXPECTED_USERNAME` e `IG_API_VERSION` como variables. No escribe tokens en archivos, argumentos de procesos o mensajes. No solicita la contraseña de Instagram. El repositorio debe existir y `gh` debe tener acceso a él. El script **no activa** el horario ni publica un vídeo; la activación del workflow es un paso separado del despliegue.

El script deriva el token de Página del token de usuario que proporcionas, pero no verifica ni extiende su duración automáticamente: comprobar la duración en Meta es necesario. El identificador numérico se descubre sin pedirlo al usuario. En ejecución, `expected_username="porquehistoria"` verifica de nuevo que no se esté preparando una publicación para otra cuenta.

## Alternativa: Instagram Login

`login_mode="instagram"` usa `graph.instagram.com`, los permisos `instagram_business_basic` e `instagram_business_content_publish`, y `public_video_url` HTTPS que Meta pueda descargar. Esta alternativa no necesita la vinculación con una Página, pero sí un alojamiento público del MP4. El proyecto no proporciona ese alojamiento; no usar una ruta local como URL.

El módulo no intenta usar la subida de archivo de Facebook Login con un token de Instagram Login. La documentación de publicación distingue los hosts y mecanismos por tipo de login; deben verificarse en el panel de la aplicación. El token de Instagram Login de larga duración se renueva antes de caducar, a partir de las 24 horas de antigüedad. `refresh_instagram_token` devuelve el nuevo token y su vencimiento; el llamante debe guardarlos de forma privada. No renueva tokens de Facebook Login.

## Integración Python

```python
from pathlib import Path
from instagram_pipeline.instagram import InstagramConfig, InstagramPublisher

# token procede del almacén privado; no escribirlo en código.
config = InstagramConfig(user_id=ig_user_id, access_token=token,
                         login_mode="facebook", api_version="v25.0")
publisher = InstagramPublisher(config)
journal = Path("output/2026-10-28/publication.json")

# Antes de las 20:00: subir el vídeo y esperar a que Meta lo procese.
publisher.prepare_reel(Path("output/2026-10-28/reel.mp4"), caption,
                       journal, publish_key="2026-10-28")

# A las 20:00 Europe/Madrid: efectuar la publicación.
publisher.publish_prepared(journal, publish_key="2026-10-28")
```

La versión Graph es configurable y debe mantenerse entre las versiones admitidas por la aplicación de Meta. El código usa `v25.0` como valor inicial; no afirma que sea la más reciente. El límite local de 300 MB es conservador; el vídeo debe cumplir también los requisitos de codificación de Meta. El renderer del proyecto debe generar MP4 H.264, AAC, 9:16 y 24–30 fps.

El contenedor solo se publica cuando Meta confirma `FINISHED`. Se consulta como máximo seis veces, cada 60 segundos (cinco minutos de espera); si sigue procesándose se conserva el contenedor para la siguiente ejecución. Crear el contenedor el mismo día: Meta puede caducar contenedores sin publicar. La publicación puede aparecer con retraso por procesamiento o indisponibilidad de Meta; no puede garantizarse visibilidad al segundo exacto.

## Interrupciones y duplicados

El diario se guarda y sincroniza en disco **antes** de enviar `media_publish`. Si la respuesta se pierde, la siguiente ejecución consulta el mismo contenedor. Si está `PUBLISHED`, lo marca publicado sin volver a publicar. Si no hay confirmación, se detiene con `AmbiguousPublishError`. Una persona debe resolver ese caso excepcional en la cuenta/Meta; borrar el diario a ciegas puede producir duplicados. También se rechazan cambios de cuenta, vídeo o texto para una fecha con diario existente, y hay un bloqueo del sistema operativo para evitar dos publicadores simultáneos.

En un runner efímero como GitHub Actions, construir `InstagramPublisher(config, checkpoint=guardar_en_almacen_durable)`. El callback recibe una copia del diario después de cada guardado local y **antes** de la siguiente petición que modifica Meta. Si falla, la excepción interrumpe el flujo. Restaurar ese diario al inicio de cada ejecución y usar además un grupo de concurrencia único para la cuenta: el bloqueo local no coordina dos máquinas. Los snapshots no incluyen tokens ni URI de subida. `PUBLISHED` puede confirmarse por estado aunque Meta no devuelva el identificador final en la recuperación; en ese caso `media_id` puede faltar y no debe generar otra publicación.

La subida y creación de contenedor tienen protección conservadora ante fallos: no se crean contenedores repetidamente tras una respuesta perdida. Se conserva el error para revisión. Esta elección evita afirmar que un sistema externo pueda funcionar indefinidamente sin excepciones ni mantenimiento.

## Fuentes consultadas el 28 de octubre de 2026

- [Ejemplo oficial de Meta: Instagram Reels Publishing](https://github.com/fbsamples/reels_publishing_apis/blob/main/insta_reels_publishing_api_sample/README.md): cuenta/Página, formato del vídeo y subida del archivo local.
- [Código oficial de Meta de dicho ejemplo](https://github.com/fbsamples/reels_publishing_apis/blob/main/insta_reels_publishing_api_sample/index.js): creación del contenedor, URI de subida, cabeceras, consulta y publicación.
- [Colección oficial de Meta en Postman](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api): diferencias entre los accesos y permisos. El buscador mostró la documentación; el visor directo fue limitado.
- [Documentación de publicación de Meta](https://developers.facebook.com/docs/instagram-platform/content-publishing/), [Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/) y [renovación de token](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/). El lector web recibió HTTP 429 al abrir estas páginas. Verificar los requisitos actuales en el panel antes de activar; no se han validado credenciales reales.

Las pruebas automatizadas simulan Meta y verifican estados y prevención de duplicados. No sustituyen la prueba de integración con la cuenta autorizada.

## Operación diaria (GitHub Actions)

`.github/workflows/daily-reel.yml` se ejecuta en un servidor gratuito de GitHub, sin depender de este ordenador. Cron en UTC con dos disparadores (15:07 y 16:07 UTC); solo continúa el que cae entre 16:30 y 19:00 hora de Madrid, así se cubre el cambio de horario. El flujo instala Ollama y `qwen2.5:7b` (en caché), descarga la voz Piper, genera y audita el episodio, sube el vídeo a Meta y espera a las 20:00 Europe/Madrid para `media_publish`.

- Prueba sin publicar: Actions → daily-reel → Run workflow con «Solo generar el vídeo» marcado. El MP4 y los informes de los agentes quedan como artefacto descargable 14 días.
- Activación: variable de repositorio `PUBLISH_ENABLED=true` (sin ella, la ejecución programada se detiene antes de subir nada).
- `OLLAMA_TIMEOUT=900`: el ejecutor no tiene GPU y cada rol IA tarda más que en un equipo de escritorio.
- Piper requiere Python ≤ 3.13 (onnxruntime); el workflow usa 3.12.
- Repositorio público: minutos de Actions ilimitados. En privado, la espera hasta las 20:00 (~3 h diarias) supera los 2.000 min/mes gratuitos.
