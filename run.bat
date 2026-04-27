@echo off
setlocal

echo Starting DataLens AI...
echo.

REM Check for .env in backend
if not exist "backend\.env" (
    echo [WARN] backend\.env not found. Copying from .env.example...
    copy "backend\.env.example" "backend\.env" >nul
    echo [WARN] Please edit backend\.env and add your GROQ_API_KEY before using the app.
    echo.
)

REM Cache backend: peek at .env for CACHE_BACKEND. If it's "fakeredis" or
REM "memory", skip the Docker Redis startup entirely — the app handles the
REM cache itself. Only when CACHE_BACKEND=redis (or unset) do we try Docker.
set "CACHE_BACKEND_VAL=redis"
if exist "backend\.env" (
    for /f "usebackq tokens=1,* delims==" %%a in (`findstr /B /I "CACHE_BACKEND=" "backend\.env" 2^>nul`) do (
        set "CACHE_BACKEND_VAL=%%b"
    )
)

if /I "%CACHE_BACKEND_VAL%"=="fakeredis" (
    echo [opt] CACHE_BACKEND=fakeredis — using in-process Redis. Skipping Docker.
) else if /I "%CACHE_BACKEND_VAL%"=="memory" (
    echo [opt] CACHE_BACKEND=memory — using in-process TTL cache only. Skipping Docker.
) else (
    REM Optional: Redis (improves cache performance and shared cache across workers).
    REM If Redis isn't running, the backend transparently falls back to an in-memory
    REM cache so the app keeps working — no setup required.
    where docker >nul 2>nul
    if %ERRORLEVEL% EQU 0 (
        docker info >nul 2>nul
        if errorlevel 1 (
            echo [opt] Docker is installed but the daemon isn't running.
            echo        Start Docker Desktop, or set CACHE_BACKEND=fakeredis in backend\.env.
        ) else (
            docker ps --format "{{.Names}}" 2>nul | findstr /B /C:"datalens-redis" >nul
            if errorlevel 1 (
                echo [opt] Starting Redis container ^(datalens-redis^)...
                docker run -d --name datalens-redis -p 6379:6379 --restart unless-stopped redis:7-alpine >nul 2>&1
                if errorlevel 1 (
                    echo [opt] Could not start Redis. The app will use in-memory cache fallback.
                ) else (
                    echo [opt] Redis ready at localhost:6379.
                )
            ) else (
                echo [opt] Redis already running.
            )
        )
    ) else (
        echo [opt] Docker not found. The app will use the in-memory cache fallback.
    )
)

REM Start backend
echo [1/2] Starting backend (FastAPI)...
start "Backend" cmd /k "cd /d %~dp0backend && .venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"

REM Give backend a moment to bind
timeout /t 2 /nobreak >nul

REM Start frontend
echo [2/2] Starting frontend (Vite)...
start "Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Both servers are starting in separate windows.
echo   Backend  : http://localhost:8000
echo   Frontend : http://localhost:5173
echo.
echo Close those windows (or press Ctrl+C in each) to stop the servers.
pause
