@echo off
rem Launch WorldClock without a console window
set "PYW=C:\Users\admin\AppData\Local\Programs\Python\Python313\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw.exe"
start "" "%PYW%" "%~dp0worldclock.pyw"
