# SPDX-License-Identifier: GPL-3.0-or-later
# libreoffice-mcp-writer: servidor MCP para LibreOffice Writer.
# Este programa es software libre: puede redistribuirse y/o modificarse bajo los
# términos de la GNU General Public License, versión 3 o posterior.
# Se distribuye SIN NINGUNA GARANTÍA. Ver el archivo LICENSE.

"""
Servidor MCP para LibreOffice Writer
======================================

Conecta Claude con una instancia de LibreOffice (headless) via la API UNO.
Todas las operaciones sobre archivos se limitan a una carpeta raíz de
documentos (DOCS_ROOT); cualquier ruta fuera de ella se rechaza.

Requisitos previos
-------------------
1. LibreOffice con el socket UNO habilitado:

       nohup soffice --headless --accept="socket,host=localhost,port=2002;urp;" --norestore > /tmp/soffice.log 2>&1 &

2. venv con acceso a los paquetes del sistema y el SDK de MCP 1.x:

       python3 -m venv --system-site-packages ~/.venvs/libreoffice-mcp
       ~/.venvs/libreoffice-mcp/bin/pip install "mcp<2"

3. Variables de entorno (en .mcp.json):

       URE_BOOTSTRAP=vnd.sun.star.pathname:/usr/lib/libreoffice/program/fundamentalrc
       LD_LIBRARY_PATH=/usr/lib/libreoffice/program
       PYTHONPATH=/usr/lib/libreoffice/program
       LIBREOFFICE_MCP_ROOT=~/Documentos   (carpeta raíz; incluye todas sus subcarpetas)

Flujo de uso
-------------
open_document (o create_document) -> edición -> save_document -> close_document

Reglas para el modelo
----------------------
- Nunca editar un .odt como ZIP/XML ni con Bash.
- Si un archivo no existe, pedir la ruta al usuario; no buscarlo en el disco.
- LibreOffice corre en modo headless: NO existe selección ni cursor visible.
  Todas las herramientas de formato ubican el texto buscándolo.
"""

import os

import uno
from mcp.server.fastmcp import FastMCP

UNO_HOST = "localhost"
UNO_PORT = "2002"
DOCS_ROOT = os.path.realpath(
    os.path.expanduser(os.environ.get("LIBREOFFICE_MCP_ROOT", "~/Documentos"))
)

STOP_MSG = (
    "No busques el archivo en otros directorios ni edites el .odt directamente. "
    "Informa este error al usuario y pídele la ruta correcta."
)

mcp = FastMCP("libreoffice-writer")


# ---------------------------------------------------------------------------
# Utilidades internas
# ---------------------------------------------------------------------------

def connect():
    """Abre la conexión UNO con la instancia de LibreOffice."""
    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_context
    )
    ctx = resolver.resolve(
        f"uno:socket,host={UNO_HOST},port={UNO_PORT};urp;StarOffice.ComponentContext"
    )
    return ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)


def resolve_in_root(file_path: str) -> str:
    """
    Convierte file_path en ruta absoluta real y verifica que esté dentro de
    DOCS_ROOT. Acepta rutas absolutas o relativas a DOCS_ROOT.
    """
    expanded = os.path.expanduser(file_path)
    if not os.path.isabs(expanded):
        expanded = os.path.join(DOCS_ROOT, expanded)
    real = os.path.realpath(expanded)
    if os.path.commonpath([real, DOCS_ROOT]) != DOCS_ROOT:
        raise PermissionError(
            f"Ruta fuera de la carpeta permitida ({DOCS_ROOT}): {real}. {STOP_MSG}"
        )
    return real


def resolve_odt(file_path: str) -> str:
    """
    Como resolve_in_root, pero además exige extensión .odt y rechaza
    cualquier carpeta o archivo oculto (ej. .claude/, .mcp.json).
    Así el MCP no puede abrir ni modificar archivos de configuración.
    """
    path = resolve_in_root(file_path)
    if not path.lower().endswith(".odt"):
        raise ValueError(f"Solo se permiten archivos .odt: {path}. {STOP_MSG}")
    rel = os.path.relpath(path, DOCS_ROOT)
    if any(part.startswith(".") for part in rel.split(os.sep)):
        raise PermissionError(f"No se permiten carpetas ni archivos ocultos: {path}. {STOP_MSG}")
    return path


def list_open_documents(desktop):
    """Devuelve {titulo: componente} de los documentos Writer abiertos."""
    docs = {}
    components = desktop.Components.createEnumeration()
    while components.hasMoreElements():
        comp = components.nextElement()
        if comp.supportsService("com.sun.star.text.TextDocument"):
            docs[comp.getTitle()] = comp
    return docs


def get_doc_by_name(desktop, name: str):
    """Busca un documento Writer abierto cuyo título contenga `name`."""
    docs = list_open_documents(desktop)
    for title, comp in docs.items():
        if name.lower() in title.lower():
            return comp
    abiertos = ", ".join(docs.keys()) if docs else "ninguno"
    raise ValueError(
        f"No hay un documento abierto que coincida con '{name}'. "
        f"Abiertos: {abiertos}. Usa open_document primero. {STOP_MSG}"
    )


def iter_paragraphs(doc):
    """Recorre los párrafos del cuerpo del documento (omite tablas)."""
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        p = enum.nextElement()
        if p.supportsService("com.sun.star.text.Paragraph"):
            yield p


def match_paragraphs(doc, fragments: list[str]):
    """
    Devuelve (párrafos que contienen alguno de los fragmentos, fragmentos sin coincidencia).
    La comparación no distingue mayúsculas.
    """
    wanted = [f.lower() for f in fragments]
    found, used = [], set()
    for p in iter_paragraphs(doc):
        s = p.getString().lower()
        for i, f in enumerate(wanted):
            if f and f in s:
                found.append(p)
                used.add(i)
                break
    missing = [fragments[i] for i in range(len(fragments)) if i not in used]
    return found, missing


def check_style(doc, family: str, name: str):
    """Verifica que un estilo exista; si no, informa los disponibles."""
    styles = doc.getStyleFamilies().getByName(family)
    if not styles.hasByName(name):
        available = ", ".join(sorted(styles.getElementNames())[:40])
        raise ValueError(f"El estilo '{name}' no existe en {family}. Disponibles: {available}")


def result_msg(n: int, missing: list[str]) -> str:
    msg = f"{n} párrafo(s) modificados"
    if missing:
        msg += f". Sin coincidencia para: {missing}"
    return msg


# ---------------------------------------------------------------------------
# Documentos
# ---------------------------------------------------------------------------

@mcp.tool()
def open_document(file_path: str) -> str:
    """Abre un .odt. Ruta absoluta o relativa a la carpeta raíz (se admiten subcarpetas)."""
    path = resolve_odt(file_path)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No existe el archivo: {path}. {STOP_MSG}")

    desktop = connect()
    url = uno.systemPathToFileUrl(path)
    for title, comp in list_open_documents(desktop).items():
        if comp.getURL() == url:
            return f"Ya estaba abierto: {title}"

    doc = desktop.loadComponentFromURL(url, "_blank", 0, ())
    if doc is None:
        raise RuntimeError(f"LibreOffice no pudo abrir: {path}. {STOP_MSG}")
    return f"Abierto: {doc.getTitle()}"


@mcp.tool()
def create_document(file_path: str) -> str:
    """Crea un .odt nuevo y vacío y lo deja abierto. Nunca sobrescribe un archivo existente."""
    path = resolve_odt(file_path)
    if os.path.exists(path):
        raise FileExistsError(
            f"Ya existe: {path}. No se sobrescribe. "
            f"Usa open_document para editarlo o pide al usuario otro nombre."
        )
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        raise FileNotFoundError(f"La carpeta no existe: {parent}. {STOP_MSG}")

    desktop = connect()
    doc = desktop.loadComponentFromURL("private:factory/swriter", "_blank", 0, ())
    filtro = uno.createUnoStruct("com.sun.star.beans.PropertyValue")
    filtro.Name = "FilterName"
    filtro.Value = "writer8"  # nombre interno del formato ODT de Writer
    doc.storeAsURL(uno.systemPathToFileUrl(path), (filtro,))
    return f"Creado y abierto: {doc.getTitle()} -> {path}"


@mcp.tool()
def list_documents() -> str:
    """Lista los documentos Writer abiertos (título y ruta)."""
    docs = list_open_documents(connect())
    if not docs:
        return "No hay documentos abiertos. Usa open_document."
    lines = []
    for title, comp in docs.items():
        url = comp.getURL()
        lines.append(f"{title} -> {uno.fileUrlToSystemPath(url) if url else '(sin guardar)'}")
    return "\n".join(lines)


@mcp.tool()
def save_document(document_name: str) -> str:
    """Guarda el documento en su archivo original."""
    doc = get_doc_by_name(connect(), document_name)
    if not doc.hasLocation():
        raise ValueError(f"El documento no tiene ruta en disco. {STOP_MSG}")
    path = resolve_odt(uno.fileUrlToSystemPath(doc.getURL()))
    doc.store()
    return f"Guardado: {path}"


@mcp.tool()
def close_document(document_name: str, save_first: bool = False) -> str:
    """Cierra un documento. save_first=True lo guarda antes."""
    doc = get_doc_by_name(connect(), document_name)
    title = doc.getTitle()
    if save_first and doc.hasLocation():
        doc.store()
    doc.close(True)
    return f"Cerrado: {title}"


# ---------------------------------------------------------------------------
# Lectura (preferir las opciones parciales: gastan menos tokens)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_last_paragraphs(document_name: str, count: int = 5) -> str:
    """Devuelve solo los últimos N párrafos. Preferir a get_document_text."""
    doc = get_doc_by_name(connect(), document_name)
    paras = []
    enum = doc.getText().createEnumeration()
    while enum.hasMoreElements():
        p = enum.nextElement()
        if p.supportsService("com.sun.star.text.Paragraph"):
            paras.append(p.getString())
    return "\n".join(paras[-count:])


@mcp.tool()
def get_document_text(document_name: str) -> str:
    """Texto completo. Costoso: usar solo si se necesita el contenido entero."""
    return get_doc_by_name(connect(), document_name).getText().getString()


# ---------------------------------------------------------------------------
# Edición de texto
# ---------------------------------------------------------------------------

@mcp.tool()
def find_and_replace(document_name: str, search: str, replace: str,
                     replace_all: bool = False, match_case: bool = True) -> str:
    """Busca y reemplaza texto. Para BORRAR un texto usa replace="". No requiere leer el documento."""
    doc = get_doc_by_name(connect(), document_name)
    desc = doc.createReplaceDescriptor()
    desc.SearchString = search
    desc.ReplaceString = replace
    desc.SearchCaseSensitive = match_case
    if replace_all:
        return f"{doc.replaceAll(desc)} reemplazo(s)"
    found = doc.findFirst(desc)
    if found is None:
        return "0 coincidencias"
    found.setString(replace)
    return "1 reemplazo"


@mcp.tool()
def append_text(document_name: str, text: str, new_paragraph: bool = True) -> str:
    """Agrega texto al final del documento."""
    doc = get_doc_by_name(connect(), document_name)
    body = doc.getText()
    cursor = body.createTextCursorByRange(body.getEnd())
    if new_paragraph:
        body.insertControlCharacter(
            cursor,
            uno.getConstantByName("com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK"),
            False,
        )
    body.insertString(cursor, text, False)
    return "ok"


@mcp.tool()
def delete_last_paragraph(document_name: str) -> str:
    """Elimina el último párrafo completo (deshace un append_text)."""
    doc = get_doc_by_name(connect(), document_name)
    body = doc.getText()
    cursor = body.createTextCursorByRange(body.getEnd())
    cursor.gotoStartOfParagraph(True)
    removed = cursor.getString()
    cursor.setString("")
    if cursor.goLeft(1, True):  # borra también el salto de párrafo anterior
        cursor.setString("")
    return f"Eliminado: {removed!r}"


@mcp.tool()
def insert_paragraph_after(document_name: str, after_text: str, text: str) -> str:
    """Inserta un párrafo nuevo después del primer párrafo que contiene after_text."""
    doc = get_doc_by_name(connect(), document_name)
    found, _ = match_paragraphs(doc, [after_text])
    if not found:
        return f"No hay ningún párrafo que contenga: {after_text!r}"
    body = doc.getText()
    cursor = body.createTextCursorByRange(found[0].getEnd())
    body.insertControlCharacter(
        cursor,
        uno.getConstantByName("com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK"),
        False,
    )
    body.insertString(cursor, text, False)
    return "ok"


# ---------------------------------------------------------------------------
# Formato (sin selección: el texto se ubica buscándolo)
# ---------------------------------------------------------------------------

@mcp.tool()
def set_char_format(document_name: str, text: str, bold: bool = None,
                    italic: bool = None, underline: bool = None,
                    all_occurrences: bool = True) -> str:
    """
    Aplica negrita/cursiva/subrayado a las apariciones de `text`.
    Los parámetros en None no se modifican.
    """
    if bold is None and italic is None and underline is None:
        raise ValueError("Indica al menos uno: bold, italic o underline.")
    doc = get_doc_by_name(connect(), document_name)
    desc = doc.createSearchDescriptor()
    desc.SearchString = text
    desc.SearchCaseSensitive = True
    if all_occurrences:
        hits = doc.findAll(desc)
        ranges = [hits.getByIndex(i) for i in range(hits.getCount())]
    else:
        first = doc.findFirst(desc)
        ranges = [first] if first is not None else []
    for r in ranges:
        if bold is not None:
            r.CharWeight = 150.0 if bold else 100.0
        if italic is not None:
            r.CharPosture = uno.Enum("com.sun.star.awt.FontSlant", "ITALIC" if italic else "NONE")
        if underline is not None:
            r.CharUnderline = 1 if underline else 0
    return f"{len(ranges)} aparición(es) formateadas" if ranges else f"No se encontró: {text!r}"


@mcp.tool()
def set_paragraph_style(document_name: str, style_name: str,
                        paragraphs_containing: list[str]) -> str:
    """
    Aplica un estilo de párrafo (ej. 'Heading 1', 'Standard') a los párrafos
    que contienen alguno de los textos indicados.
    """
    doc = get_doc_by_name(connect(), document_name)
    check_style(doc, "ParagraphStyles", style_name)
    found, missing = match_paragraphs(doc, paragraphs_containing)
    for p in found:
        p.ParaStyleName = style_name
    return result_msg(len(found), missing)


LIST_STYLES = {"bullet": "List 1", "number": "Numbering 123"}


@mcp.tool()
def set_list(document_name: str, kind: str, paragraphs_containing: list[str]) -> str:
    """
    Convierte en lista real los párrafos que contienen alguno de los textos.
    kind: 'bullet' (viñetas), 'number' (numerada) o 'none' (quitar lista).
    No escribas "•" a mano: usa esta herramienta.
    """
    if kind not in ("bullet", "number", "none"):
        raise ValueError("kind debe ser 'bullet', 'number' o 'none'")
    doc = get_doc_by_name(connect(), document_name)
    style = "" if kind == "none" else LIST_STYLES[kind]
    if style:
        check_style(doc, "NumberingStyles", style)
    found, missing = match_paragraphs(doc, paragraphs_containing)
    for p in found:
        p.NumberingStyleName = style
    return result_msg(len(found), missing)


# ---------------------------------------------------------------------------
# Tablas
# ---------------------------------------------------------------------------

@mcp.tool()
def insert_table(document_name: str, rows: int, cols: int,
                 data: list[list[str]] = None, after_text: str = None) -> str:
    """
    Inserta una tabla al final del documento, o después del párrafo que
    contiene after_text si se indica. data opcional: lista de filas.
    """
    doc = get_doc_by_name(connect(), document_name)
    body = doc.getText()
    if after_text:
        found, _ = match_paragraphs(doc, [after_text])
        if not found:
            return f"No hay ningún párrafo que contenga: {after_text!r}"
        cursor = body.createTextCursorByRange(found[0].getEnd())
    else:
        cursor = body.createTextCursorByRange(body.getEnd())
    body.insertControlCharacter(
        cursor,
        uno.getConstantByName("com.sun.star.text.ControlCharacter.PARAGRAPH_BREAK"),
        False,
    )
    table = doc.createInstance("com.sun.star.text.TextTable")
    table.initialize(rows, cols)
    body.insertTextContent(cursor, table, False)
    if data:
        for r, row in enumerate(data[:rows]):
            for c, value in enumerate(row[:cols]):
                table.getCellByName(f"{chr(65 + c)}{r + 1}").setString(str(value))
    return f"ok: {table.getName()}"


@mcp.tool()
def list_tables(document_name: str) -> str:
    """Nombres de las tablas del documento."""
    tables = get_doc_by_name(connect(), document_name).getTextTables()
    return "\n".join(tables.getElementNames()) if tables.getCount() else "No hay tablas"


@mcp.tool()
def set_table_cell(document_name: str, table_name: str, cell: str, value: str) -> str:
    """Escribe en una celda (ej. 'B3')."""
    doc = get_doc_by_name(connect(), document_name)
    doc.getTextTables().getByName(table_name).getCellByName(cell).setString(value)
    return "ok"


@mcp.tool()
def get_table_cell(document_name: str, table_name: str, cell: str) -> str:
    """Lee una celda."""
    doc = get_doc_by_name(connect(), document_name)
    return doc.getTextTables().getByName(table_name).getCellByName(cell).getString()


if __name__ == "__main__":
    mcp.run()
