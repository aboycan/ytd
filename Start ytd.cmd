@echo off
rem Double-click this file to use ytd without typing commands.
rem It opens interactive mode: paste a YouTube link, choose video or audio.
title ytd - YouTube downloader
call "%~dp0ytd.cmd" %*
echo.
echo Files are saved in: "%~dp0downloads"
pause
