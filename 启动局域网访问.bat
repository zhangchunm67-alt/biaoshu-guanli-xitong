@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem 远程访问（腾宝AI WiFi 局域网 / 节点 / 花生壳）。配置写在 .env.oray，详见《局域网与节点访问方案.md》。
if not exist .env.oray (
  echo 未找到 .env.oray。请复制 .env.oray.example 为 .env.oray，填写访问地址与访问口令。
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -c "import olefile, pypdf, pypdfium2" >nul 2>&1
if errorlevel 1 .venv\Scripts\python.exe -m pip install -r requirements.txt
echo 保留这个窗口以持续运行；关闭窗口即停止服务。
.venv\Scripts\python.exe server.py --env-file .env.oray --open
pause
