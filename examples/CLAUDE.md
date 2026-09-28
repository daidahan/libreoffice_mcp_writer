# Instrucciones para Claude

## Documentos de LibreOffice

- Para leer, crear o editar archivos .odt en esta carpeta o cualquier subcarpeta,
  usa siempre las herramientas del MCP `libreoffice-writer`.
- Las rutas pueden ser relativas a la carpeta raíz configurada (ej. `informes/marzo.odt`).
- LibreOffice corre sin ventana: no existe selección ni cursor. Las herramientas de
  formato ubican el texto buscándolo.
- Para listas usa `set_list`. No escribas "•" a mano.
- Para borrar texto usa `find_and_replace` con `replace=""` o `delete_last_paragraph`.
  No leas el documento completo para cambios puntuales.
- Nunca edites un .odt como ZIP/XML.

## Restricciones

- Bash, Edit y Write están deshabilitados a propósito. Si una tarea los necesita,
  dilo de inmediato en una frase, sin verificarlo ni lanzar subagentes.
- Si una herramienta falla, informa el error. No busques archivos en otros directorios.
