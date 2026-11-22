@echo off
title PodMedia LAN Access Fixer
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0fix_plex_access.ps1\"'"
