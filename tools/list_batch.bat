@echo off
cd /d "C:\Users\prash\Kiro Projects\BeatCanvas"
dir /b "C:\Users\prash\Pictures\Batch" > _logs\batch_list.txt 2>&1
echo --- videos --- >> _logs\batch_list.txt
dir /b /s "C:\Users\prash\Pictures\Batch\*.mp4" >> _logs\batch_list.txt 2>&1
