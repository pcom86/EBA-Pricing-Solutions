@echo off
setlocal
pushd "%~dp0" || exit /b 1
if not exist "logs\." mkdir "logs" || exit /b 1
set "runlog=logs\pricing-%RANDOM%-%RANDOM%.log"
echo Run log: "%CD%\%runlog%"
call :log Starting local pricing launcher.
if not defined GAP_HOST_OUTPUT set "GAP_HOST_OUTPUT=%CD%\pricing_output"
if not exist "%GAP_HOST_OUTPUT%\." (
    mkdir "%GAP_HOST_OUTPUT%" || goto :output_failed
)
set "phase=Local image preparation"
if not defined GAP_LOCAL_PYTHON set "GAP_LOCAL_PYTHON=python.exe"
"%GAP_LOCAL_PYTHON%" -B -u "%~dp0deployments\local_build.py" "%runlog%"
if errorlevel 1 goto :finished
set "phase=Pricing container"
call :log Starting the pricing container. Live engine output follows.
docker compose run --rm -T pricing %*
:finished
set "result=%ERRORLEVEL%"
call :log %phase% exited with code %result%.
popd
exit /b %result%

:output_failed
call :log Cannot create the reports folder. Check GAP_HOST_OUTPUT and folder permissions.
popd
exit /b 1

:log
echo [%DATE% %TIME%] %*
>>"%runlog%" echo [%DATE% %TIME%] %*
exit /b 0
