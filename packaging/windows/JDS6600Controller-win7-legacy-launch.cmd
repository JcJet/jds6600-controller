@echo off
setlocal
set "APPDIR=%~dp0"
set "EXE=%APPDIR%JDS6600Controller-win7-legacy.exe"

if not exist "%EXE%" (
  call :showmsg "JDS6600Controller-win7-legacy.exe was not found next to this launcher."
  exit /b 1
)

ver | find "6.1." >nul 2>nul
if %errorlevel%==0 (
  wmic qfe get HotFixID 2>nul | find /I "KB2533623" >nul
  if errorlevel 1 (
    call :showmsg "Windows 7 update KB2533623 appears to be missing. Please install KB2533623 from Microsoft, then run this launcher again."
    exit /b 1
  )
)

start "" "%EXE%"
exit /b 0

:showmsg
set "MSG=%~1"
if exist "%SystemRoot%\System32\mshta.exe" (
  mshta vbscript:Execute("MsgBox ""%MSG%"",16,""JDS6600 Controller"":close")
) else (
  echo %MSG%
  pause
)
exit /b 0
