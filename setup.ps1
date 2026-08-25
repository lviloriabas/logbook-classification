<#
.SYNOPSIS
    Reconstruye el entorno portable descargando todo lo que el repositorio no
    versiona: Python 3.12, las dependencias, Tesseract y los modelos OCR.

.DESCRIPTION
    El repositorio guarda solo codigo fuente. La carpeta portable\ (unos 2 GB)
    queda fuera de git por peso, asi que en una maquina nueva hay que traerla:

        portable\python312   interprete Python 3.12 + dependencias (pip)
        portable\tesseract   Tesseract 5.4 con tessdata eng/osd
        portable\paddlex     modelos PaddleOCR (detector + reconocedor)
        portable\llama       modelos del verificador VLM (opcional)

    El script es idempotente: lo que ya esta completo no se vuelve a bajar.
    Necesita internet solo mientras corre; despues la aplicacion trabaja
    offline, como exige el paquete portable.

.PARAMETER Check
    No descarga nada: solo informa que componentes estan presentes.

.PARAMETER SkipTesseract
    Omite Tesseract (237 MB). La GUI y el CLI no lo usan; hace falta para el
    OCR de respaldo por campo y para tools\evaluate_date_images.py.

.PARAMETER Vlm
    Descarga tambien los modelos del verificador VLM (varios GB).

.PARAMETER Launcher
    Regenera LogbookClassification.exe con PyInstaller al terminar.

.PARAMETER Force
    Rehace los componentes aunque ya esten instalados.

.PARAMETER CleanCache
    Borra portable\.cache (instaladores descargados) al terminar.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File setup.ps1 -Check
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$SkipTesseract,
    [switch]$Vlm,
    [switch]$Launcher,
    [switch]$Force,
    [switch]$CleanCache
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --- Versiones fijas -------------------------------------------------------
# Son exactamente las del portable verificado. PaddlePaddle no publica ruedas
# para Python 3.14, por eso 3.12 (ver README).
$PythonVersion = '3.12.10'
$PythonUrl = "https://globalcdn.nuget.org/packages/python.$PythonVersion.nupkg"
$PythonSha = '0EB85C2DFCCCCF1B17352DE4C397F69194035B7D37149EACC16F1147D93DE3B8'

$TesseractVersion = '5.4.0.20240606'
$TesseractUrl = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-$TesseractVersion.exe"
$TesseractSha = 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9'

# Los mismos nombres que fija app/ocr/engine.py y descarga tools\precache_paddle.py.
$PaddleModels = @('PP-OCRv6_medium_det', 'PP-OCRv5_mobile_rec')

# --- Rutas -----------------------------------------------------------------
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Portable = Join-Path $Root 'portable'
$Cache = Join-Path $Portable '.cache'
$PythonDir = Join-Path $Portable 'python312'
$PythonExe = Join-Path $PythonDir 'tools\python.exe'
$TessDir = Join-Path $Portable 'tesseract'
$TessExe = Join-Path $TessDir 'tesseract.exe'
$ModelsDir = Join-Path $Portable 'paddlex\official_models'
$LlamaDir = Join-Path $Portable 'llama'
$LauncherExe = Join-Path $Root 'LogbookClassification.exe'

# --- Salida ----------------------------------------------------------------

function Write-Paso {
    param([string]$Texto)
    Write-Host ""
    Write-Host "==> $Texto" -ForegroundColor Cyan
}

function Write-Detalle {
    param([string]$Texto)
    Write-Host "    $Texto" -ForegroundColor DarkGray
}

function Write-Listo {
    param([string]$Texto)
    Write-Host "    $Texto" -ForegroundColor Green
}

function Write-Aviso {
    param([string]$Texto)
    Write-Host "    $Texto" -ForegroundColor Yellow
}

# --- Utilidades ------------------------------------------------------------

function Invoke-Programa {
    <#
        Ejecuta un programa externo callando su salida y devuelve si termino
        bien. Baja ErrorActionPreference mientras corre porque, con 'Stop',
        PowerShell 5.1 convierte cualquier linea de stderr en error terminante.
    #>
    param(
        [string]$Ruta,
        [string[]]$Argumentos
    )
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Ruta @Argumentos 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
    finally {
        $ErrorActionPreference = $anterior
    }
}

function Get-SalidaPrograma {
    <#
        Igual que Invoke-Programa pero devuelve las lineas que imprimio el
        programa (stdout y stderr juntos).
    #>
    param(
        [string]$Ruta,
        [string[]]$Argumentos
    )
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        return (& $Ruta @Argumentos 2>&1)
    }
    finally {
        $ErrorActionPreference = $anterior
    }
}

function Test-PythonPortable {
    if (-not (Test-Path $PythonExe)) { return $false }
    return (Invoke-Programa -Ruta $PythonExe -Argumentos @(
            '-c', "import sys; assert sys.version_info[:2] == (3, 12)"))
}

function Test-Dependencias {
    if (-not (Test-Path $PythonExe)) { return $false }
    # Imports representativos, incluido el puente al almacen TLS de Windows.
    return (Invoke-Programa -Ruta $PythonExe -Argumentos @(
            '-c', 'import paddleocr, fitz, PySide6, truststore'))
}

function Test-Modelos {
    foreach ($modelo in $PaddleModels) {
        $pesos = Join-Path $ModelsDir "$modelo\inference.pdiparams"
        if (-not (Test-Path $pesos)) { return $false }
    }
    return $true
}

function Test-Vlm {
    $modelos = Join-Path $LlamaDir 'models'
    if (-not (Test-Path $modelos)) { return $false }
    $gguf = @(Get-ChildItem -Path $modelos -Filter '*.gguf' -File -ErrorAction SilentlyContinue)
    $texto = @($gguf | Where-Object { $_.Name -notmatch 'mmproj' })
    $proyector = @($gguf | Where-Object { $_.Name -match 'mmproj' })
    return ($texto.Count -gt 0 -and $proyector.Count -gt 0)
}

function Get-TamanoMB {
    param([string]$Ruta)
    if (-not (Test-Path $Ruta)) { return 0 }
    $bytes = (Get-ChildItem -Path $Ruta -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum).Sum
    if ($null -eq $bytes) { return 0 }
    return [math]::Round($bytes / 1MB)
}

function Get-Descarga {
    <#
        Descarga con reanudacion y verifica SHA256. El archivo final solo
        aparece cuando el hash coincide, asi que una descarga cortada nunca
        se confunde con un instalador completo.
    #>
    param(
        [string]$Url,
        [string]$Destino,
        [string]$Sha256
    )

    if (Test-Path $Destino) {
        $hash = (Get-FileHash -Path $Destino -Algorithm SHA256).Hash
        if ($hash -eq $Sha256) {
            Write-Detalle "ya descargado: $(Split-Path -Leaf $Destino)"
            return
        }
        Write-Aviso "hash distinto en la copia guardada, se descarga otra vez"
        Remove-Item -LiteralPath $Destino -Force
    }

    New-Item -ItemType Directory -Path (Split-Path -Parent $Destino) -Force | Out-Null
    $parcial = "$Destino.part"
    Write-Detalle "bajando $Url"

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        & $curl.Source --location --fail --retry 3 --continue-at - `
            --output $parcial $Url
        if ($LASTEXITCODE -ne 0) {
            throw "Fallo la descarga de $Url (curl $LASTEXITCODE)."
        }
    }
    else {
        if (Test-Path $parcial) { Remove-Item -LiteralPath $parcial -Force }
        Invoke-WebRequest -Uri $Url -OutFile $parcial -UseBasicParsing
    }

    $hash = (Get-FileHash -Path $parcial -Algorithm SHA256).Hash
    if ($hash -ne $Sha256) {
        Remove-Item -LiteralPath $parcial -Force
        throw ("El archivo bajado de $Url no coincide con el hash esperado " +
            "($hash != $Sha256). Vuelva a intentarlo.")
    }
    Move-Item -LiteralPath $parcial -Destination $Destino -Force
}

function Find-SevenZip {
    $comando = Get-Command '7z.exe' -ErrorAction SilentlyContinue
    if ($comando) { return $comando.Source }
    $candidatos = @(
        (Join-Path $env:ProgramFiles '7-Zip\7z.exe'),
        (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\7-Zip\7z.exe')
    )
    foreach ($ruta in $candidatos) {
        if ($ruta -and (Test-Path $ruta)) { return $ruta }
    }
    return $null
}

# --- Componentes -----------------------------------------------------------

function Install-Python {
    Write-Paso "Python $PythonVersion portable"
    if ((Test-PythonPortable) -and -not $Force) {
        Write-Listo "ya esta en portable\python312"
        return
    }
    $paquete = Join-Path $Cache "python.$PythonVersion.nupkg.zip"
    Get-Descarga -Url $PythonUrl -Destino $paquete -Sha256 $PythonSha

    # El paquete NuGet trae el interprete completo bajo tools\, que es la
    # ruta que usan el launcher, el README y los scripts de tools\.
    $temporal = Join-Path $Cache "python-$PythonVersion-extract"
    if (Test-Path $temporal) { Remove-Item -LiteralPath $temporal -Recurse -Force }
    Write-Detalle 'descomprimiendo'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($paquete, $temporal)

    $destino = Join-Path $PythonDir 'tools'
    if (Test-Path $destino) { Remove-Item -LiteralPath $destino -Recurse -Force }
    New-Item -ItemType Directory -Path $PythonDir -Force | Out-Null
    Move-Item -LiteralPath (Join-Path $temporal 'tools') -Destination $destino
    Remove-Item -LiteralPath $temporal -Recurse -Force

    # El paquete NuGet no trae pip; ensurepip lo instala desde la rueda incluida.
    Write-Detalle 'instalando pip'
    & $PythonExe -m ensurepip --upgrade --default-pip
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo instalar pip (ensurepip).' }
    & $PythonExe -m pip install --quiet --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo actualizar pip.' }

    if (-not (Test-PythonPortable)) { throw 'El Python portable no quedo utilizable.' }
    Write-Listo "listo en portable\python312\tools\python.exe"
}

function Install-Dependencias {
    Write-Paso 'Dependencias de Python'
    if ((Test-Dependencias) -and -not $Force) {
        Write-Listo 'requirements.txt ya satisfecho'
        return
    }
    $requisitos = Join-Path $Root 'requirements.txt'
    Write-Detalle 'pip install -r requirements.txt (PaddlePaddle y PySide6 tardan)'
    & $PythonExe -m pip install --disable-pip-version-check `
        --no-warn-script-location -r $requisitos
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la instalacion de dependencias.' }
    if (-not (Test-Dependencias)) {
        throw 'Las dependencias se instalaron pero no se pueden importar.'
    }
    Write-Listo 'dependencias instaladas'
}

function Install-Tesseract {
    Write-Paso "Tesseract $TesseractVersion"
    if ((Test-Path $TessExe) -and -not $Force) {
        Write-Listo 'ya esta en portable\tesseract'
        return
    }
    $instalador = Join-Path $Cache "tesseract-ocr-w64-setup-$TesseractVersion.exe"
    Get-Descarga -Url $TesseractUrl -Destino $instalador -Sha256 $TesseractSha

    if (Test-Path $TessDir) { Remove-Item -LiteralPath $TessDir -Recurse -Force }
    New-Item -ItemType Directory -Path $TessDir -Force | Out-Null

    # El instalador es NSIS: 7-Zip lo abre como archivo comprimido y deja los
    # binarios sin tocar el registro ni el menu inicio. Si no hay 7-Zip se cae
    # al modo silencioso del propio instalador, que tampoco pide permisos de
    # administrador cuando el destino es escribible.
    $sevenZip = Find-SevenZip
    if ($sevenZip) {
        Write-Detalle 'extrayendo con 7-Zip'
        Get-SalidaPrograma -Ruta $sevenZip `
            -Argumentos @('x', $instalador, "-o$TessDir", '-y') | Out-Null
        # 1 son advertencias (el instalador trae entradas que 7-Zip no reconoce).
        if ($LASTEXITCODE -gt 1) { throw "7-Zip fallo al extraer ($LASTEXITCODE)." }
        # Restos del instalador que no forman parte de Tesseract.
        foreach ($sobra in @('$PLUGINSDIR', '[NSIS].nsi', 'Uninstall.exe')) {
            $ruta = Join-Path $TessDir $sobra
            if (Test-Path -LiteralPath $ruta) {
                Remove-Item -LiteralPath $ruta -Recurse -Force
            }
        }
    }
    else {
        Write-Detalle 'sin 7-Zip: instalacion silenciosa en portable\tesseract'
        # /D debe ir al final y sin comillas: NSIS toma el resto de la linea.
        $proceso = Start-Process -FilePath $instalador `
            -ArgumentList "/S /D=$TessDir" -Wait -PassThru
        if ($proceso.ExitCode -ne 0) {
            throw "El instalador de Tesseract fallo ($($proceso.ExitCode))."
        }
    }

    if (-not (Test-Path $TessExe)) {
        throw ("No aparecio tesseract.exe en portable\tesseract. Instale 7-Zip " +
            'y repita, o copie la carpeta desde otro portable.')
    }
    $version = Get-SalidaPrograma -Ruta $TessExe -Argumentos @('--version')
    if ($version) { Write-Detalle ($version | Select-Object -First 1) }
    Write-Listo 'listo en portable\tesseract'
}

function Install-Modelos {
    Write-Paso 'Modelos PaddleOCR'
    if ((Test-Modelos) -and -not $Force) {
        Write-Listo "$($PaddleModels -join ' + ') ya estan en portable\paddlex"
        return
    }
    if ($Force) {
        foreach ($modelo in $PaddleModels) {
            $ruta = Join-Path $ModelsDir $modelo
            if (Test-Path $ruta) { Remove-Item -LiteralPath $ruta -Recurse -Force }
        }
    }
    Write-Detalle 'tools\precache_paddle.py (descarga y prueba los modelos)'
    & $PythonExe (Join-Path $Root 'tools\precache_paddle.py')
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la precarga de modelos PaddleOCR.' }
    if (-not (Test-Modelos)) {
        throw 'La precarga termino pero faltan pesos en portable\paddlex.'
    }
    Write-Listo 'modelos listos en portable\paddlex'
}

function Install-Vlm {
    Write-Paso 'Modelos del verificador VLM'
    if ((Test-Vlm) -and -not $Force) {
        Write-Listo 'ya estan en portable\llama\models'
        return
    }
    Write-Detalle 'tools\precache_vlm.py (varios GB)'
    & $PythonExe (Join-Path $Root 'tools\precache_vlm.py')
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la descarga de los modelos VLM.' }
    $binario = Join-Path $LlamaDir 'bin\llama-server.exe'
    if (-not (Test-Path $binario)) {
        Write-Aviso ('falta portable\llama\bin\llama-server.exe: fije ' +
            'BITS_LLAMA_BIN_ZIP con la URL del release de llama.cpp o copielo a mano')
    }
    Write-Listo 'modelos VLM listos'
}

function Build-Launcher {
    Write-Paso 'Launcher LogbookClassification.exe'
    Write-Detalle 'instalando pyinstaller'
    & $PythonExe -m pip install --quiet --disable-pip-version-check `
        --no-warn-script-location pyinstaller
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo instalar PyInstaller.' }
    & $PythonExe (Join-Path $Root 'tools\build_launcher.py')
    if ($LASTEXITCODE -ne 0) { throw 'Fallo la construccion del launcher.' }
    Write-Listo 'launcher regenerado'
}

# --- Informe ---------------------------------------------------------------

function Show-Estado {
    $filas = @(
        [pscustomobject]@{
            Componente = "Python $PythonVersion"
            Estado     = if (Test-PythonPortable) { 'ok' } else { 'FALTA' }
            Ruta       = 'portable\python312'
            MB         = Get-TamanoMB $PythonDir
        },
        [pscustomobject]@{
            Componente = 'Dependencias'
            Estado     = if (Test-Dependencias) { 'ok' } else { 'FALTA' }
            Ruta       = 'requirements.txt'
            MB         = 0
        },
        [pscustomobject]@{
            Componente = 'Modelos PaddleOCR'
            Estado     = if (Test-Modelos) { 'ok' } else { 'FALTA' }
            Ruta       = 'portable\paddlex'
            MB         = Get-TamanoMB (Join-Path $Portable 'paddlex')
        },
        [pscustomobject]@{
            Componente = 'Tesseract'
            Estado     = if (Test-Path $TessExe) { 'ok' } else { 'opcional' }
            Ruta       = 'portable\tesseract'
            MB         = Get-TamanoMB $TessDir
        },
        [pscustomobject]@{
            Componente = 'Verificador VLM'
            Estado     = if (Test-Vlm) { 'ok' } else { 'opcional' }
            Ruta       = 'portable\llama'
            MB         = Get-TamanoMB $LlamaDir
        },
        [pscustomobject]@{
            Componente = 'Launcher'
            Estado     = if (Test-Path $LauncherExe) { 'ok' } else { 'FALTA' }
            Ruta       = 'LogbookClassification.exe'
            MB         = Get-TamanoMB $LauncherExe
        }
    )
    Write-Host ""
    $filas | Format-Table -AutoSize | Out-String | Write-Host
    $pdfs = @(Get-ChildItem -Path (Join-Path $Root 'input') -Filter '*.pdf' `
            -File -ErrorAction SilentlyContinue)
    if ($pdfs.Count -eq 0) {
        Write-Aviso 'input\ no tiene PDFs: los escaneos se copian a mano, no se descargan.'
    }
}

# --- Programa --------------------------------------------------------------

if ($env:OS -ne 'Windows_NT') {
    throw 'El entorno portable es de Windows: ejecute este script en Windows.'
}

Write-Host "Entorno portable de Logbook Classification" -ForegroundColor White
Write-Detalle "carpeta: $Root"

if ($Check) {
    Show-Estado
    return
}

Install-Python
Install-Dependencias
Install-Modelos
if ($SkipTesseract) {
    Write-Paso 'Tesseract'
    Write-Aviso 'omitido por -SkipTesseract (el OCR de respaldo quedara apagado)'
}
else {
    Install-Tesseract
}
if ($Vlm) { Install-Vlm }
if ($Launcher) { Build-Launcher }

if ($CleanCache -and (Test-Path $Cache)) {
    Remove-Item -LiteralPath $Cache -Recurse -Force
    Write-Detalle 'cache de instaladores borrada'
}

Show-Estado
Write-Host "Todo listo. Abra LogbookClassification.exe o ejecute:" -ForegroundColor Green
Write-Host "    portable\python312\tools\python.exe run_cli.py --pdf input\<archivo>.pdf"
