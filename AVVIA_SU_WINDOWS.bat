@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   DiegoTV - EPG unificata
 echo ========================================
py -3 scripts\crea_epg.py --playlist DiegoTV.m3u --output docs\epg.xml.gz --report docs\report.csv
if errorlevel 1 (
 echo.
 echo La creazione non e' riuscita. Controlla la rete e i messaggi.
) else (
 echo.
 echo Creata docs\epg.xml.gz: contiene la guida unificata.
 echo Trovi il riepilogo in docs\report.csv
)
echo.
pause
