"""Normalize supported Word uploads without modifying the source file."""
from __future__ import annotations

import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


def find_converter():
    candidates = [
        os.environ.get('BID_LIBREOFFICE'),
        str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice'),
        shutil.which('soffice'),
        shutil.which('libreoffice'),
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
    ]
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)'):
        if os.environ.get(variable):
            candidates.append(str(Path(os.environ[variable]) / 'LibreOffice/program/soffice.exe'))
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)


def normalize_word(name, raw, limit):
    suffix = Path(name).suffix.lower()
    if suffix not in {'.doc', '.docx'}:
        raise ValueError('模板仅支持 .doc 或 .docx 格式的 Word 文件')
    if not raw or len(raw) > limit:
        raise ValueError(f'Word 模板不能为空，且不能超过 {limit // 1024 // 1024} MB')
    # Some procurement sites serve an OOXML document with a legacy .doc suffix.
    # Keep its bytes unchanged; the caller still performs all DOCX validation.
    if suffix == '.docx' or (raw.startswith(b'PK\x03\x04') and zipfile.is_zipfile(io.BytesIO(raw))):
        return raw, 'docx' if suffix == '.docx' else 'docx_with_doc_suffix'
    if not raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        raise ValueError('此 .doc 不是受支持的 Word 文件，请在 Word 中另存为 .docx 后上传')
    try:
        import olefile
    except ImportError:
        raise ValueError('旧版 .doc 支持组件未安装，请安装 requirements.txt 中的依赖，或在 Word 中另存为 .docx 后上传') from None
    try:
        with olefile.OleFileIO(io.BytesIO(raw)) as document:
            paths = document.listdir(streams=True, storages=True)
            if document.exists('EncryptedPackage'):
                raise ValueError('不支持加密的 Word 文件，请先在 Word 中解除密码后上传')
            if any(part.lower() in {'macros', 'vba', '_vba_project_cur', 'objectpool'} for path in paths for part in path):
                raise ValueError('模板包含宏或嵌入文件，请移除后上传')
            if not document.exists('WordDocument'):
                raise ValueError('文件不是有效的 Word 文档')
            fib = document.openstream('WordDocument').read(32)
            if len(fib) < 32 or fib[:2] != b'\xec\xa5':
                raise ValueError('此旧版 Word 格式不受支持，请在 Word 中另存为 .docx 后上传')
            if int.from_bytes(fib[10:12], 'little') & 0x8100:
                raise ValueError('不支持加密的 Word 文件，请先在 Word 中解除密码后上传')
    except ValueError:
        raise
    except Exception:
        raise ValueError('旧版 Word 文件损坏或格式不正确，请在 Word 中另存为 .docx 后上传') from None
    converter = find_converter()
    if not converter:
        raise ValueError('未找到本机 Word 格式转换工具，请安装 LibreOffice，或在 Word 中另存为 .docx 后上传')
    with tempfile.TemporaryDirectory(prefix='bid-word-import-') as directory:
        root = Path(directory)
        source = root / 'template.doc'
        source.write_bytes(raw)
        profile = root / 'profile'
        (profile / 'user').mkdir(parents=True)
        (profile / 'user/registrymodifications.xcu').write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
            '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
            '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
            '</item></oor:items>', encoding='utf-8')
        try:
            result = subprocess.run([
                converter, f'-env:UserInstallation={profile.as_uri()}',
                '--headless', '--nologo', '--nodefault', '--norestore',
                '--convert-to', 'docx:Office Open XML Text',
                '--outdir', str(root), str(source),
            ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=60, check=False)
        except subprocess.TimeoutExpired:
            raise ValueError('旧版 Word 转换超时，请在 Word 中另存为 .docx 后上传') from None
        except OSError:
            raise ValueError('本机 Word 格式转换工具无法启动，请在 Word 中另存为 .docx 后上传') from None
        output = root / 'template.docx'
        if result.returncode or not output.is_file() or not output.stat().st_size:
            raise ValueError('旧版 Word 转换失败，请在 Word 中另存为 .docx 后上传')
        if output.stat().st_size > limit:
            raise ValueError(f'Word 转换后超过 {limit // 1024 // 1024} MB，请精简模板后上传')
        return output.read_bytes(), 'converted_doc'
