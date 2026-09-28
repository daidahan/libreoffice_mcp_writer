# libreoffice-mcp-writer

Servidor [MCP](https://modelcontextprotocol.io) que conecta Claude con **LibreOffice Writer** mediante la API UNO. Permite crear, editar y dar formato a documentos `.odt` (texto, listas, tablas y estilos) desde Claude Code, con LibreOffice corriendo sin ventana y el acceso a archivos restringido a una sola carpeta.

> **English summary:** MCP server that lets Claude create, edit and format LibreOffice Writer `.odt` documents through the UNO API. LibreOffice runs headless and file access is sandboxed to a single root folder. Documentation is in Spanish.

---

## Cómo funciona

```
Claude Code  ──MCP (stdio)──▶  libreoffice_mcp_writer.py  ──UNO (socket :2002)──▶  LibreOffice (headless)  ──▶  .odt
```

El servidor nunca escribe archivos por su cuenta: le pide a LibreOffice que los abra y los guarde. Así los documentos se guardan siempre en el formato nativo, sin manipular el ZIP/XML interno del `.odt`.

## Modelo de seguridad

Un asistente con acceso a tus documentos puede dañarlos si improvisa. Este proyecto se diseñó para que eso no sea posible:

**En el servidor:**
- Solo acepta rutas dentro de la carpeta raíz (`LIBREOFFICE_MCP_ROOT`). Rechaza `../` y enlaces simbólicos que apunten fuera.
- Solo trabaja con archivos `.odt` y rechaza carpetas y archivos ocultos, así que no puede tocar `.mcp.json`, `.claude/` ni el propio script.
- `create_document` nunca sobrescribe un archivo existente.
- Los mensajes de error le indican al modelo que informe el problema en vez de buscar archivos por el disco o editarlos directamente.

**En Claude Code (muy recomendado):**
- Deniega `Bash`, `Edit` y `Write` en la carpeta de documentos (ver [`examples/settings.json`](examples/settings.json)). Sin eso, el modelo podría saltarse el MCP y modificar o borrar archivos directamente. Bloquear comandos específicos como `rm` no basta, porque siempre hay otro camino (`python -c`, `find -delete`…).

## Requisitos

- **LibreOffice** con los bindings de Python (`uno`):
  - Arch Linux: incluidos en `libreoffice-fresh` o `libreoffice-still`.
  - Debian / Ubuntu: `sudo apt install libreoffice-writer python3-uno`
- **Python 3.10 o superior**, el mismo intérprete contra el que se compiló `uno`.
- **Claude Code**.

Comprueba que Python puede cargar `uno`:

```bash
URE_BOOTSTRAP="vnd.sun.star.pathname:/usr/lib/libreoffice/program/fundamentalrc" \
LD_LIBRARY_PATH="/usr/lib/libreoffice/program" \
PYTHONPATH="/usr/lib/libreoffice/program" \
python3 -c "import uno; print('ok')"
```

## Instalación

```bash
git clone https://github.com/TU_USUARIO/libreoffice-mcp-writer.git
cd libreoffice-mcp-writer

python3 -m venv --system-site-packages ~/.venvs/libreoffice-mcp
~/.venvs/libreoffice-mcp/bin/pip install -r requirements.txt
```

`--system-site-packages` es necesario: `uno` lo instala LibreOffice en el Python del sistema y no está disponible en pip. Sin ese flag, el entorno virtual no lo ve.

## Configuración

Todo se configura en tu **carpeta de documentos**, que es desde donde inicias Claude Code.

**1. Registrar el servidor.** Copia `.mcp.json.example` como `.mcp.json` en tu carpeta de documentos y ajusta las rutas, o regístralo con la CLI:

```bash
cd ~/Documentos
claude mcp add --scope project libreoffice-writer \
  --env URE_BOOTSTRAP="vnd.sun.star.pathname:/usr/lib/libreoffice/program/fundamentalrc" \
  --env LD_LIBRARY_PATH="/usr/lib/libreoffice/program" \
  --env PYTHONPATH="/usr/lib/libreoffice/program" \
  --env LIBREOFFICE_MCP_ROOT="$HOME/Documentos" \
  -- ~/.venvs/libreoffice-mcp/bin/python3 /ruta/a/libreoffice-mcp-writer/libreoffice_mcp_writer.py
```

**2. Restringir permisos.** Copia `examples/settings.json` a `.claude/settings.json` en tu carpeta de documentos.

**3. Instrucciones para el modelo (opcional).** Copia `examples/CLAUDE.md` a tu carpeta de documentos. Ahorra tokens y evita que el modelo intente caminos que los permisos van a bloquear.

## Uso

```bash
./scripts/start-libreoffice.sh     # arranca LibreOffice headless (una sola vez por sesión)
cd ~/Documentos
claude --model sonnet
```

Dentro de Claude Code, `/mcp` debe mostrar `libreoffice-writer` conectado. Luego puedes pedir cosas como:

```
Crea informes/reunion.odt con el título "Acta de reunión" en estilo Heading 1
Agrega una tabla de 3x2 con los asistentes y sus cargos
Convierte los párrafos "Presupuesto", "Plazos" y "Riesgos" en una lista con viñetas
Pon "Total" en negrita y guarda el documento
```

Se recomienda Sonnet u Opus. Los modelos más pequeños tienden a ignorar las restricciones cuando una herramienta falla.

## Herramientas

| Herramienta | Qué hace |
|---|---|
| `open_document` | Abre un `.odt` existente dentro de la carpeta raíz |
| `create_document` | Crea un `.odt` nuevo y vacío; nunca sobrescribe |
| `list_documents` | Lista los documentos abiertos con su ruta |
| `save_document` | Guarda el documento en su archivo |
| `close_document` | Cierra un documento, opcionalmente guardándolo |
| `get_last_paragraphs` | Lee los últimos N párrafos |
| `get_document_text` | Lee el documento completo (costoso en tokens) |
| `find_and_replace` | Busca y reemplaza; con `replace=""` borra |
| `append_text` | Agrega texto al final |
| `delete_last_paragraph` | Elimina el último párrafo |
| `insert_paragraph_after` | Inserta un párrafo después del que contiene cierto texto |
| `set_char_format` | Negrita, cursiva o subrayado sobre un texto |
| `set_paragraph_style` | Aplica un estilo de párrafo |
| `set_list` | Convierte párrafos en lista real (viñetas o numerada) |
| `insert_table` | Inserta una tabla al final o después de un párrafo |
| `list_tables` | Lista las tablas del documento |
| `set_table_cell` / `get_table_cell` | Escribe o lee una celda |

LibreOffice corre sin ventana, así que no hay selección ni cursor visible: las herramientas de formato ubican el texto **buscándolo**.

## Solución de problemas

**`Segmentation fault` al hacer `import uno`.** Faltan las variables de entorno. `uno` carga el resto de LibreOffice en tiempo de ejecución y, sin `URE_BOOTSTRAP` y `LD_LIBRARY_PATH`, falla sin un mensaje claro. Deben estar en el bloque `env` de `.mcp.json`.

**`No module named 'mcp.server.fastmcp'`.** Se instaló mcp 2.x. Ejecuta `pip install "mcp<2"` dentro del entorno virtual.

**`No module named 'uno'` dentro del venv.** El entorno se creó sin `--system-site-packages`. Bórralo y créalo de nuevo con ese flag.

**Claude Code muestra `Failed to connect` o el error `-32000`.** El proceso del servidor terminó al arrancar. Ejecuta a mano el mismo comando y los mismos `args` de `.mcp.json`, con sus variables de entorno, para ver el error real. Revisa que la ruta del script sea exacta (sin sufijos como `~` de archivos de respaldo).

**LibreOffice se cierra apenas arranca, sin mensajes.** Quedó un archivo de bloqueo tras un cierre forzado. Bórralo con `rm ~/.config/libreoffice/4/.lock`, o usa `scripts/start-libreoffice.sh`, que lo hace automáticamente.

**`Can't open display` al arrancar LibreOffice.** Falta la opción `--headless`, o estás ejecutándolo con un usuario sin acceso a la sesión gráfica. El modo headless no necesita pantalla.

**Comprobar si LibreOffice está escuchando:** `ss -ltnp | grep 2002`

## Limitaciones

- Solo LibreOffice **Writer**. Calc e Impress no están soportados.
- No inserta imágenes.
- Las listas se aplican en un solo nivel (sin sublistas).
- Requiere el SDK de MCP 1.x.
- Probado en Linux.

## Licencia

[GNU General Public License v3.0](LICENSE) o posterior (GPL-3.0-or-later).

Puedes usar, modificar y redistribuir este proyecto. Si distribuyes una versión modificada, debe publicarse con su código fuente bajo esta misma licencia.
