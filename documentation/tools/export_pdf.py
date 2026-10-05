"""Refresh the guide's table of contents and export it to PDF.

    /usr/bin/python3 documentation/tools/export_pdf.py

Opens i-CONNECTS_User_Guide.docx in a headless LibreOffice (through its UNO
API, so it needs the system Python with python3-uno and libreoffice-writer),
updates every index — the table of contents is written empty and filled in
here — saves the .docx back with the filled-in contents, and exports
i-CONNECTS_User_Guide.pdf next to it.

Editing the guide in Word instead? Right-click the table of contents, choose
Update Field, and use File > Save As > PDF; this script is not needed then.
"""

import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile

import uno
from com.sun.star.beans import PropertyValue

HERE = os.path.dirname(os.path.abspath(__file__))
DOCX = os.path.abspath(os.path.join(HERE, "..", "i-CONNECTS_User_Guide.docx"))
PDF = DOCX[:-5] + ".pdf"
PORT = 2002


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def fix_section_breaks(path):
    """Move each paragraph's <w:sectPr> to the end of its <w:pPr>.

    LibreOffice writes a section break ahead of the paragraph's style, which
    the Office Open XML schema does not allow and Word may flag as damaged.
    """
    def reorder(m):
        ppr = m.group(0)
        sect = re.search(r"<w:sectPr\b.*?</w:sectPr>", ppr, re.S)
        if not sect:
            return ppr
        rest = ppr.replace(sect.group(0), "", 1)
        return rest.replace("</w:pPr>", sect.group(0) + "</w:pPr>")

    tmp = path + ".tmp"
    with zipfile.ZipFile(path) as src, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "word/document.xml":
                data = re.sub(r"<w:pPr>.*?</w:pPr>", reorder, data.decode("utf-8"), flags=re.S).encode("utf-8")
            dst.writestr(item, data)
    shutil.move(tmp, path)


def main():
    profile = tempfile.mkdtemp(prefix="lo_profile_")
    office = subprocess.Popen([
        "soffice", "--headless", "--norestore", "--invisible",
        f"-env:UserInstallation=file://{profile}",
        f"--accept=socket,host=localhost,port={PORT};urp;",
    ])
    try:
        resolver = uno.getComponentContext().ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", uno.getComponentContext())
        for _ in range(60):
            try:
                ctx = resolver.resolve(
                    f"uno:socket,host=localhost,port={PORT};urp;StarOffice.ComponentContext")
                break
            except Exception:  # noqa: BLE001 — office still starting
                time.sleep(1)
        else:
            raise SystemExit("LibreOffice did not start")
        desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
        doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(DOCX), "_blank", 0,
                                           (prop("Hidden", True),))
        indexes = doc.getDocumentIndexes()
        for i in range(indexes.getCount()):
            indexes.getByIndex(i).update()
        doc.storeToURL(uno.systemPathToFileUrl(DOCX), (prop("FilterName", "MS Word 2007 XML"),))
        doc.storeToURL(uno.systemPathToFileUrl(PDF), (
            prop("FilterName", "writer_pdf_Export"),
            prop("FilterData", uno.Any("[]com.sun.star.beans.PropertyValue", tuple([
                prop("ExportBookmarks", True),       # PDF outline from the headings
                # Screenshots are captured at 2x; 300 dpi at JPEG quality 92
                # keeps their text sharp at about half the file size.
                prop("Quality", 92),
                prop("ReduceImageResolution", True),
                prop("MaxImageResolution", 300),
            ]))),
        ))
        doc.close(True)
        fix_section_breaks(DOCX)
        print("Wrote", DOCX)
        print("Wrote", PDF)
    finally:
        office.terminate()
        office.wait(timeout=30)


if __name__ == "__main__":
    main()
