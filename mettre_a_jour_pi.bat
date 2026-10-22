@echo off
rem Envoie le code du site musique sur le Pi et redemarre le service (music.dwa.gold).
rem Le .env, le venv et la base music.db restent sur le Pi, intacts.
cd /d "%~dp0"
set PI=pi@192.168.1.73
tar czf "%TEMP%\anime-music.tgz" --exclude=__pycache__ app static templates requirements.txt set_spotify.py || goto :err
scp "%TEMP%\anime-music.tgz" %PI%:/tmp/anime-music.tgz || goto :err
ssh %PI% "mkdir -p ~/anime-music && tar xzf /tmp/anime-music.tgz -C ~/anime-music && rm /tmp/anime-music.tgz && cd ~/anime-music && venv/bin/pip install -q -r requirements.txt && sudo systemctl restart anime-music && sleep 2 && systemctl is-active anime-music" || goto :err
echo.
echo OK : https://music.dwa.gold
pause
exit /b 0
:err
echo.
echo ECHEC de la mise a jour (Pi joignable ? ssh %PI%)
pause
exit /b 1
