"""Read factual XLSX cell values with the standard library; never execute macros.

Times remain Excel day fractions. Caller must apply service-day rollover and
interpret arrival/departure row pairs according to each official workbook.
"""
from pathlib import PurePosixPath
from zipfile import ZipFile
import re
import xml.etree.ElementTree as ET

MAIN='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS={'m':MAIN}


def sheets(path):
    with ZipFile(path) as archive:
        strings=[]
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings=[''.join(node.itertext()) for node in ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        relations={node.attrib['Id']:node.attrib['Target'] for node in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        workbook=ET.fromstring(archive.read('xl/workbook.xml'))
        for sheet in workbook.find('m:sheets',NS):
            target=relations[sheet.attrib['{'+REL+'}id']]
            target=target.lstrip('/') if target.startswith('/') else 'xl/'+target
            if '..' in PurePosixPath(target).parts:
                raise ValueError('Unexpected workbook relationship')
            content=ET.fromstring(archive.read(target))
            result=[]
            for row in content.findall('m:sheetData/m:row',NS):
                values={}
                for cell in row.findall('m:c',NS):
                    match=re.fullmatch(r'([A-Z]+)(\d+)',cell.attrib['r'])
                    column=0
                    for letter in match[1]:
                        column=column*26+ord(letter)-64
                    value=cell.find('m:v',NS)
                    value=value.text if value is not None else None
                    kind=cell.attrib.get('t')
                    if kind=='s' and value is not None:
                        value=strings[int(value)]
                    elif kind=='inlineStr':
                        value=''.join(cell.itertext())
                    elif kind not in ('s','str','inlineStr','e') and value is not None:
                        try:
                            value=float(value)
                        except ValueError:
                            pass
                    if value is not None:
                        values[column]=value
                if values:
                    result.append((int(row.attrib['r']),values))
            yield sheet.attrib['name'],result
